import os
import re
import sys
import json
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import webbrowser
import hashlib
import concurrent.futures
import html
from email.utils import parsedate_to_datetime
import http.server
import socketserver
import urllib.parse


# Base directories
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
TEMPLATE_PATH = os.path.join(PROJECT_DIR, "template.html")
OUTPUT_PATH = os.path.join(PROJECT_DIR, "digest.html")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --- PWA assets emitted by the hosted build -------------------------------------
# Injected into the page <head> in hosted mode so the digest is installable to the
# home screen and works offline.
PWA_HEAD_TAGS = """  <link rel="manifest" href="manifest.json">
  <meta name="theme-color" content="#fdfbf7" media="(prefers-color-scheme: light)">
  <meta name="theme-color" content="#141414" media="(prefers-color-scheme: dark)">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="news-bot">
  <link rel="apple-touch-icon" href="icon.svg">
  <link rel="icon" type="image/svg+xml" href="icon.svg">"""

# Injected before </body> in hosted mode to register the service worker.
PWA_FOOT_TAGS = """  <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function () {
        navigator.serviceWorker.register('sw.js').catch(function (e) {
          console.warn('Service worker registration failed:', e);
        });
      });
    }
  </script>"""

PWA_MANIFEST_JSON = """{
  "name": "news-bot daily briefing",
  "short_name": "news-bot",
  "description": "Your clean daily briefing digest",
  "start_url": ".",
  "scope": ".",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#fdfbf7",
  "theme_color": "#fdfbf7",
  "icons": [
    { "src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable" }
  ]
}
"""

PWA_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#fdfbf7"/>
  <text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle" font-family="Georgia, 'Times New Roman', Times, serif" font-size="340" font-weight="700" fill="#991b1b">n</text>
</svg>
"""

# Network-first so the latest twice-daily render always wins; cache is only a
# fallback for offline reading. (Note: if a Cloudflare Access session has expired,
# the document fetch may return the login page — a re-open re-authenticates.)
PWA_SERVICE_WORKER = """const CACHE = 'news-bot-v1';

