#!/usr/bin/env python3
"""
UnGenAI Lead Finder — automates the proven lead-sourcing methodology using the
Claude API with server-side web search.

Run this daily (via cron or GitHub Actions) to source a fresh batch of leads
without manual searching. It rotates through a list of city+vertical
combinations, asks Claude to find real businesses, mine their reviews for a
genuine pain point, verify a real contact email, and classify each lead —
then appends everything to leads.csv (skipping combos already tried, and
skipping businesses already in the CSV).

Requires:
    pip install anthropic

Environment variable required:
    ANTHROPIC_API_KEY

Usage:
    python lead_finder.py                # runs one batch (2 combos)
    python lead_finder.py --batches 3    # runs 3 batches in one execution
"""

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
LEADS_CSV = HERE / "leads.csv"
CITIES_FILE = HERE / "cities_verticals.json"

CSV_FIELDS = [
    "date_found", "tier", "city", "business", "category", "phone", "email",
    "website", "contact_method", "hook", "pitch", "email_confirmed",
]

SYSTEM_PROMPT = """You are sourcing real, verified B2B leads for UnGenAI, a Bengaluru-based
agency offering:
1. AI Chat Agents — chatbots trained on a business's own content: FAQ answering, lead capture, human handoff.
2. Agentic AI — AI that takes real actions (books appointments, sends confirmations, checks status).
3. Full-Stack Website Design, Development & Deployment — for businesses with no website.

Follow this exact method:
1. Search for real businesses in the given city + vertical using web search.
2. For each promising candidate, read their actual customer reviews (Google/Maps/Facebook).
   Look for a genuine, specific complaint (e.g. "emails went unanswered", "no booking
   confirmation", "terrible WhatsApp service", "cumbersome intake process"). This becomes
   the personalization hook. If no complaint exists, use their scale/history/notable
   clients as the hook instead.
3. Verify a real contact email via the business's own official site, Facebook page, or a
   legitimate directory. NEVER invent or guess an email. If none can be found, say so and
   set email to null and contact_method to "phone" or "form".
4. Check whether they already have a website:
   - Has a website -> pitch = "AI Chat"
   - No website found anywhere -> pitch = "Full Website Build"
5. Classify tier:
   - "big_shot" if multi-branch / notable / large scale
   - "standard" otherwise

Return ONLY a JSON array (no markdown, no prose) of objects shaped exactly like this:
[
  {
    "business": "string",
    "category": "string",
    "phone": "string or null",
    "email": "string or null",
    "website": "string or null",
    "contact_method": "email | phone | form",
    "hook": "the specific real detail or complaint found, one sentence",
    "pitch": "AI Chat | Full Website Build",
    "tier": "standard | big_shot",
    "email_confirmed": true/false
  }
]

Return 3-6 leads per city+vertical combo given. Quality over quantity — skip a candidate
entirely rather than fabricate any detail about it."""


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def load_existing_businesses():
    """Return a set of business names already in leads.csv, to avoid duplicates."""
    if not LEADS_CSV.exists():
        return set()
    with open(LEADS_CSV, newline="", encoding="utf-8") as f:
        return {row["business"].strip().lower() for row in csv.DictReader(f)}


def append_leads(leads, city):
    write_header = not LEADS_CSV.exists()
    existing = load_existing_businesses()
    new_count = 0
    with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for lead in leads:
            name_key = lead.get("business", "").strip().lower()
            if not name_key or name_key in existing:
                continue
            writer.writerow({
                "date_found": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "tier": lead.get("tier", "standard"),
                "city": city,
                "business": lead.get("business", ""),
                "category": lead.get("category", ""),
                "phone": lead.get("phone") or "",
                "email": lead.get("email") or "",
                "website": lead.get("website") or "",
                "contact_method": lead.get("contact_method", ""),
                "hook": lead.get("hook", ""),
                "pitch": lead.get("pitch", ""),
                "email_confirmed": lead.get("email_confirmed", False),
            })
            existing.add(name_key)
            new_count += 1
    return new_count


def next_combo(state, all_combos):
    used = set(state.get("used_combos", []))
    for combo in all_combos:
        key = f"{combo['city']}|{combo['vertical']}"
        if key not in used:
            return combo, key
    # all used — reset rotation so it can run indefinitely
    state["used_combos"] = []
    return all_combos[0], f"{all_combos[0]['city']}|{all_combos[0]['vertical']}"


def run_one_batch(client, state, all_combos):
    combo, key = next_combo(state, all_combos)
    city, vertical = combo["city"], combo["vertical"]
    print(f"[{datetime.now().isoformat()}] Searching: {vertical} in {city}")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{
            "role": "user",
            "content": f"Find leads for: {vertical} in {city}. Follow the method exactly.",
        }],
    )

    # Collect all text blocks (Claude may interleave search + text)
    text_parts = [b.text for b in response.content if b.type == "text"]
    raw = "\n".join(text_parts).strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        leads = json.loads(raw)
    except json.JSONDecodeError:
        print("  ! Could not parse JSON response, skipping this batch.")
        print("  Raw output was:", raw[:500])
        leads = []

    added = append_leads(leads, city)
    print(f"  -> {len(leads)} leads returned, {added} new (deduplicated) added to leads.csv")

    state.setdefault("used_combos", []).append(key)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    return added


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batches", type=int, default=2,
                         help="How many city+vertical combos to run this execution")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: set the ANTHROPIC_API_KEY environment variable first.")

    client = anthropic.Anthropic(api_key=api_key)
    state = load_json(STATE_FILE, {"used_combos": []})
    all_combos = load_json(CITIES_FILE, [])

    if not all_combos:
        sys.exit(f"ERROR: {CITIES_FILE} is missing or empty. See cities_verticals.json.")

    total_added = 0
    for _ in range(args.batches):
        total_added += run_one_batch(client, state, all_combos)
        save_json(STATE_FILE, state)  # save progress after every batch

    print(f"\nDone. {total_added} new leads added this run. See leads.csv")


if __name__ == "__main__":
    main()
