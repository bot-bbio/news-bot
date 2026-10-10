# news-bot // Minimalist Editorial Briefing Generator

A serverless, private news aggregator designed to compile a lean, text-centric daily news briefing. It is designed to match the print layouts of traditional publications (like *The Wall Street Journal* or *The Economist*), prioritizing information hierarchy, strict recency, and security.

## Key Design Principles

1. **Ultra-Lean Editorial Interface:** Built with classic serif typography, minimal borders, and zero heavy cards or "flashy" modern gradients.
2. **Prioritized Topic Hierarchy:** News is grouped by topic (e.g. *Ukraine War*, *Iran War*, *Markets*). Inside each topic, the foundational **Basis/Foundation Report** (like the daily ISW Assessment) is pinned to the very top, with supporting bullet-point updates from other news outlets listed underneath.
3. **Time-Prioritized, No Story Cap:** There is no fixed per-topic story limit. Every article that clears the recency filter is shown, ordered newest-first — so a topic that genuinely produced 20 fresh stories displays all 20. The only limiter is each source's own per-feed cap, which prevents a single outlet from dominating.
4. **Hard 7-Day Recency Filter:** Articles older than 7 days are discarded, and items with no parseable publication date are dropped rather than admitted — recency is enforced, not assumed.
5. **One-Click Story Triage:** Mark any story as *Read* to dismiss it — read stories are hidden by default and stay hidden across refreshes (the read list is remembered locally). A *Show Read* toggle in the toolbar brings them back whenever you want to review what you've cleared.
6. **Newsletters as RSS Feeds:** Editorial newsletters (e.g. Ed Zitron's *Where's Your Ed At*, Matt Levine's *Money Stuff*) are pulled from their public RSS feeds and collected into a dedicated *Editorials & Newsletters* section. No mail credentials are required.
7. **Installable PWA, Auto-Refreshed:** The briefing can deploy itself as a private, offline-capable Progressive Web App and refresh on a schedule (see *Deploy as a Hosted PWA*), so you just open the home-screen app and read the latest render.

---

## Getting Started

### 1. Configure Topics
Open [config.json](config.json) to edit your topics and feeds.
- **`topics`**: Defines headers. Can contain a `"basis"` configuration (which grabs the single latest report and pins it) and a list of auxiliary `"feeds"`.
- **`email_newsletters`**: RSS sources for the *Editorials & Newsletters* section. Each entry's `feed_name` is the display label and `fallback_url` is the RSS feed to pull. *(The legacy `sender`/`subject_matches` fields are ignored.)*
- **`max_days_old`**: Hard recency cutoff — auxiliary articles older than this (default 7 days), and any article without a parseable date, are dropped.
- **`max_items_per_feed`**: Default number of stories pulled from a single source per refresh (default 6). A *per-feed* fallback used when a feed omits its own `max_items` — **not** a per-topic cap. Topics have no fixed story limit; they are bounded only by recency.
- **Per-feed `recency`** *(optional)*: tighten a single feed's window below the global `max_days_old`, e.g. `"recency": "1d"` (also accepts `12h`, `30m`, `2w`, or a bare number of days). Ideal for noisy wire feeds you want capped to the last day while slower long-form feeds keep the 7-day window. It's enforced as a hard date cutoff for every feed type; for Google News feeds it *additionally* injects the `when:` operator so Google returns fresh, recency-ordered results.
- **`max_total_per_topic`** *(optional, advanced)*: A hidden per-topic safety ceiling (default 60) that only trims a topic if a malfunctioning feed floods it. Not shown in the settings UI; edit it directly in `config.json`.

### 2. Run Locally (authoring & preview)
- Double-click [refresh.bat](refresh.bat) — runs the local server (`python generate.py --serve`) and opens the interactive digest with the full **Briefing Settings** drawer for editing topics, feeds, and newsletters.
- Or run `python generate.py` to compile `digest.html` once and open it in your browser.
- Run the tests with `python test_generate.py`.

### 3. Deploy as a Hosted PWA (twice-daily auto-refresh)
The briefing can run itself: a GitHub Actions cron renders each user's config and deploys an installable, offline-capable PWA to Cloudflare Pages at ~6am and ~5pm America/New_York. Open the home-screen app on your schedule and it shows the latest render — no email, no notifications.

**Render a hosted build by hand (to preview or deploy manually):**
```powershell
python generate.py --mode=hosted --config config.json --out dist
```
This writes `dist/index.html` (read + triage UI, no settings drawer) plus `manifest.json`, `sw.js`, and `icon.svg`.

**One-time setup for the automated flow:**
1. **Cloudflare Pages:** create one Pages project per user (e.g. `newsbot-user1`). Add a [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/) policy limiting each project to that user's email (use a ~30-day session so the installed PWA rarely re-prompts). Add a custom domain if you like.
2. **Host each config privately** (a private repo raw URL or a secret gist) so the workflow can fetch it.
3. **Add repository secrets** (Settings → Secrets and variables → Actions):
   - `CLOUDFLARE_API_TOKEN` (scope: *Cloudflare Pages: Edit*) and `CLOUDFLARE_ACCOUNT_ID`
   - `CONFIG_FETCH_TOKEN` *(optional)* — bearer token for private config URLs
   - `USER1_CONFIG_URL`, `USER1_PAGES_PROJECT`, `USER2_CONFIG_URL`, `USER2_PAGES_PROJECT`
4. The schedule lives in [.github/workflows/refresh.yml](.github/workflows/refresh.yml). Trigger it from the **Actions** tab to test (manual runs bypass the time gate).
5. On each phone, open the user's Pages URL once and **Add to Home Screen** to install the PWA.
6. **Keep the cron alive:** GitHub disables scheduled workflows after 60 days with no commits (state `disabled_inactivity`). Run `.\keepalive.ps1 -Install` once; a weekly (Fridays, 9 AM) Task Scheduler job then re-enables any workflow GitHub switched off. Requires `gh` to be authenticated. Run `.\keepalive.ps1` any time to check by hand.

---

## File Structure

- [config.json](config.json) — Hierarchical topics and feed sources.
- [generate.py](generate.py) — Parser with parallel RSS fetching, hard recency filtering, and two render modes: `local` (interactive `digest.html`) and `hosted` (a deploy-ready PWA folder).
- [test_generate.py](test_generate.py) — Network-free behavioral tests for the recency/cap logic and the local/hosted render (`python test_generate.py`).
- [template.html](template.html) — Text-centric, paper-styled HTML layout (the settings drawer is stripped from hosted builds).
- [.github/workflows/refresh.yml](.github/workflows/refresh.yml) — Bi-daily GitHub Actions cron that renders and deploys each user's PWA to Cloudflare Pages.
- [refresh.bat](refresh.bat) — Double-click to run the local server and open the digest.
- [keepalive.ps1](keepalive.ps1) — Re-enables workflows GitHub disabled for inactivity; `-Install` registers it as a weekly Windows scheduled task.
- [schedule.ps1](schedule.ps1) — *(optional)* Windows Task Scheduler registration for a local background refresh.
- `dist/` — Hosted build output, one folder per user (gitignored).
- `digest.html` — The compiled local briefing page (gitignored).
