# news-bot // Minimalist Editorial Briefing Generator

A serverless, private news aggregator designed to compile a lean, text-centric daily news briefing. It is designed to match the print layouts of traditional publications (like *The Wall Street Journal* or *The Economist*), prioritizing information hierarchy, strict recency, and security.

## Key Design Principles

1. **Ultra-Lean Editorial Interface:** Built with classic serif typography, minimal borders, and zero heavy cards or "flashy" modern gradients.
2. **Prioritized Topic Hierarchy:** News is grouped by topic (e.g. *Ukraine War*, *Iran War*, *Markets*). Inside each topic, the foundational **Basis/Foundation Report** (like the daily ISW Assessment) is pinned to the very top, with supporting bullet-point updates from other news outlets listed underneath.
3. **Time-Prioritized, No Story Cap:** There is no fixed per-topic story limit. Every article that clears the recency filter is shown, ordered newest-first — so a topic that genuinely produced 20 fresh stories displays all 20. The only limiter is each source's own per-feed cap, which prevents a single outlet from dominating.
4. **Hard 7-Day Recency Filter:** Articles older than 7 days are discarded, and items with no parseable publication date are dropped rather than admitted — recency is enforced, not assumed.
5. **One-Click Story Triage:** Mark any story as *Read* to dismiss it — read stories are hidden by default and stay hidden across refreshes (the read list is remembered locally). A *Show Read* toggle in the toolbar brings them back whenever you want to review what you've cleared.
6. **Secure Local Email Newsletter Integration:** Fetch your email newsletters (like Ed Zitron's *Where's Your Ed At* or Matt Levine's *Money Stuff*) directly from your mail folder using secure IMAP. Mails are downloaded locally, parsed, and rendered as collapsible inline documents—ensuring 100% privacy and local-only credential protection.
7. **No-Config Public Fallbacks:** If email credentials are not set up, the script automatically falls back to scraping public RSS feeds (Substack) and Google News searches (Bloomberg opinion) to find these newsletters.

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
- **`max_days_old`**: Hard recency cutoff — auxiliary articles older than this (default 7 days), and any article without a parseable date, are dropped.
- **`max_items_per_feed`**: Default number of stories pulled from a single source per refresh (default 6). It is a *per-feed* fallback used when a feed omits its own `max_items` — **not** a per-topic cap. Topics have no fixed story limit; they are bounded only by recency.
- **`max_total_per_topic`** *(optional, advanced)*: A hidden per-topic safety ceiling (default 60) that only trims a topic if a malfunctioning feed floods it. It is intentionally **not** shown in the settings UI; edit it directly in `config.json` if you ever need to change it.

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
- [test_generate.py](test_generate.py) — Network-free behavioral tests for the recency filter and story-capping logic (`python test_generate.py`).
- [template.html](template.html) — Text-centric, paper-styled HTML layout.
- [refresh.bat](refresh.bat) — Double-click to refresh and open.
- [schedule.ps1](schedule.ps1) — Background Task Scheduler registry.
- `.env` — Local credential storage (gitignored).
- `digest.html` — The compiled output news briefing page (gitignored).
