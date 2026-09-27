# UnGenAI Lead Automation

Two scripts that automate the proven lead-sourcing method:

- **`lead_finder.py`** — searches for real businesses (via Claude + web search), mines
  their reviews for a genuine pain point, verifies a real email, and appends new leads
  to `leads.csv`. Rotates through `cities_verticals.json` so it never repeats the same
  search twice.
- **`draft_creator.py`** — reads `leads.csv`, writes a personalized email for every
  lead with a confirmed email, and creates it as a **Gmail draft** (never auto-sent —
  you still review and click Send yourself).

## 1. One-time setup

```bash
cd automation
pip install -r requirements.txt
```

Get an Anthropic API key from https://console.anthropic.com/ and set it:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```
(Add this line to your `~/.bashrc` or `~/.zshrc` so it persists across terminal sessions.)

### Gmail API setup (only needed for `draft_creator.py`)

1. Go to https://console.cloud.google.com/ → create a project (or reuse one).
2. Enable the **Gmail API** for that project (search "Gmail API" in the API library, click Enable).
3. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
   - Application type: **Desktop app**
4. Download the JSON, rename it to `credentials.json`, and put it in this same `automation/` folder.
5. Run `python draft_creator.py` once — it opens a browser window asking you to log into
   `theja4ravi@gmail.com` and approve access. This creates `token.json`, which is reused
   automatically after that (no repeated logins).

## 2. Running manually

```bash
# Find 2 new batches of leads (default)
python lead_finder.py

# Find leads for 5 combos in one run
python lead_finder.py --batches 5

# Create Gmail drafts for every new confirmed-email lead
python draft_creator.py

# Only draft the next 5, if you want to review in smaller chunks
python draft_creator.py --limit 5
```

## 3. Running automatically every day

### Option A — cron (Mac/Linux, if you leave a machine running)

```bash
crontab -e
```
Add this line to run both scripts every day at 9 AM IST:
```
0 9 * * * cd /path/to/automation && /usr/bin/python3 lead_finder.py --batches 2 && /usr/bin/python3 draft_creator.py >> run.log 2>&1
```

### Option B — GitHub Actions (runs in the cloud, no machine needed)

Create `.github/workflows/daily-leads.yml` in a repo containing this folder:

```yaml
name: Daily Lead Sourcing
on:
  schedule:
    - cron: '30 3 * * *'   # 9:00 AM IST daily
  workflow_dispatch: {}     # lets you also trigger it manually from GitHub's UI

jobs:
  find-leads:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r automation/requirements.txt
      - run: python automation/lead_finder.py --batches 2
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      - name: Commit updated leads.csv and state
        run: |
          git config user.name "lead-bot"
          git config user.email "lead-bot@ungenai.com"
          git add automation/leads.csv automation/state.json
          git commit -m "Daily lead batch" || echo "nothing to commit"
          git push
```

Notes on this option:
- Add `ANTHROPIC_API_KEY` as a GitHub repo secret (**Settings → Secrets and variables → Actions**).
- `draft_creator.py` needs a one-time interactive browser login for Gmail, so it's not a
  clean fit for a fully headless GitHub Action. Recommended split: let GitHub Actions
  handle `lead_finder.py` (fully automatic), and run `draft_creator.py` yourself locally
  every few days once `token.json` exists — after the first login it runs silently with
  no further prompts.

## 4. What's in each file

| File | Purpose |
|---|---|
| `lead_finder.py` | Finds and verifies leads, appends to `leads.csv` |
| `draft_creator.py` | Writes personalized emails, creates Gmail drafts |
| `cities_verticals.json` | The rotation list of city+vertical combos to search |
| `state.json` | Auto-generated — tracks which combos have been used |
| `leads.csv` | Auto-generated — your growing lead database |
| `drafted.json` | Auto-generated — tracks which leads already have a draft |
| `credentials.json` | You provide this — Gmail OAuth client (see setup above) |
| `token.json` | Auto-generated after first Gmail login — do not share this file |

## 5. Cost estimate

Each `lead_finder.py` batch (one city+vertical combo) typically costs a few cents in
Claude API usage — web search + reasoning for 3-6 leads. Running 2 batches daily is a
very light, low-cost habit. `draft_creator.py` is even cheaper per email since it's just
text generation.

## 6. Safety notes

- **Nothing is ever auto-sent.** Every email lands as a Gmail *draft* for you to review.
- The script **never invents contact details** — if Claude can't verify a real email, the
  lead is still saved (for phone/WhatsApp outreach) but skipped by `draft_creator.py`.
- `token.json` and `credentials.json` grant access to your Gmail — don't commit them to a
  public GitHub repo. Add both to `.gitignore`.
