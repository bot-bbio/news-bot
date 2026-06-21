"""Behavioral tests for news-bot's recency and story-capping pipeline.

These tests are network-free: feed XML is synthesized in-memory and the network
fetch is monkeypatched, so they assert the *logic* (hard recency filter,
time-priority ordering, no per-topic cap, per-feed limits) rather than live feeds.

Run:  python test_generate.py
"""

import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

import generate


def _rss(items):
    """Build a minimal RSS document from a list of (title, link, pubdate) tuples.

    A pubdate of None omits the <pubDate> element entirely (undated item)."""
    parts = ['<?xml version="1.0"?>', "<rss><channel>"]
    for title, link, pub in items:
        parts.append("<item>")
        parts.append(f"<title>{title}</title>")
        parts.append(f"<link>{link}</link>")
        if pub is not None:
            parts.append(f"<pubDate>{pub}</pubDate>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


def _rfc822(dt):
    return format_datetime(dt)


class DateParsingTests(unittest.TestCase):
    def test_rfc822_with_timezone(self):
        dt = generate.parse_rfc822_date("Mon, 16 Jun 2025 14:30:00 +0000")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual((dt.year, dt.month, dt.day), (2025, 6, 16))

    def test_rfc822_gmt_suffix(self):
        dt = generate.parse_rfc822_date("Mon, 16 Jun 2025 14:30:00 GMT")
        self.assertIsNotNone(dt)

    def test_iso8601_z(self):
        dt = generate.parse_rfc822_date("2025-06-16T14:30:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 14)

    def test_iso8601_offset_with_fractional(self):
        dt = generate.parse_rfc822_date("2025-06-16T14:30:00.123456+02:00")
        self.assertIsNotNone(dt)

    def test_naive_is_treated_as_utc(self):
        dt = generate.parse_rfc822_date("2025-06-16 14:30:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_garbage_returns_none(self):
        self.assertIsNone(generate.parse_rfc822_date("not a date"))
        self.assertIsNone(generate.parse_rfc822_date(""))
        self.assertIsNone(generate.parse_rfc822_date(None))


class GNewsRecencyTests(unittest.TestCase):
    """The `when:` recency operator workaround for Google News relevance ranking."""

    GN = "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en"

    def test_injects_when_operator_into_query(self):
        out = generate.apply_gnews_recency(self.GN, "1d")
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(out).query)["q"][0]
        self.assertIn("site:apnews.com", q)
        self.assertIn("when:1d", q)

    def test_preserves_other_query_params(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(generate.apply_gnews_recency(self.GN, "1d")).query)
        self.assertEqual(qs["hl"][0], "en-US")
        self.assertEqual(qs["gl"][0], "US")
        self.assertEqual(qs["ceid"][0], "US:en")

    def test_idempotent_when_already_constrained(self):
        once = generate.apply_gnews_recency(self.GN, "1d")
        twice = generate.apply_gnews_recency(once, "1d")
        self.assertEqual(once, twice)

    def test_noop_for_non_gnews_url(self):
        url = "http://feeds.bbci.co.uk/news/world/rss.xml"
        self.assertEqual(generate.apply_gnews_recency(url, "1d"), url)

    def test_noop_for_empty_recency(self):
        self.assertEqual(generate.apply_gnews_recency(self.GN, None), self.GN)
        self.assertEqual(generate.apply_gnews_recency(self.GN, ""), self.GN)

    def test_fetch_path_applies_recency(self):
        """A feed carrying "recency" must reach the network with when: injected;
        a feed without it must be fetched unchanged."""
        seen = []
        orig = generate.fetch_feed_xml
        generate.fetch_feed_xml = lambda url: (seen.append(url) or b"<rss><channel></channel></rss>")
        try:
            topic_config = {"feeds": [
                {"name": "AP", "url": self.GN, "recency": "1d"},
                {"name": "BBC", "url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
            ]}
            generate.fetch_and_process_topic("T", topic_config, default_feed_cap=6,
                                             max_days_old=7, safety_ceiling=60)
        finally:
            generate.fetch_feed_xml = orig
        self.assertTrue(any("when:1d" in u or "when%3A1d" in u for u in seen),
                        "recency feed should be fetched with when: injected")
        self.assertTrue(any(u == "http://feeds.bbci.co.uk/news/world/rss.xml" for u in seen),
                        "non-recency feed should be fetched unchanged")


class RecencyFilterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def test_old_items_dropped(self):
        recent = self.now - timedelta(days=1)
        stale = self.now - timedelta(days=30)
        xml = _rss([
            ("Fresh", "https://example.com/fresh", _rfc822(recent)),
            ("Stale", "https://example.com/stale", _rfc822(stale)),
        ])
        items = generate.parse_rss_xml(xml, "Test", max_items=10, max_days_old=7)
        titles = [i["title"] for i in items]
        self.assertIn("Fresh", titles)
        self.assertNotIn("Stale", titles)

    def test_undated_items_dropped_hard_filter(self):
        recent = self.now - timedelta(hours=2)
        xml = _rss([
            ("Dated", "https://example.com/dated", _rfc822(recent)),
            ("Undated", "https://example.com/undated", None),
        ])
        items = generate.parse_rss_xml(xml, "Test", max_items=10, max_days_old=7)
        titles = [i["title"] for i in items]
        self.assertEqual(titles, ["Dated"], "undated items must be dropped by the hard filter")

    def test_leading_whitespace_and_bom_tolerated(self):
        recent = self.now - timedelta(hours=1)
        xml = _rss([("Fresh", "https://example.com/fresh", _rfc822(recent))])
        # Simulate a feed that prefixes a BOM and blank lines before the declaration.
        dirty = b"\xef\xbb\xbf\n\n  " + xml
        items = generate.parse_rss_xml(dirty, "Test", max_items=10, max_days_old=7)
        self.assertEqual([i["title"] for i in items], ["Fresh"])

    def test_per_feed_cap_respected(self):
        items_src = [
            (f"Item {n}", f"https://example.com/{n}", _rfc822(self.now - timedelta(hours=n)))
            for n in range(20)
        ]
        xml = _rss(items_src)
        items = generate.parse_rss_xml(xml, "Test", max_items=4, max_days_old=7)
        self.assertEqual(len(items), 4)


class TopicCapTests(unittest.TestCase):
    """The per-topic global truncation was removed: every recent, deduped story
    should survive, ordered newest-first."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self._orig_fetch = generate.fetch_feed_xml

    def tearDown(self):
        generate.fetch_feed_xml = self._orig_fetch

    def test_no_global_topic_cap_and_time_ordering(self):
        # Two feeds, each returning 8 fresh items -> 16 total. Old behaviour capped
        # the topic at default_feed_cap * 2; the new behaviour keeps them all.
        def fake_fetch(url):
            base = 0 if "a" in url else 100
            return _rss([
                (f"Story {base + n}", f"{url}#{n}",
                 _rfc822(self.now - timedelta(hours=base + n)))
                for n in range(8)
            ])

        generate.fetch_feed_xml = fake_fetch

        topic_config = {
            "feeds": [
                {"name": "Feed A", "url": "https://a.example.com/feed", "max_items": 8},
                {"name": "Feed B", "url": "https://b.example.com/feed", "max_items": 8},
            ]
        }
        result = generate.fetch_and_process_topic(
            "Topic", topic_config, default_feed_cap=6, max_days_old=7, safety_ceiling=1000)
        aux = result["auxiliary"]

        self.assertEqual(len(aux), 16, "all 16 fresh stories should survive (no everyday per-topic cap)")

        # Verify newest-first ordering (time priority).
        times = [a["datetime"] for a in aux]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_default_feed_cap_used_when_feed_omits_max_items(self):
        def fake_fetch(url):
            return _rss([
                (f"S{n}", f"{url}#{n}", _rfc822(self.now - timedelta(hours=n)))
                for n in range(20)
            ])

        generate.fetch_feed_xml = fake_fetch

        topic_config = {"feeds": [{"name": "NoCapFeed", "url": "https://x.example.com/feed"}]}
        result = generate.fetch_and_process_topic(
            "Topic", topic_config, default_feed_cap=5, max_days_old=7, safety_ceiling=1000)
        self.assertEqual(len(result["auxiliary"]), 5, "feed without max_items should inherit default_feed_cap")

    def test_safety_ceiling_trims_runaway_topic(self):
        # A single misbehaving feed returning 50 fresh items must be trimmed to the
        # hidden safety ceiling, even though there is no everyday per-topic cap.
        def fake_fetch(url):
            return _rss([
                (f"S{n}", f"{url}#{n}", _rfc822(self.now - timedelta(minutes=n)))
                for n in range(50)
            ])

        generate.fetch_feed_xml = fake_fetch
        topic_config = {"feeds": [{"name": "Flood", "url": "https://flood.example.com/feed", "max_items": 50}]}
        result = generate.fetch_and_process_topic(
            "Topic", topic_config, default_feed_cap=6, max_days_old=7, safety_ceiling=10)
        self.assertEqual(len(result["auxiliary"]), 10, "safety ceiling must trim a runaway topic")

    def test_duplicate_links_deduped(self):
        def fake_fetch(url):
            return _rss([
                ("Dup", "https://dup.example.com/same", _rfc822(self.now - timedelta(hours=1))),
                ("Dup again", "https://dup.example.com/same", _rfc822(self.now - timedelta(hours=2))),
                ("Unique", "https://dup.example.com/other", _rfc822(self.now - timedelta(hours=3))),
            ])

        generate.fetch_feed_xml = fake_fetch
        topic_config = {"feeds": [{"name": "DupFeed", "url": "https://dup.example.com/feed", "max_items": 10}]}
        result = generate.fetch_and_process_topic(
            "Topic", topic_config, default_feed_cap=6, max_days_old=7, safety_ceiling=1000)
        links = [a["link"] for a in result["auxiliary"]]
        self.assertEqual(len(links), len(set(links)), "duplicate links should be collapsed")


class CompileDigestTests(unittest.TestCase):
    """End-to-end render tests for local vs hosted output (network stubbed)."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self._orig_fetch = generate.fetch_feed_xml
        # Any feed/newsletter URL returns one fresh item.
        generate.fetch_feed_xml = lambda url: _rss([
            ("Fresh story", url + "#a", _rfc822(self.now - timedelta(hours=1))),
        ])
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({
                "topics": {
                    "World": {"enabled": True, "feeds": [
                        {"name": "Reuters", "url": "https://example.com/r", "max_items": 3}
                    ]}
                },
                "email_newsletters": [
                    {"sender": "x@y.z", "subject_matches": "", "feed_name": "Ed Zitron",
                     "fallback_url": "https://example.com/zitron"}
                ],
                "max_days_old": 7,
                "max_items_per_feed": 6
            }, f)

    def tearDown(self):
        generate.fetch_feed_xml = self._orig_fetch
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hosted_mode_emits_pwa_and_strips_settings(self):
        out_dir = os.path.join(self.tmp, "dist")
        ok = generate.compile_digest(silent=True, mode="hosted",
                                     config_source=self.config_path, out_path=out_dir)
        self.assertTrue(ok)
        for asset in ("index.html", "manifest.json", "sw.js", "icon.svg"):
            self.assertTrue(os.path.exists(os.path.join(out_dir, asset)), f"missing {asset}")

        idx = open(os.path.join(out_dir, "index.html"), encoding="utf-8").read()
        self.assertNotIn('class="settings-drawer"', idx, "hosted build must drop the settings drawer")
        self.assertNotIn("HOSTED_STRIP", idx, "strip markers must be removed")
        self.assertNotIn("{PWA_HEAD}", idx)
        self.assertNotIn("{PWA_FOOT}", idx)
        self.assertIn('rel="manifest"', idx)
        self.assertIn("serviceWorker", idx)
        # Newsletters still render (as RSS) in their own section.
        self.assertIn("Editorials & Newsletters", idx)

    def test_times_are_rendered_client_side_from_absolute_timestamps(self):
        """Times must ship as absolute UTC ISO-8601 and be rendered in the viewer's
        timezone on load — not baked as server-local strings. This guards against the
        two original bugs: a UTC-runner mislabeling the 'Compiled' stamp, and frozen
        'Nh ago' ages on a cached page."""
        out_file = os.path.join(self.tmp, "digest.html")
        ok = generate.compile_digest(silent=True, mode="local",
                                     config_source=self.config_path, out_path=out_file)
        self.assertTrue(ok)
        page = open(out_file, encoding="utf-8").read()

        # Article ages ship as <time datetime="...ISO..."> with a UTC offset, so the
        # client can recompute them against the viewer's clock.
        self.assertIn('<time class="rel-time" datetime="', page)
        self.assertIn("+00:00", page)  # a UTC offset is present on the stamps

        # The compile stamp ships as an absolute ISO timestamp the client localizes,
        # and the no-JS fallback is explicitly UTC-labeled (not an unlabeled local time).
        self.assertIn('id="generation-time"', page)
        self.assertIn('data-iso="', page)
        self.assertIn("UTC", page)
        self.assertNotIn("{GENERATED_ISO}", page)

        # The client-side renderer is present.
        self.assertIn("function renderTimes()", page)

    def test_compile_stamp_is_utc_not_runner_local(self):
        """The baked 'Compiled' string must be UTC-derived so it never silently shows
        a UTC CI runner's wall clock as if it were the reader's local time."""
        out_file = os.path.join(self.tmp, "digest.html")
        generate.compile_digest(silent=True, mode="local",
                                config_source=self.config_path, out_path=out_file)
        page = open(out_file, encoding="utf-8").read()
        import re as _re
        m = _re.search(r'data-iso="([^"]+)"', page)
        self.assertIsNotNone(m, "header must carry an absolute ISO compile timestamp")
        stamp = datetime.fromisoformat(m.group(1))
        self.assertIsNotNone(stamp.tzinfo, "compile timestamp must be timezone-aware")
        # Within a minute of 'now' in UTC (proves it's UTC-based, not naive local).
        drift = abs((datetime.now(timezone.utc) - stamp).total_seconds())
        self.assertLess(drift, 120, "compile timestamp should be ~now in UTC")

    def test_local_mode_keeps_settings_and_writes_single_file(self):
        out_file = os.path.join(self.tmp, "digest.html")
        ok = generate.compile_digest(silent=True, mode="local",
                                     config_source=self.config_path, out_path=out_file)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(out_file))
        page = open(out_file, encoding="utf-8").read()
        self.assertIn('class="settings-drawer"', page, "local build keeps the settings drawer")
        self.assertNotIn("{PWA_HEAD}", page)
        self.assertNotIn("{PWA_FOOT}", page)


class ConfigSourceTests(unittest.TestCase):
    def test_local_path_loads(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "c.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"topics": {}}')
            self.assertEqual(generate.load_config_source(p), '{"topics": {}}')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_path_returns_none(self):
        self.assertIsNone(generate.load_config_source("/no/such/config-xyz.json"))


class RecencyParsingTests(unittest.TestCase):
    def test_units(self):
        self.assertAlmostEqual(generate.parse_recency_to_days("1d", 7), 1.0)
        self.assertAlmostEqual(generate.parse_recency_to_days("12h", 7), 0.5)
        self.assertAlmostEqual(generate.parse_recency_to_days("2w", 7), 14.0)
        self.assertAlmostEqual(generate.parse_recency_to_days("30m", 7), 30 / 1440)
        self.assertAlmostEqual(generate.parse_recency_to_days("1.5d", 7), 1.5)
        self.assertAlmostEqual(generate.parse_recency_to_days("3", 7), 3.0)  # bare = days

    def test_whitespace_and_case(self):
        self.assertAlmostEqual(generate.parse_recency_to_days(" 1D ", 7), 1.0)
        self.assertAlmostEqual(generate.parse_recency_to_days("2 D", 7), 2.0)

    def test_numeric_input(self):
        self.assertEqual(generate.parse_recency_to_days(2, 7), 2.0)
        self.assertEqual(generate.parse_recency_to_days(2.5, 7), 2.5)

    def test_fallbacks_to_default(self):
        for bad in (None, "", "garbage", "0d", "-5", "0", 0, -3, True, False):
            self.assertEqual(generate.parse_recency_to_days(bad, 7), 7,
                             f"{bad!r} should fall back to the default window")


class RecencyOverrideTests(unittest.TestCase):
    """Per-feed `recency` should tighten the cutoff below the global window."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self._orig = generate.fetch_feed_xml
        generate.fetch_feed_xml = lambda url: _rss([
            ("Old", url + "#old", _rfc822(self.now - timedelta(days=2))),
            ("Fresh", url + "#fresh", _rfc822(self.now - timedelta(hours=2))),
        ])

    def tearDown(self):
        generate.fetch_feed_xml = self._orig

    def test_per_feed_recency_tightens_window(self):
        cfg = {"feeds": [{"name": "Wire", "url": "https://e.com/f", "max_items": 10, "recency": "1d"}]}
        res = generate.fetch_and_process_topic("T", cfg, default_feed_cap=6, max_days_old=7, safety_ceiling=1000)
        self.assertEqual([a["title"] for a in res["auxiliary"]], ["Fresh"],
                         "a 1d recency must drop the 2-day-old item even though the global window is 7d")

    def test_without_recency_uses_global_window(self):
        cfg = {"feeds": [{"name": "Wire", "url": "https://e.com/f", "max_items": 10}]}
        res = generate.fetch_and_process_topic("T", cfg, default_feed_cap=6, max_days_old=7, safety_ceiling=1000)
        self.assertEqual(sorted(a["title"] for a in res["auxiliary"]), ["Fresh", "Old"],
                         "with no per-feed recency, the global 7d window keeps both items")


class GNewsRecencyUrlTests(unittest.TestCase):
    def test_injects_when_operator_for_google_news(self):
        url = "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US"
        out = generate.apply_gnews_recency(url, "1d")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(out).query)["q"][0]
        self.assertIn("when:1d", q)  # decoded query carries the operator

    def test_noop_for_non_google_news(self):
        url = "https://www.theguardian.com/world/rss"
        self.assertEqual(generate.apply_gnews_recency(url, "1d"), url)

    def test_noop_when_recency_absent(self):
        url = "https://news.google.com/rss/search?q=foo"
        self.assertEqual(generate.apply_gnews_recency(url, None), url)

    def test_idempotent(self):
        url = "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US"
        once = generate.apply_gnews_recency(url, "1d")
        twice = generate.apply_gnews_recency(once, "1d")
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main(verbosity=2)