self.addEventListener('install', function (event) {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var req = event.request;
  if (req.method !== 'GET') return;

  var accept = req.headers.get('accept') || '';
  var isDocument = req.mode === 'navigate' || accept.indexOf('text/html') !== -1;

  if (isDocument) {
    event.respondWith(
      fetch(req).then(function (res) {
        var copy = res.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copy); });
        return res;
      }).catch(function () {
        return caches.match(req).then(function (r) { return r || caches.match('./'); });
      })
    );
  } else {
    event.respondWith(
      caches.match(req).then(function (cached) {
        return cached || fetch(req).then(function (res) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
          return res;
        });
      })
    );
  }
});
"""


def load_config_source(source):
    """Load raw config JSON text from a local path or an http(s) URL.

    Remote URLs may be private; set the CONFIG_FETCH_TOKEN environment variable to
    send an 'Authorization: Bearer <token>' header (e.g. a token-gated gist/repo).
    Returns the raw text, or None if it could not be loaded."""
    if source and (source.startswith("http://") or source.startswith("https://")):
        headers = {"User-Agent": USER_AGENT}
        token = os.environ.get("CONFIG_FETCH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(source, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read().decode("utf-8")
        except Exception as e:
            print(f"  Error fetching config from {source}: {e}")
            return None
    path = source or CONFIG_PATH
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

PAYWALLED_DOMAINS = [
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "nytimes.com",
    "economist.com",
    "barrons.com",
    "thetimes.co.uk",
    "foreignpolicy.com",
    "newstatesman.com"
]

def check_paywall(url, source_name=""):
    """Check if the article link domain or source name belongs to a known paywalled publisher."""
    if source_name:
        s_lower = source_name.lower()
        paywall_sources = ["bloomberg", "financial times", "wall street journal", "wsj", "nytimes", "new york times", "economist", "barrons", "the times"]
        for ps in paywall_sources:
            if ps in s_lower:
                return True
    if not url or url == "#" or url.startswith("mailto:"):
        return False
    domain = re.sub(r'^https?://(www\.)?', '', url).split('/')[0].lower()
    for p in PAYWALLED_DOMAINS:
        if p in domain:
            return True
    return False

def clean_html(raw_html):
    """Strip HTML tags and unescape entities for simple inline previews."""
    if not raw_html:
        return ""
    # Remove script, style, and head content
    raw_html = re.sub(r'<head>.*?</head>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    raw_html = re.sub(r'<script.*?>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    raw_html = re.sub(r'<style.*?>.*?</style>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = html.unescape(clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
    return clean_text

def clean_title(title, source_name):
    """Clean trailing source tags from headlines."""
    if not title:
        return ""
    title = html.unescape(title)
    suffixes = [
        rf"\s*-\s*{re.escape(source_name)}$",
        rf"\s*\|\s*{re.escape(source_name)}$",
        r"\s*-\s*Reuters$",
        r"\s*-\s*Bloomberg$",
        r"\s*-\s*FT$",
        r"\s*-\s*CNBC$",
        r"\s*-\s*Hacker News$"
    ]
    for suffix in suffixes:
        title = re.sub(suffix, "", title, flags=re.IGNORECASE)
    return title.strip()

def parse_rfc822_date(date_str):
    """Parse dates from RSS feeds and emails into timezone-aware datetimes.

    Tries the standard-library RFC 822 parser first (handles the vast majority of
    real-world RSS/email date headers), then ISO 8601, then a few manual fallbacks.
    Returns None only when no strategy can extract a date. Naive datetimes are
    treated as UTC so downstream recency comparisons never raise."""
    if not date_str:
        return None

    date_str = date_str.strip()

    # 1. Robust RFC 822 parser (e.g. "Mon, 16 Jun 2026 14:30:00 +0000" / "GMT")
    try:
        dt = parsedate_to_datetime(date_str)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except (TypeError, ValueError, IndexError, OverflowError):
        pass

    # 2. ISO 8601 (e.g. "2026-06-16T14:30:00Z" / "...+00:00" / with fractional seconds)
    iso_candidate = date_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # 3. Manual fallbacks for non-standard feeds
    clean_date = re.sub(r'^[A-Za-z]{3},\s*', '', date_str)
    formats = [
        "%d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S",
        "%d %b %Y %H:%M %z",
        "%d %b %Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            if fmt.endswith("%Z") and (clean_date.endswith("GMT") or clean_date.endswith("UTC")):
                clean_date_tz = clean_date.replace("GMT", "+0000").replace("UTC", "+0000")
                return datetime.strptime(clean_date_tz, fmt.replace("%Z", "%z")).replace(tzinfo=timezone.utc)

            dt = datetime.strptime(clean_date, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None

def get_relative_time(dt):
    if not dt:
        return "Unknown date"
    now = datetime.now(timezone.utc)
    diff = now - dt
    seconds = diff.total_seconds()
    if seconds < 0:
        return "Just now"
    minutes = int(seconds / 60)
    if minutes < 1:
        return "Just now"
    elif minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes / 60)
    if hours < 24:
        return f"{hours}h ago"
    days = int(hours / 24)
    if days == 1:
        return "Yesterday"
    return f"{days}d ago"

def fetch_feed_xml(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read()
    except Exception as e:
        print(f"  Error fetching feed {url}: {e}")
        return None

def parse_rss_xml(xml_bytes, source_name, max_items, max_days_old=7):
    if not xml_bytes:
        return []

    # Some feeds emit a UTF-8 BOM or blank lines before the XML declaration, which
    # makes the strict parser raise "declaration not at start of entity". Trim any
    # leading whitespace/BOM so these otherwise-valid feeds still parse.
    xml_bytes = xml_bytes.lstrip()
    if xml_bytes.startswith(b'\xef\xbb\xbf'):
        xml_bytes = xml_bytes[3:].lstrip()

    articles = []
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_days_old)

    try:
        root = ET.fromstring(xml_bytes)
        channel = root.find("channel")
        items = root.findall(".//item") if channel is not None else root.findall(".//entry")
        
        for item in items:
            if len(articles) >= max_items:
                break
                
            title_elem = item.find("title")
            link_elem = item.find("link")
            
            if link_elem is not None and link_elem.get("href"):
                link = link_elem.get("href")
            else:
                link = link_elem.text if link_elem is not None else ""
                
            title = title_elem.text if title_elem is not None else ""
            
            if not title or not link:
                continue
                
            pub_date_elem = item.find("pubDate")
            if pub_date_elem is None:
                pub_date_elem = item.find("published")
            if pub_date_elem is None:
                pub_date_elem = item.find("updated")
                
            pub_date_raw = pub_date_elem.text if pub_date_elem is not None else ""
            pub_datetime = parse_rfc822_date(pub_date_raw)

            # Hard recency filter: an item must carry a parseable date inside the
            # window. Undated items cannot be proven recent, so they are dropped
            # rather than silently admitted.
            if pub_datetime is None or pub_datetime < cutoff_date:
                continue
                
            desc_elem = item.find("description")
            if desc_elem is None:
                desc_elem = item.find("summary")
            if desc_elem is None:
                desc_elem = item.find("content")
                
            desc_raw = desc_elem.text if desc_elem is not None else ""
            desc = clean_html(desc_raw)
            
            actual_source = source_name
            source_tag_elem = item.find("source")
            if source_tag_elem is not None and source_tag_elem.text:
                actual_source = source_tag_elem.text.strip()
                
            title = clean_title(title, actual_source)
            article_id = hashlib.md5(link.encode('utf-8')).hexdigest()
            is_paywall = check_paywall(link, actual_source)
            
            articles.append({
                "id": article_id,
                "title": title,
                "link": link,
                "description": desc[:250] + "..." if len(desc) > 250 else desc,
                "datetime": pub_datetime,
                "relative_time": get_relative_time(pub_datetime),
                "source": actual_source,
                "paywall": is_paywall
            })
    except Exception as e:
        print(f"  Error parsing XML for {source_name}: {e}")
    return articles

def fetch_and_process_topic(topic_name, topic_config, default_feed_cap, max_days_old, safety_ceiling):
    """Concurrently download feeds for a topic, processing Basis vs Auxiliary news.

    There is no everyday per-topic story cap: every item that clears the hard recency
    filter is kept and the list is ordered newest-first, so a topic that genuinely
    produced 20 fresh stories shows all 20. The only routine limiter is each feed's own
    ``max_items`` (falling back to ``default_feed_cap``), which prevents a single
    noisy source from dominating the topic.

    ``safety_ceiling`` is a hidden guardrail (not exposed in the UI) that trims the
    final list only in pathological cases where a feed floods a topic."""
    topic_data = {
        "name": topic_name,
        "basis": None,
        "auxiliary": []
    }
    
    # 1. Fetch Basis document if configured
    if "basis" in topic_config:
        basis_conf = topic_config["basis"]
        print(f"  Fetching Basis for '{topic_name}': {basis_conf['name']}...")
        xml_data = fetch_feed_xml(basis_conf["url"])
        basis_items = parse_rss_xml(xml_data, basis_conf["name"], max_items=2, max_days_old=max_days_old)
        if basis_items:
            # Pin the single latest article as the basis
            topic_data["basis"] = basis_items[0]
            
    # 2. Fetch auxiliary feeds
    feeds = topic_config.get("feeds", [])
    if feeds:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(feeds)) as executor:
            future_to_feed = {
                executor.submit(fetch_feed_xml, feed["url"]): feed for feed in feeds
            }
            for future in concurrent.futures.as_completed(future_to_feed):
                feed = future_to_feed[future]
                try:
                    xml_data = future.result()
                    # Feed-specific cap to prevent a single source dominating the
                    # topic; falls back to the global default when unspecified.
                    feed_max = int(feed.get("max_items", default_feed_cap))
                    items = parse_rss_xml(xml_data, feed["name"], feed_max, max_days_old)
                    topic_data["auxiliary"].extend(items)
                except Exception as e:
                    print(f"  Error fetching {feed['name']} auxiliary: {e}")
                    
        # Order auxiliary articles newest-first (time-prioritized).
        topic_data["auxiliary"] = [a for a in topic_data["auxiliary"] if a["datetime"] is not None]
        topic_data["auxiliary"].sort(key=lambda x: x["datetime"], reverse=True)
        # De-duplicate articles by link. No global per-topic truncation is applied:
        # recency is the only gate, so all fresh stories survive.
        seen_links = set()
        unique_aux = []
        for a in topic_data["auxiliary"]:
            if a["link"] not in seen_links:
                seen_links.add(a["link"])
                unique_aux.append(a)
        # Hidden safety ceiling: guardrail against a runaway feed, not an everyday cap.
        topic_data["auxiliary"] = unique_aux[:safety_ceiling]

    return topic_data

def render_basis_html(basis):
    if not basis:
        return ""
    paywall_tag = ' <span class="paywall-label">• 🔒 Paywall</span>' if basis.get("paywall") else ''
    return f"""
      <div class="basis-doc" data-article-id="{basis["id"]}">
        <span class="basis-label">Mandated Priority Basis — {basis["source"]}</span>
        <div class="basis-meta">
          <span class="meta-source">{basis["source"]}</span>
          <span class="meta-separator">•</span>
          <span>{basis["relative_time"]}</span>
          {paywall_tag}
          <span class="meta-separator">•</span>
          <button class="action-link" onclick="toggleRead('{basis["id"]}')">Mark Read</button>
        </div>
        <h3 class="basis-title">
          <a href="{basis["link"]}" target="_blank" rel="noopener noreferrer">{basis["title"]}</a>
        </h3>
        <p class="basis-desc">{basis["description"]}</p>
      </div>"""

def render_auxiliary_html(items):
    if not items:
        return '<p class="empty-state">No recent auxiliary reports in the last 7 days.</p>'
        
    html_out = '<div class="auxiliary-header">Recent Developments</div>'
    html_out += '<ul class="aux-list">'
    
    for art in items:
        link_target = f'href="{art["link"]}" target="_blank" rel="noopener noreferrer"'
        paywall_tag = ' <span class="paywall-label">• 🔒 Paywall</span>' if art.get("paywall") else ''

        html_out += f"""
        <li class="aux-item" data-article-id="{art["id"]}">
          <div class="aux-meta-row">
            <span class="meta-source">{art["source"]}</span>
            <span class="meta-separator">•</span>
            <span>{art["relative_time"]}</span>
            {paywall_tag}
            <span class="meta-separator">•</span>
            <button class="action-link" onclick="toggleRead('{art["id"]}')">Mark Read</button>
          </div>
          <div class="aux-title-row">
            <a {link_target}>{art["title"]}</a>
          </div>
          <p class="aux-snippet">{art["description"]}</p>
        </li>"""
        
    html_out += '</ul>'
    return html_out

def compile_digest(silent=False, mode="local", config_source=None, out_path=None):
    """Compile the briefing.

    mode="local"  -> full interactive page with the settings drawer, written to a
                     single HTML file (digest.html by default).
    mode="hosted" -> read+triage page (no settings drawer / no /save calls) plus PWA
                     assets (manifest.json, sw.js, icon.svg), written into an output
                     directory ready to deploy to a static host.

    config_source is a local path or an http(s) URL; out_path is a file for local
    mode or an output directory for hosted mode."""
    if not silent:
        print("Initializing news-bot clean rebuild...")

    # 1. Load config (local path or remote URL)
    config_raw_str = load_config_source(config_source)
    if config_raw_str is None:
        print(f"Error: Could not load config from {config_source or CONFIG_PATH}.")
        return False
    config = json.loads(config_raw_str)
        
    # Default number of stories pulled from a single source per refresh. This is a
    # per-feed fallback (used when a feed omits its own "max_items"), not a per-topic
    # ceiling — topics are bounded by recency, not by a fixed story count.
    default_feed_cap = config.get("max_items_per_feed", 6)
    max_days_old = config.get("max_days_old", 7)
    # Hidden per-topic safety ceiling. Deliberately not surfaced in the settings UI;
    # it only exists to stop a malfunctioning feed from flooding a single topic.
    # Tunable for power users via "max_total_per_topic" in config.json.
    safety_ceiling = config.get("max_total_per_topic", 60)
    
    # 2. Newsletters as RSS feeds (the "Editorials & Newsletters" section).
    #    Each configured newsletter is pulled purely from its RSS feed URL.
    newsletter_configs = config.get("email_newsletters", [])
    newsletter_tasks = [
        {"name": nc["feed_name"], "url": nc["fallback_url"]}
        for nc in newsletter_configs
        if nc.get("fallback_url")
    ]

    all_newsletters = []
    if newsletter_tasks:
        if not silent:
            print(f"Queueing {len(newsletter_tasks)} newsletter RSS feed(s)...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(newsletter_tasks)) as executor:
            futures = {
                executor.submit(fetch_feed_xml, t["url"]): t for t in newsletter_tasks
            }
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    xml_data = future.result()
                    items = parse_rss_xml(xml_data, task["name"], default_feed_cap, max_days_old)
                    all_newsletters.extend(items)
                except Exception as e:
                    print(f"  Error fetching newsletter feed {task['name']}: {e}")

    # De-duplicate by link and order newest-first (undated items are already dropped).
    seen_nl = set()
    deduped_newsletters = []
    for a in sorted(all_newsletters, key=lambda x: x["datetime"], reverse=True):
        if a["link"] not in seen_nl:
            seen_nl.add(a["link"])
            deduped_newsletters.append(a)
    all_newsletters = deduped_newsletters

    # 5. Fetch Topic Feeds (Parallel Execution)
    topics_html_output = []
    topics = config.get("topics", {})
    
    if not silent:
        print(f"Queueing {len(topics)} topic sections...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(topics))) as executor:
        future_to_topic = {
            executor.submit(fetch_and_process_topic, name, conf, default_feed_cap, max_days_old, safety_ceiling): name
            for name, conf in topics.items()
            if conf.get("enabled", True)
        }
        
        # Compile each topic section's HTML as it finishes
        for future in concurrent.futures.as_completed(future_to_topic):
            topic_name = future_to_topic[future]
            try:
                topic_data = future.result()
                
                # Render HTML
                basis_html = render_basis_html(topic_data["basis"])
                aux_html = render_auxiliary_html(topic_data["auxiliary"])
                
                # Calculate total count
                total_stories = (1 if topic_data["basis"] else 0) + len(topic_data["auxiliary"])
                
                topic_html = f"""
                <section class="topic-section" data-topic-name="{topic_name}">
                  <details class="topic-details">
                    <summary class="topic-summary">
                      <span class="topic-title-wrapper"><span class="topic-title">{topic_name}</span></span>
                      <span style="display:inline-flex; align-items:center;">
                        <span class="topic-count">{total_stories} stories</span>
                        <span class="reorder-controls">
                          <button class="reorder-btn" onclick="moveTopic(event, '{topic_name}', 'up')" title="Move category up">▲</button>
                          <button class="reorder-btn" onclick="moveTopic(event, '{topic_name}', 'down')" title="Move category down">▼</button>
                        </span>
                      </span>
                    </summary>
                    <div class="topic-content" style="margin-top: 1rem;">
                      {basis_html}
                      {aux_html}
                    </div>
                  </details>
                </section>"""
                
                topics_html_output.append((topic_name, topic_html, total_stories))
                if not silent:
                    print(f"  Completed topic '{topic_name}' ({total_stories} stories)")
            except Exception as e:
                print(f"  Error building topic '{topic_name}': {e}")
                
    # Sort topics output by config order to preserve vertical priority
    topic_order = list(topics.keys())
    topics_html_output.sort(key=lambda x: topic_order.index(x[0]) if x[0] in topic_order else 999)
    
    # 6. Append Editorials & Newsletters section if we have content
    if all_newsletters:
        newsletter_html = render_auxiliary_html(all_newsletters)
        newsletter_section_html = f"""
        <section class="topic-section" data-topic-name="Editorials & Newsletters">
          <details class="topic-details">
            <summary class="topic-summary">
              <span class="topic-title-wrapper"><span class="topic-title">Editorials & Newsletters</span></span>
              <span style="display:inline-flex; align-items:center;">
                <span class="topic-count">{len(all_newsletters)} stories</span>
                <span class="reorder-controls">
                  <button class="reorder-btn" onclick="moveTopic(event, 'Editorials & Newsletters', 'up')" title="Move category up">▲</button>
                  <button class="reorder-btn" onclick="moveTopic(event, 'Editorials & Newsletters', 'down')" title="Move category down">▼</button>
                </span>
              </span>
            </summary>
            <div class="topic-content" style="margin-top: 1rem;">
              {newsletter_html}
            </div>
          </details>
        </section>"""
        topics_html_output.append(("Editorials & Newsletters", newsletter_section_html, len(all_newsletters)))

    # 7. Write digest
    if not silent:
        print("Compiling final editorial page...")
    
    combined_topics_html = "".join([t[1] for t in topics_html_output])
    
    if not os.path.exists(TEMPLATE_PATH):
        print(f"Error: Template not found at {TEMPLATE_PATH}.")
        return False
        
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        template_content = f.read()

    gen_time_str = datetime.now().strftime("%B %d, %Y - %I:%M %p")
    
    output_content = template_content.replace("{GENERATED_TIME}", gen_time_str)
    output_content = output_content.replace("{TOPICS_HTML}", combined_topics_html)
    output_content = output_content.replace("{CONFIG_JSON}", config_raw_str)

    if mode == "hosted":
        # Hosted/PWA build: inject the PWA tags and strip the settings drawer (a static
        # host has no /save server; authoring stays in the local build).
        output_content = output_content.replace("{PWA_HEAD}", PWA_HEAD_TAGS)
        output_content = output_content.replace("{PWA_FOOT}", PWA_FOOT_TAGS)
        output_content = re.sub(
            r"<!--HOSTED_STRIP_START-->.*?<!--HOSTED_STRIP_END-->",
            "", output_content, flags=re.DOTALL)

        out_dir = out_path or os.path.join(PROJECT_DIR, "dist")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "index.html"), 'w', encoding='utf-8') as f:
            f.write(output_content)
        with open(os.path.join(out_dir, "manifest.json"), 'w', encoding='utf-8') as f:
            f.write(PWA_MANIFEST_JSON)
        with open(os.path.join(out_dir, "sw.js"), 'w', encoding='utf-8') as f:
            f.write(PWA_SERVICE_WORKER)
        with open(os.path.join(out_dir, "icon.svg"), 'w', encoding='utf-8') as f:
            f.write(PWA_ICON_SVG)
        if not silent:
            print(f"Hosted digest written to: {out_dir}")
    else:
        # Local build: strip the (unused) PWA placeholders and write a single file.
        output_content = output_content.replace("{PWA_HEAD}", "").replace("{PWA_FOOT}", "")
        target = out_path or OUTPUT_PATH
        with open(target, 'w', encoding='utf-8') as f:
            f.write(output_content)
        if not silent:
            print(f"Digest successfully updated: {target}")
    return True

class NewsBotHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urllib.parse.urlparse(path)
        path_str = parsed.path
        if path_str == "/" or path_str == "/index.html" or path_str == "/digest.html":
            return OUTPUT_PATH
        # Convert path to absolute path in PROJECT_DIR
        rel_path = path_str.lstrip('/')
        safe_path = os.path.abspath(os.path.join(PROJECT_DIR, rel_path))
        if not safe_path.startswith(PROJECT_DIR):
            return ""
        return safe_path

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/refresh":
            try:
                print("Server received refresh request. Recompiling feed...")
                compile_digest(silent=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                print(f"Error during server recompilation: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
                
        elif parsed.path == "/save":
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                config_data = json.loads(post_data.decode('utf-8'))
                
                print(f"Server received save request. Saving to {CONFIG_PATH}...")
                with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(config_data, f, indent=2)
                
                # Recompile feed immediately so the next load has the updated topics
                compile_digest(silent=True)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                print(f"Error saving config via server: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def log_message(self, format, *args):
        # Silence default request logs unless running with server output
        if "--silent" in sys.argv:
            return
        sys.stderr.write("%s - - [%s] %s\n" %
                         (self.address_string(),
                          self.log_date_time_string(),
                          format%args))

def get_cli_value(flag):
    """Read a CLI value passed as '--flag value' or '--flag=value'."""
    for i, arg in enumerate(sys.argv):
        if arg == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None

def main():
    serve_mode = "--serve" in sys.argv
    silent_mode = "--silent" in sys.argv
    mode = get_cli_value("--mode") or "local"
    config_source = get_cli_value("--config")
    out_path = get_cli_value("--out")

    # Hosted builds are headless (run by the scheduler): compile and exit.
    if mode == "hosted":
        success = compile_digest(silent=silent_mode, mode="hosted",
                                 config_source=config_source, out_path=out_path)
        sys.exit(0 if success else 1)

    # 1. Compile digest first (local mode)
    success = compile_digest(silent=silent_mode or serve_mode,
                             config_source=config_source, out_path=out_path)
    if not success:
        sys.exit(1)

    if serve_mode:
        port = 8999
        class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            
        handler = NewsBotHTTPHandler
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            print(f"\n[news-bot server] Running at http://127.0.0.1:{port}")
            print("Press Ctrl+C in this terminal window to stop the server.\n")
            # Open browser to the localhost address instead of file:///
            webbrowser.open(f"http://127.0.0.1:{port}")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                print("\nServer shutting down...")
                server.server_close()
        except OSError as e:
            # Port already in use, likely because server is already running
            print(f"\n[news-bot server] Port {port} is already in use. Opening browser...")
            webbrowser.open(f"http://127.0.0.1:{port}")
    else:
        # Open browser to the local file
        if not silent_mode:
            webbrowser.open("file://" + OUTPUT_PATH)

if __name__ == "__main__":
    main()
