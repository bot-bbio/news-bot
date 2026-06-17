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
import imaplib
import email
from email.header import decode_header
import http.server
import socketserver
import urllib.parse


# Base directories
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
TEMPLATE_PATH = os.path.join(PROJECT_DIR, "template.html")
OUTPUT_PATH = os.path.join(PROJECT_DIR, "digest.html")
ENV_PATH = os.path.join(PROJECT_DIR, ".env")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def load_env(env_path):
    """Load variables from a local .env file (gitignored) into a dictionary."""
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                      continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars

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

def clean_email_html(raw_html):
    """Clean layout tables/div styles from HTML email newsletter bodies for inline reading."""
    if not raw_html:
        return ""
    # Strip script/style tags
    raw_html = re.sub(r'<head>.*?</head>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    raw_html = re.sub(r'<script.*?>.*?</script>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    raw_html = re.sub(r'<style.*?>.*?</style>', '', raw_html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove layout elements (tables, divs, spans) but preserve structural elements (p, br, h1-h6, a)
    # Strip attributes except href from tags
    def clean_tag(match):
        tag_content = match.group(0)
        tag_name = match.group(1).lower()
        if tag_name == 'a':
            # Preserve href
            href_match = re.search(r'href=["\']([^"\']+)["\']', tag_content, re.IGNORECASE)
            if href_match:
                return f'<a href="{href_match.group(1)}" target="_blank" rel="noopener noreferrer">'
            return '<a>'
        elif tag_name in ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'strong', 'em', 'b', 'i']:
            return f'<{tag_name}>'
        elif tag_name in ['/p', '/a', '/h1', '/h2', '/h3', '/h4', '/h5', '/h6', '/ul', '/ol', '/li', '/strong', '/em', '/b', '/i']:
            return f'<{tag_name}>'
        return '' # Strip everything else (divs, tables, etc.)
        
    cleaned = re.sub(r'<(/?[a-zA-Z0-9]+)(?:\s+[^>]*?)?>', clean_tag, raw_html)
    
    # Normalize extra line breaks
    cleaned = re.sub(r'(?:<br>\s*){3,}', '<br><br>', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

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
    """Parse dates from RSS feeds and emails."""
    if not date_str:
        return None
    
    # Clean day of week
    clean_date = re.sub(r'^[A-Za-z]{3},\s*', '', date_str.strip())
    
    formats = [
        "%d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%d %b %Y %H:%M %Z",
        "%d %b %Y %H:%M %z"
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
            
            # Apply strict date filter (except if date parsing fails entirely)
            if pub_datetime and pub_datetime < cutoff_date:
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

def fetch_local_emails(env_vars, newsletter_configs, max_days_old=7):
    """Connect to IMAP and retrieve matching newsletters locally and securely."""
    imap_server = env_vars.get("EMAIL_IMAP_SERVER")
    email_addr = env_vars.get("EMAIL_ADDRESS")
    email_pwd = env_vars.get("EMAIL_APP_PASSWORD")
    folder = env_vars.get("EMAIL_FOLDER", "INBOX")
    
    if not (imap_server and email_addr and email_pwd):
        print("  Email integration credentials not configured in .env. Skipping email fetch.")
        return []
        
    articles = []
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_days_old)
    
    try:
        print(f"Connecting to IMAP mail server {imap_server}...")
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_addr, email_pwd)
        
        # Select folder
        status, _ = mail.select(folder)
        if status != 'OK':
            print(f"  Error: Could not select folder '{folder}'")
            return []
            
        # Search criteria: emails in the last 7 days
        since_date = cutoff_date.strftime("%d-%b-%Y")
        search_crit = f'(SINCE {since_date})'
        status, data = mail.search(None, search_crit)
        
        if status != 'OK' or not data[0]:
            print("  No recent emails found in folder.")
            mail.logout()
            return []
            
        mail_ids = data[0].split()
        print(f"  Scanning {len(mail_ids)} recent emails for newsletters...")
        
        # Scan in reverse order (newest first)
        for mid in reversed(mail_ids):
            status, msg_data = mail.fetch(mid, '(RFC822)')
            if status != 'OK':
                continue
                
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # Extract headers
            from_header = msg.get("From", "")
            subject_header = msg.get("Subject", "")
            date_header = msg.get("Date", "")
            
            # Decode headers safely
            def decode_safely(header_val):
                if not header_val:
                    return ""
                decoded = decode_header(header_val)
                parts = []
                for val, charset in decoded:
                    if isinstance(val, bytes):
                        parts.append(val.decode(charset or 'utf-8', errors='ignore'))
                    else:
                        parts.append(str(val))
                return "".join(parts)
                
            sender = decode_safely(from_header)
            subject = decode_safely(subject_header)
            email_date = parse_rfc822_date(date_header)
            
            if email_date and email_date < cutoff_date:
                continue # Re-confirm date check
                
            # Check if this email matches any of our configured newsletter senders
            matched_config = None
            for config in newsletter_configs:
                target_sender = config["sender"].lower()
                target_subject = config.get("subject_matches", "").lower()
                
                if target_sender in sender.lower():
                    if not target_subject or target_subject in subject.lower():
                        matched_config = config
                        break
                        
            if not matched_config:
                continue
                
            # Parse body
            body_html = ""
            body_text = ""
            
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disp = str(part.get("Content-Disposition"))
                    
                    if "attachment" in content_disp:
                        continue
                        
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or 'utf-8'
                        decoded_payload = payload.decode(charset, errors='ignore')
                        
                        if content_type == "text/html":
                            body_html = decoded_payload
                        elif content_type == "text/plain":
                            body_text = decoded_payload
                    except Exception:
                        pass
            else:
                try:
                    payload = msg.get_payload(decode=True)
                    charset = msg.get_content_charset() or 'utf-8'
                    decoded_payload = payload.decode(charset, errors='ignore')
                    if msg.get_content_type() == "text/html":
                        body_html = decoded_payload
                    else:
                        body_text = decoded_payload
                except Exception:
                    pass
            
            # Format content: prefer clean HTML layout, fallback to text
            if body_html:
                content_html = clean_email_html(body_html)
            else:
                # Format plain text with linebreaks
                content_html = "<p>" + html.escape(body_text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
                
            # Truncate content preview for the main listing
            snippet = clean_html(body_html or body_text)
            snippet = snippet[:220] + "..." if len(snippet) > 220 else snippet
            
            # Unique ID based on subject & date
            article_id = hashlib.md5((subject + str(email_date)).encode('utf-8')).hexdigest()
            
            articles.append({
                "id": article_id,
                "title": subject,
                "link": "#", # Local email, no external link
                "description": snippet,
                "content_body": content_html, # Contains cleaned email body
                "datetime": email_date,
                "relative_time": get_relative_time(email_date),
                "source": matched_config["feed_name"],
                "is_email": True
            })
            print(f"  [Email] Loaded newsletter: '{subject}' from {matched_config['feed_name']}")
            
        mail.close()
        mail.logout()
    except Exception as e:
        print(f"  IMAP email fetch error: {e}. Falling back to public URL feeds.")
        
    return articles

def fetch_and_process_topic(topic_name, topic_config, max_items_per_feed, max_days_old):
    """Concurrently download feeds for a topic, processing Basis vs Auxiliary news."""
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
                    # Feed-specific maximum cap to prevent source domination, fallback to 3
                    feed_max = int(feed.get("max_items", 3))
                    items = parse_rss_xml(xml_data, feed["name"], feed_max, max_days_old)
                    topic_data["auxiliary"].extend(items)
                except Exception as e:
                    print(f"  Error fetching {feed['name']} auxiliary: {e}")
                    
        # Sort auxiliary articles chronologically
        topic_data["auxiliary"] = [a for a in topic_data["auxiliary"] if a["datetime"] is not None]
        topic_data["auxiliary"].sort(key=lambda x: x["datetime"], reverse=True)
        # De-duplicate articles by link
        seen_links = set()
        unique_aux = []
        for a in topic_data["auxiliary"]:
            if a["link"] not in seen_links:
                seen_links.add(a["link"])
                unique_aux.append(a)
        topic_data["auxiliary"] = unique_aux[:max_items_per_feed * 2]
        
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
        details_html = ""
        link_target = f'href="{art["link"]}" target="_blank" rel="noopener noreferrer"'
        paywall_tag = ' <span class="paywall-label">• 🔒 Paywall</span>' if art.get("paywall") else ''
        
        if art.get("is_email"):
            link_target = 'href="#" onclick="return false;" style="cursor:default;"'
            details_html = f"""
            <details style="margin-top: 0.5rem; font-family: Georgia; font-size: 0.95rem;">
              <summary style="cursor:pointer; font-family: var(--font-ui); font-size: 0.75rem; color: var(--accent-crimson); font-weight:700; text-transform:uppercase;">Read Newsletter Inline</summary>
              <div style="margin-top: 0.75rem; padding: 1rem; border-top: 1px dotted var(--border-color); background: rgba(0,0,0,0.02); overflow-x: auto; max-height: 500px;">
                {art["content_body"]}
              </div>
            </details>"""
            
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
          {details_html}
        </li>"""
        
    html_out += '</ul>'
    return html_out

def compile_digest(silent=False):
    if not silent:
        print("Initializing news-bot clean rebuild...")
    
    # 1. Load config
    if not os.path.exists(CONFIG_PATH):
        print(f"Error: Config not found at {CONFIG_PATH}.")
        return False
        
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config_raw_str = f.read()
        config = json.loads(config_raw_str)
        
    max_items = config.get("max_items_per_feed", 10)
    max_days_old = config.get("max_days_old", 7)
    
    # 2. Check local environment credentials
    env_vars = load_env(ENV_PATH)
    
    # 3. Pull Email Newsletters (Secure IMAP)
    email_articles = []
    newsletter_configs = config.get("email_newsletters", [])
    if newsletter_configs:
        email_articles = fetch_local_emails(env_vars, newsletter_configs, max_days_old)
        
    # 4. For newsletters that failed to load via email, fetch public fallbacks
    loaded_newsletter_sources = {a["source"] for a in email_articles}
    fallback_newsletter_tasks = []
    
    for nc in newsletter_configs:
        if nc["feed_name"] not in loaded_newsletter_sources and nc.get("fallback_url"):
            if not silent:
                print(f"  Queueing public fallback feed for newsletter: {nc['feed_name']}...")
            fallback_newsletter_tasks.append({
                "name": nc["feed_name"],
                "url": nc["fallback_url"]
            })
            
    fallback_newsletter_articles = []
    if fallback_newsletter_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(fallback_newsletter_tasks)) as executor:
            futures = {
                executor.submit(fetch_feed_xml, t["url"]): t for t in fallback_newsletter_tasks
            }
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    xml_data = future.result()
                    items = parse_rss_xml(xml_data, task["name"], max_items, max_days_old)
                    fallback_newsletter_articles.extend(items)
                except Exception as e:
                    print(f"  Error fetching public fallback {task['name']}: {e}")

    # Combine loaded email articles & fallback RSS newsletters
    all_newsletters = email_articles + fallback_newsletter_articles
    all_newsletters.sort(key=lambda x: x["datetime"] if x["datetime"] else datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # 5. Fetch Topic Feeds (Parallel Execution)
    topics_html_output = []
    topics = config.get("topics", {})
    
    if not silent:
        print(f"Queueing {len(topics)} topic sections...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(topics))) as executor:
        future_to_topic = {
            executor.submit(fetch_and_process_topic, name, conf, max_items, max_days_old): name 
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

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        f.write(output_content)

    if not silent:
        print(f"Digest successfully updated: {OUTPUT_PATH}")
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

def main():
    serve_mode = "--serve" in sys.argv
    silent_mode = "--silent" in sys.argv
    
    # 1. Compile digest first
    success = compile_digest(silent=silent_mode or serve_mode)
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
