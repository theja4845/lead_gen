#!/usr/bin/env python3
"""
UnGenAI Draft Creator — reads leads.csv, writes a personalized outreach email
for every lead that has a confirmed email and hasn't been drafted yet, and
creates it as a Gmail DRAFT (never auto-sent — you still review and hit send
yourself in Gmail).

Requires:
    pip install anthropic google-api-python-client google-auth-httplib2 google-auth-oauthlib

One-time Gmail API setup (takes ~5 minutes):
    1. Go to https://console.cloud.google.com/ -> create a project (or reuse one).
    2. Enable the "Gmail API" for that project.
    3. Go to "APIs & Services" -> "Credentials" -> "Create Credentials" ->
       "OAuth client ID" -> Application type: "Desktop app".
    4. Download the JSON and save it as credentials.json in this same folder.
    5. Run this script once: it will open a browser window asking you to log
       into the Gmail account (theja4ravi@gmail.com) and approve access.
       This creates token.json, which is reused automatically after that —
       no repeated logins needed.

Environment variable required:
    ANTHROPIC_API_KEY

Usage:
    python draft_creator.py                # drafts every un-drafted, email-confirmed lead
    python draft_creator.py --limit 10      # only draft the next 10
"""

import argparse
import base64
import csv
import json
import os
import sys
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

HERE = Path(__file__).parent
LEADS_CSV = HERE / "leads.csv"
DRAFTED_STATE = HERE / "drafted.json"
CREDENTIALS_FILE = HERE / "credentials.json"
TOKEN_FILE = HERE / "token.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

# --- your UnGenAI signature — edit here if it ever changes ---
SIGNATURE_HTML = """
<p>Best,<br>
Ravi<br>
UnGenAI — AI Agents + Websites<br>
<a href="https://ungenai.com" style="color:#3a1fff;text-decoration:none;">ungenai.com</a> |
<a href="mailto:ravi@ungenai.com" style="color:#3a1fff;text-decoration:none;">ravi@ungenai.com</a> |
+91 95733 58709
</p>
"""
SIGNATURE_PLAIN = """
Best,
Ravi
UnGenAI — AI Agents + Websites
ungenai.com | ravi@ungenai.com | +91 95733 58709
"""

EMAIL_SYSTEM_PROMPT = """You write short, personalized cold outreach emails for UnGenAI, a
Bengaluru-based agency offering AI chat agents, agentic AI, and full-stack website builds.

Rules:
- Open by referencing the specific detail given (the "hook") — a real fact or complaint
  about the business, never generic.
- One line introducing UnGenAI, tied to their specific need.
- One or two sentences on what the AI chat/agent (or website, if pitch is "Full Website
  Build") would concretely do for THEM.
- Soft call to action — offer a short call or a quick demo. Never pushy.
- Do NOT include a signature or sign-off — that is added separately.
- Do NOT include a subject line in the body.
- If the business is not in an English-speaking country, write the email in the
  primary local language instead of English.
- Keep it under 120 words.

Return ONLY a JSON object: {"subject": "...", "body": "...", "language_note": "..."}
body should be plain text with \\n\\n between paragraphs, no HTML, no signature.
language_note should say what language you wrote in and why, or "English" if default.
"""


def get_gmail_service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                sys.exit(
                    "ERROR: credentials.json not found. See the setup instructions "
                    "at the top of this file."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def load_drafted_state():
    if DRAFTED_STATE.exists():
        return set(json.loads(DRAFTED_STATE.read_text()))
    return set()


def save_drafted_state(drafted):
    DRAFTED_STATE.write_text(json.dumps(sorted(drafted)))


def generate_email(client, lead):
    prompt = (
        f"Business: {lead['business']}\n"
        f"Category: {lead['category']}\n"
        f"City: {lead['city']}\n"
        f"Pitch type: {lead['pitch']}\n"
        f"Hook (specific detail/complaint to reference): {lead['hook']}\n"
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=EMAIL_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip().replace("```json", "").replace("```", "")
    return json.loads(raw)


def create_gmail_draft(service, to_email, subject, body_plain):
    body_html = (
        f'<div style="font-family: Arial, sans-serif; font-size: 14px; color: #222;">'
        + "".join(f"<p>{p}</p>" for p in body_plain.split("\n\n"))
        + SIGNATURE_HTML
        + "</div>"
    )
    full_plain = body_plain + "\n" + SIGNATURE_PLAIN

    message = MIMEText(body_html, "html")
    message["to"] = to_email
    message["subject"] = subject
    # Note: MIMEText only carries one body; Gmail API's simple path sends HTML.
    # Gmail renders this fine and still shows a sensible plain-text fallback.
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    draft = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    return draft["id"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Max number of new drafts to create this run")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("ERROR: set the ANTHROPIC_API_KEY environment variable first.")
    if not LEADS_CSV.exists():
        sys.exit(f"ERROR: {LEADS_CSV} not found. Run lead_finder.py first.")

    client = anthropic.Anthropic(api_key=api_key)
    gmail = get_gmail_service()
    drafted = load_drafted_state()

    with open(LEADS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    made = 0
    for row in rows:
        key = row["business"].strip().lower()
        if key in drafted:
            continue
        if not row.get("email"):
            continue  # no confirmed email — nothing to draft
        if args.limit and made >= args.limit:
            break

        print(f"Drafting: {row['business']} ({row['email']})")
        try:
            email_content = generate_email(client, row)
            draft_id = create_gmail_draft(
                gmail, row["email"], email_content["subject"], email_content["body"]
            )
            print(f"  -> draft created ({email_content.get('language_note', 'English')}), id={draft_id}")
            drafted.add(key)
            made += 1
        except Exception as e:
            print(f"  ! failed: {e}")

        save_drafted_state(drafted)  # save progress after every draft

    print(f"\nDone. {made} new Gmail drafts created. Review and send from your Drafts folder.")


if __name__ == "__main__":
    main()
