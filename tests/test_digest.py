import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

from digest import (
    Article,
    GitHubProject,
    build_report,
    build_project_report,
    dedupe_github_projects,
    dedupe_articles,
    extract_response_text,
    filter_articles,
    parse_model_json,
    report_page_url,
    render_rss,
    sign_feishu_request,
    write_report,
)


class DigestTests(unittest.TestCase):
    def test_github_projects_dedupe_by_repository(self):
        projects = [
            GitHubProject("owner/tool", "https://github.com/owner/tool", "tool", 9000, "2026-08-01T00:00:00Z"),
            GitHubProject("OWNER/TOOL", "https://github.com/OWNER/TOOL", "duplicate", 1, "", source="GitHub Trending"),
        ]
        self.assertEqual(len(dedupe_github_projects(projects)), 1)

    def test_project_report_without_key_keeps_verified_candidates(self):
        project = GitHubProject("owner/tool", "https://github.com/owner/tool", "A useful tool", 6000, "2026-08-01T00:00:00Z")
        report = build_project_report([project], base_url="https://example.com/v1", api_key="", model="test")
        self.assertTrue(report["degraded"])
        self.assertEqual(report["sections"][0]["items"][0]["url"], project.url)

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

    def test_render_editorial_html_contract(self):
        from digest import render_editorial_html

        report = {
            "overview": "AI 项目正在被成本和可验证结果重新定义。",
            "sections": [
                {
                    "name": "关键变化",
                    "items": [
                        {
                            "title": "平台开始算清资源账 <测试>",
                            "url": "https://example.com/article",
                            "source": "Example Source",
                            "summary": "发生了什么的摘要。",
                            "why": "为什么重要的判断。",
                            "action": "你现在可以做的动作。",
                            "importance": 5,
                        }
                    ],
                },
                {"name": "工程实践", "items": []},
                {"name": "学习优先级", "items": []},
                {"name": "职业信号", "items": []},
            ],
            "degraded": False,
        }

        rendered = render_editorial_html(
            report,
            title="AI 工程与职业速报",
            generated_at="2026-08-05 08:00:00 CST",
            mode="daily",
        )

        self.assertIn("radial-gradient", rendered)
        self.assertIn("关键变化", rendered)
        self.assertIn("工程实践", rendered)
        self.assertIn("学习优先级", rendered)
        self.assertIn("职业信号", rendered)
        self.assertIn("发生了什么", rendered)
        self.assertIn("为什么重要", rendered)
        self.assertIn("你现在做什么", rendered)
        self.assertIn("平台开始算清资源账 &lt;测试&gt;", rendered)
        self.assertNotIn("<a href=", rendered)
        self.assertNotIn("https://example.com/article", rendered)
        self.assertIn("2026-08-05 08:00:00 CST", rendered)
        self.assertNotIn("第 162 期", rendered)

    def test_render_report_png_invocation(self):
        from digest import render_report_png

        html_path = Path("input.html")
        png_path = Path("output.png")
        with patch("digest.subprocess.run") as run_mock:
            result = render_report_png(html_path, png_path)

        self.assertEqual(result, png_path)
        command = run_mock.call_args.args[0]
        self.assertTrue(any(str(part).lower() == "node" for part in command))
        self.assertIn("scripts/render_report.mjs", [str(part).replace("\\", "/") for part in command])
        self.assertIn("--input", command)
        self.assertIn("--output", command)
        self.assertTrue(run_mock.call_args.kwargs["check"])

    def test_send_feishu_image_contract(self):
        from digest import notify_feishu, send_feishu_image

        image_path = Path("digest.png")
        with patch(
            "digest.http_post_json",
            side_effect=[
                {"code": 0, "tenant_access_token": "tenant-token"},
                {"code": 0},
            ],
        ) as json_post, patch(
            "digest.http_post_multipart",
            return_value={"code": 0, "data": {"image_key": "img_v3_key"}},
        ) as multipart_post:
            send_feishu_image("app-id", "app-secret", "chat-id", image_path)

        token_call = json_post.call_args_list[0]
        self.assertIn("tenant_access_token/internal", token_call.args[0])
        self.assertEqual(token_call.args[1], {"app_id": "app-id", "app_secret": "app-secret"})
        self.assertIn("/im/v1/messages", json_post.call_args_list[1].args[0])
        message_payload = json_post.call_args_list[1].args[1]
        self.assertEqual(message_payload["receive_id"], "chat-id")
        self.assertEqual(message_payload["msg_type"], "image")
        self.assertIn("img_v3_key", message_payload["content"])
        self.assertEqual(multipart_post.call_args.args[1], {"image_type": "message"})
        self.assertEqual(multipart_post.call_args.args[2], "image")
        self.assertEqual(multipart_post.call_args.args[3], image_path)

        with patch("digest.send_feishu") as webhook, patch("digest.send_feishu_image") as image_sender:
            notify_feishu(
                image_path=image_path,
                report_url="https://example.com/daily/2026-08-05.html",
                app_id="",
                app_secret="",
                chat_id="",
                webhook_url="hook",
                secret="secret",
            )
        image_sender.assert_not_called()
        webhook.assert_called_once()
        self.assertIn("https://example.com/daily/2026-08-05.html", webhook.call_args.args[2])

        with patch("digest.send_feishu_image", side_effect=RuntimeError("upload failed")), patch(
            "digest.send_feishu"
        ) as webhook:
            notify_feishu(
                image_path=image_path,
                report_url="https://example.com/daily/2026-08-05.html",
                app_id="app-id",
                app_secret="app-secret",
                chat_id="chat-id",
                webhook_url="hook",
                secret="secret",
            )
        self.assertIn("图片推送失败", webhook.call_args.args[2])

    def test_workflows_install_renderer_and_expose_feishu_app_secrets(self):
        for workflow_name in ("daily.yml", "weekly.yml"):
            workflow = (Path(".github") / "workflows" / workflow_name).read_text(encoding="utf-8")
            self.assertIn("actions/setup-node", workflow)
            self.assertIn("npm ci", workflow)
            self.assertIn("playwright install", workflow)
            self.assertIn("FEISHU_APP_ID", workflow)
            self.assertIn("FEISHU_APP_SECRET", workflow)
            self.assertIn("FEISHU_CHAT_ID", workflow)

    def test_editorial_png_smoke(self):
        from digest import render_report_png

        if not Path("node_modules/playwright").exists():
            self.skipTest("Playwright is not installed; run npm ci first")
        fixture = Path("tests/fixtures/sample-editorial.html")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "report.png"
            render_report_png(fixture, output)
            payload = output.read_bytes()

        self.assertGreater(len(payload), 100)
        self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")

    def test_write_report_preserves_archive_outputs(self):
        report = {
            "overview": "本期判断。",
            "sections": [
                {
                    "name": "关键变化",
                    "items": [
                        {
                            "title": "条目标题",
                            "url": "https://example.com/article",
                            "source": "Example",
                            "summary": "发生了什么。",
                            "why": "为什么重要。",
                            "action": "你现在做什么。",
                            "importance": 4,
                        }
                    ],
                }
            ],
            "degraded": False,
        }
        now = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = write_report(
                report,
                root=Path(temp_dir),
                now=now,
                mode="daily",
                pages_base_url="https://example.github.io/digest",
            )
            self.assertTrue(bundle.markdown_path.exists())
            self.assertTrue(bundle.html_path.exists())
            self.assertTrue(bundle.json_path.exists())
            self.assertEqual(bundle.png_path.name, "2026-08-05.png")
            html_body = bundle.html_path.read_text(encoding="utf-8")
            self.assertNotIn("<a href=", html_body)
            index_body = (Path(temp_dir) / "docs" / "index.html").read_text(encoding="utf-8")
            self.assertIn("daily/2026-08-05.html", index_body)
            self.assertIn("daily/2026-08-05.png", index_body)
            ET.fromstring((Path(temp_dir) / "docs" / "feed.xml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
