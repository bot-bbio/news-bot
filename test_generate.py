"""Behavioral tests for news-bot's recency and story-capping pipeline.

These tests are network-free: feed XML is synthesized in-memory and the network
fetch is monkeypatched, so they assert the *logic* (hard recency filter,
time-priority ordering, no per-topic cap, per-feed limits) rather than live feeds.

Run:  python test_generate.py
"""

import unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
