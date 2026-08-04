import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from digest import (
    Article,
    build_report,
    dedupe_articles,
    extract_response_text,
    filter_articles,
    parse_model_json,
    report_page_url,
    render_rss,
    sign_feishu_request,
)


class DigestTests(unittest.TestCase):
    def test_dedupe_articles_normalizes_tracking_query(self):
        articles = [
            Article("A", "https://example.com/post?utm_source=x", "src", "2026-08-04T00:00:00+00:00", ""),
            Article("A copy", "https://example.com/post", "src", "2026-08-04T00:01:00+00:00", ""),
        ]

        result = dedupe_articles(articles)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, "https://example.com/post?utm_source=x")

    def test_filter_articles_keeps_recent_matching_items(self):
        now = datetime(2026, 8, 4, 8, tzinfo=timezone.utc)
        articles = [
            Article("Agent runtime release", "https://a", "GitHub", "2026-08-04T06:00:00+00:00", ""),
            Article("Old agent article", "https://b", "Blog", "2026-08-01T06:00:00+00:00", ""),
            Article("Cooking recipe", "https://c", "Blog", "2026-08-04T06:00:00+00:00", ""),
        ]

        result = filter_articles(articles, now=now, hours=48, keywords=("agent", "runtime"))

        self.assertEqual([article.title for article in result], ["Agent runtime release"])

    def test_parse_model_json_accepts_fenced_json(self):
        payload = {"items": [{"title": "A", "summary": "B"}]}

        result = parse_model_json("```json\n" + json.dumps(payload) + "\n```")

        self.assertEqual(result, payload)

    def test_extract_response_text_supports_responses_output_items(self):
        response = {"output": [{"content": [{"type": "output_text", "text": '{"ok":true}'}]}]}

        self.assertEqual(extract_response_text(response), '{"ok":true}')

    def test_build_report_without_key_returns_degraded_sections(self):
        articles = [Article("Agent release", "https://example.com/a", "Source", "2026-08-04T00:00:00+00:00", "Summary")]

        report = build_report(articles, mode="daily", base_url="https://example.com/v1", api_key="", model="test")

        self.assertTrue(report["degraded"])
        self.assertTrue(report["sections"][0]["items"])

    def test_render_rss_escapes_xml_and_contains_items(self):
        articles = [
            Article("A & B", "https://example.com/a?x=1&y=2", "Source", "2026-08-04T06:00:00+00:00", "Summary")
        ]

        rss = render_rss(articles, title="AI Digest", link="https://example.com")

        self.assertIn("A &amp; B", rss)
        self.assertIn("https://example.com/a?x=1&amp;y=2", rss)
        self.assertIn("<item>", rss)

    def test_sign_feishu_request_is_deterministic(self):
        self.assertEqual(
            sign_feishu_request("1700000000", "secret"),
            "fiWS2+gh28DOydAv7hzONH/mDn9+b1Y4Y5ivXWXy8vA=",
        )

    def test_report_page_url_adds_cache_buster(self):
        url = report_page_url(
            "https://example.github.io/ai-engineering-career-digest/",
            Path("2026-08-04.md"),
            cache_buster=1722758400,
        )

        self.assertEqual(
            url,
            "https://example.github.io/ai-engineering-career-digest/daily/2026-08-04.html?v=1722758400",
        )


if __name__ == "__main__":
    unittest.main()
