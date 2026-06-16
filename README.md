# news-bot // Minimalist Editorial Briefing Generator

A serverless, private news aggregator designed to compile a lean, text-centric daily news briefing. It is designed to match the print layouts of traditional publications (like *The Wall Street Journal* or *The Economist*), prioritizing information hierarchy, strict recency, and security.

## Key Design Principles

1. **Ultra-Lean Editorial Interface:** Built with classic serif typography, minimal borders, and zero heavy cards or "flashy" modern gradients.
2. **Prioritized Topic Hierarchy:** News is grouped by topic (e.g. *Ukraine War*, *Iran War*, *Markets*). Inside each topic, the foundational **Basis/Foundation Report** (like the daily ISW Assessment) is pinned to the very top, with supporting bullet-point updates from other news outlets listed underneath.
3. **Strict 7-Day Recency Filter:** Automatically discards auxiliary news stories older than 7 days to keep your reading stream highly relevant.
4. **Secure Local Email Newsletter Integration:** Fetch your email newsletters (like Ed Zitron's *Where's Your Ed At* or Matt Levine's *Money Stuff*) directly from your mail folder using secure IMAP. Mails are downloaded locally, parsed, and rendered as collapsible inline documents—ensuring 100% privacy and local-only credential protection.
5. **No-Config Public Fallbacks:** If email credentials are not set up, the script automatically falls back to scraping public RSS feeds (Substack) and Google News searches (Bloomberg opinion) to find these newsletters.

---

## Getting Started

### 1. Setup Your Credentials (Optional)
If you want to pull newsletters directly from your email inbox:
1. Copy [.env.example](.env.example) to `.env` in this directory:
   ```powershell
   copy .env.example .env
   ```
2. Open `.env` and fill in your email details (IMAP server, email address, and an **App Password**).
3. (Recommended) Create a rule in your mail client to automatically move Zitron and Levine's emails to a subfolder (e.g., `Newsletters`) and set `EMAIL_FOLDER` to that folder name.

*If you do not configure `.env`, the script will automatically fallback to public feeds for Ed Zitron and Matt Levine.*

### 2. Configure Topics
Open [config.json](config.json) to edit your topics and feeds.
- **`topics`**: Defines headers. Can contain a `"basis"` configuration (which grabs the single latest report and pins it) and a list of auxiliary `"feeds"`.
- **`email_newsletters`**: Defines senders, subject keyword filters, and public RSS fallback urls.
- **`max_days_old`**: Maximum age of auxiliary articles to include (default is 7 days).

### 3. Run Manually
Double-click [refresh.bat](refresh.bat) (or run `python generate.py` in your terminal).
This will compile `digest.html` and launch it in your browser.

### 4. Background Scheduling (Windows Task Scheduler)
To silently refresh the page in the background every 4 hours:
1. Open PowerShell.
2. Run the scheduling script:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\schedule.ps1
   ```
You can bookmark the generated `digest.html` in your browser. It will update silently without browser popups when run in the background.

---

## File Structure

- [config.json](config.json) — Hierarchical topics and feed sources.
- [generate.py](generate.py) — Parser script with parallel RSS fetching, strict date filtering, and secure IMAP polling.
- [template.html](template.html) — Text-centric, paper-styled HTML layout.
- [refresh.bat](refresh.bat) — Double-click to refresh and open.
- [schedule.ps1](schedule.ps1) — Background Task Scheduler registry.
- `.env` — Local credential storage (gitignored).
- `digest.html` — The compiled output news briefing page (gitignored).
