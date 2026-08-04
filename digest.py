#!/usr/bin/env python3
"""Collect, summarize, publish, and notify for the personal AI digest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger("digest")
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_BASE_URL = "https://api.aijws.com/v1"

GITHUB_REPOS = (
    "openai/codex",
    "anthropics/claude-code",
    "QwenLM/qwen-code",
    "anomalyco/opencode",
    "modelcontextprotocol/servers",
    "langchain-ai/langchain",
    "langchain-ai/langgraph",
    "huggingface/transformers",
    "vllm-project/vllm",
    "ollama/ollama",
)

RSS_FEEDS = (
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
)

KEYWORDS = (
    "ai",
    "agent",
    "llm",
    "rag",
    "mcp",
    "inference",
    "reasoning",
    "retrieval",
    "model",
    "transformer",
    "copilot",
    "codex",
)


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published: str
    summary: str = ""


def parse_datetime(value: str) -> datetime:
    value = (value or "").strip()
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    ignored = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "fbclid"}
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query) if key not in ignored]
    return urllib.parse.urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(query), "")
    )


def dedupe_articles(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    result: list[Article] = []
    for article in articles:
        key = canonical_url(article.url)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def filter_articles(
    articles: Iterable[Article],
    *,
    now: datetime,
    hours: int,
    keywords: Iterable[str] = (),
) -> list[Article]:
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=hours)
    terms = tuple(term.lower() for term in keywords if term)
    result: list[Article] = []
    for article in articles:
        published = parse_datetime(article.published)
        if published < cutoff or published > now.astimezone(timezone.utc) + timedelta(minutes=10):
            continue
        if terms:
            haystack = f"{article.title} {article.summary}".lower()
            if not any(term in haystack for term in terms):
                continue
        result.append(article)
    return sorted(result, key=lambda item: parse_datetime(item.published), reverse=True)


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response does not contain a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model response JSON must be an object")
    return parsed


def sign_feishu_request(timestamp: str, secret: str) -> str:
    payload = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def render_rss(articles: Iterable[Article], *, title: str, link: str) -> str:
    items = []
    for article in articles:
        items.append(
            "<item>"
            f"<title>{html.escape(article.title)}</title>"
            f"<link>{html.escape(article.url, quote=True)}</link>"
            f"<guid isPermaLink=\"true\">{html.escape(article.url, quote=True)}</guid>"
            f"<description>{html.escape(article.summary)}</description>"
            f"<source url=\"{html.escape(article.url, quote=True)}\">{html.escape(article.source)}</source>"
            f"<pubDate>{html.escape(article.published)}</pubDate>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>{html.escape(title)}</title><link>{html.escape(link, quote=True)}</link>"
        "<description>AI 工程学习与职业判断速报</description>"
        + "".join(items)
        + "</channel></rss>\n"
    )


def http_get(url: str, *, timeout: int = 25) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ai-engineering-career-digest/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], *, timeout: int = 60) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json", "User-Agent": "ai-engineering-career-digest/1.0", **headers}
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("API response must be a JSON object")
    return parsed


def _element_text(parent: ET.Element, names: Iterable[str]) -> str:
    wanted = set(names)
    for child in parent.iter():
        local = child.tag.rsplit("}", 1)[-1]
        if local in wanted and child.text:
            return child.text.strip()
    return ""


def parse_feed(payload: bytes, source: str) -> list[Article]:
    root = ET.fromstring(payload)
    articles: list[Article] = []
    for item in root.iter():
        local = item.tag.rsplit("}", 1)[-1]
        if local not in {"item", "entry"}:
            continue
        url = _element_text(item, ("link",))
        if not url:
            link = next((child for child in item if child.tag.rsplit("}", 1)[-1] == "link"), None)
            url = link.attrib.get("href", "") if link is not None else ""
        title = _element_text(item, ("title",))
        summary = _element_text(item, ("description", "summary", "content"))
        published = _element_text(item, ("pubDate", "published", "updated", "date"))
        if title and url:
            articles.append(Article(title, url, source, published, re.sub(r"\s+", " ", summary)))
    return articles


def fetch_feed(url: str, source: str) -> list[Article]:
    try:
        return parse_feed(http_get(url), source)
    except (ET.ParseError, OSError, urllib.error.URLError, ValueError) as exc:
        LOGGER.warning("source failed: %s (%s)", source, exc)
        return []


def fetch_hacker_news() -> list[Article]:
    url = "https://hn.algolia.com/api/v1/search_by_date?tags=story&query=AI&hitsPerPage=20"
    try:
        payload = json.loads(http_get(url))
        return [
            Article(
                hit.get("title") or hit.get("story_title") or "",
                hit.get("url") or hit.get("story_url") or "",
                "Hacker News",
                hit.get("created_at", ""),
                hit.get("title") or hit.get("story_title") or "",
            )
            for hit in payload.get("hits", [])
            if (hit.get("url") or hit.get("story_url")) and (hit.get("title") or hit.get("story_title"))
        ]
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("source failed: Hacker News (%s)", exc)
        return []


def fetch_arxiv() -> list[Article]:
    query = urllib.parse.quote("cat:cs.AI OR cat:cs.CL OR cat:cs.LG")
    url = f"https://export.arxiv.org/api/query?search_query={query}&start=0&max_results=20&sortBy=submittedDate&sortOrder=descending"
    return fetch_feed(url, "ArXiv")


def fetch_github_releases() -> list[Article]:
    articles: list[Article] = []
    for repo in GITHUB_REPOS:
        articles.extend(fetch_feed(f"https://github.com/{repo}/releases.atom", f"GitHub Release: {repo}"))
    return articles


def collect_articles() -> list[Article]:
    articles: list[Article] = []
    for source, url in RSS_FEEDS:
        articles.extend(fetch_feed(url, source))
    articles.extend(fetch_github_releases())
    articles.extend(fetch_hacker_news())
    articles.extend(fetch_arxiv())
    return dedupe_articles(articles)


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    chunks: list[str] = []
    for output in response.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks)


def call_responses(base_url: str, api_key: str, model: str, prompt: str) -> str:
    endpoint = base_url.rstrip("/") + "/responses"
    payload = {
        "model": model,
        "input": prompt,
        "temperature": 0.2,
        "max_output_tokens": 4000,
    }
    response = http_post_json(endpoint, payload, {"Authorization": f"Bearer {api_key}"})
    text = extract_response_text(response)
    if not text:
        raise ValueError("Responses API returned no text")
    return text


def build_prompt(articles: list[Article], mode: str) -> str:
    compact = [
        {"title": a.title, "url": a.url, "source": a.source, "published": a.published, "summary": a.summary[:500]}
        for a in articles[:80]
    ]
    return f"""你是个人 AI 工程与职业速报编辑。当前模式：{mode}。
只从给定条目中选择，不得编造事实；保留原文 URL。
日报需分为：今日关键变化、工程实践、学习优先级、职业信号；职业周报需重点分析岗位技能、招聘趋势和企业落地。
每条必须给出对读者的实际影响或下一步动作。输出严格 JSON，不要 Markdown 代码围栏：
{{"overview":"整体判断","sections":[{{"name":"今日关键变化","items":[{{"title":"","url":"","source":"","summary":"","why":"","action":"","importance":1}}]}}]}}
条目：
{json.dumps(compact, ensure_ascii=False)}"""


def fallback_sections(articles: list[Article], mode: str) -> list[dict[str, Any]]:
    limits = {"今日关键变化": 3, "工程实践": 4, "学习优先级": 2, "职业信号": 2}
    sections = [{"name": name, "items": []} for name in limits]
    for index, article in enumerate(articles[: sum(limits.values())]):
        section = sections[min(index // 3, len(sections) - 1)]
        section["items"].append(
            {
                "title": article.title,
                "url": article.url,
                "source": article.source,
                "summary": article.summary or "原文摘要待补充。",
                "why": "该条目来自已配置的 AI 工程信息源，值得快速判断是否与你当前学习方向相关。",
                "action": "打开原文，确认是否值得加入本周学习清单。",
                "importance": 3,
            }
        )
    return sections


def build_report(articles: list[Article], *, mode: str, base_url: str, api_key: str, model: str) -> dict[str, Any]:
    if not articles:
        return {"overview": "本期没有通过时间和关键词筛选的条目。", "sections": fallback_sections([], mode), "degraded": True}
    try:
        raw = call_responses(base_url, api_key, model, build_prompt(articles, mode)) if api_key else ""
        parsed = parse_model_json(raw) if raw else {}
        sections = parsed.get("sections")
        if not isinstance(sections, list):
            raise ValueError("model response missing sections")
        return {"overview": str(parsed.get("overview", "")), "sections": sections, "degraded": False}
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("model unavailable; using fallback report (%s)", exc)
        return {
            "overview": "模型调用暂不可用，本期保留原始条目供人工筛选。",
            "sections": fallback_sections(articles, mode),
            "degraded": True,
        }


def report_items(report: dict[str, Any]) -> list[Article]:
    items: list[Article] = []
    for section in report.get("sections", []):
        for item in section.get("items", []) if isinstance(section, dict) else []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            items.append(Article(str(item.get("title", "")), str(item["url"]), str(item.get("source", "")), "", str(item.get("summary", ""))))
    return items


def render_markdown(report: dict[str, Any], *, title: str, generated_at: str, mode: str) -> str:
    lines = [f"# {title}", "", f"> 生成时间：{generated_at}  |  模式：{mode}", ""]
    if report.get("degraded"):
        lines.extend(["> 本期模型调用未完成，以下内容为降级原始条目；请以原文为准。", ""])
    lines.extend(["## 今日判断", "", str(report.get("overview", "")), ""])
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        lines.extend([f"## {section.get('name', '未分类')}", ""])
        for item in section.get("items", []):
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"### [{item.get('title', '未命名')}]({item.get('url', '')})",
                    "",
                    f"- 来源：{item.get('source', '')}",
                    f"- 摘要：{item.get('summary', '')}",
                    f"- 为什么重要：{item.get('why', '')}",
                    f"- 下一步：{item.get('action', '')}",
                    "",
                ]
            )
    return "\n".join(lines)


def render_html(markdown: str, *, title: str) -> str:
    paragraphs = []
    for line in markdown.splitlines():
        if line.startswith("# "):
            paragraphs.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            paragraphs.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            match = re.match(r"### \[(.*?)\]\((.*?)\)", line)
            if match:
                paragraphs.append(f'<h3><a href="{html.escape(match.group(2), quote=True)}">{html.escape(match.group(1))}</a></h3>')
        elif line.startswith("- "):
            paragraphs.append(f"<p>{html.escape(line[2:])}</p>")
        elif line.startswith("> "):
            paragraphs.append(f"<blockquote>{html.escape(line[2:])}</blockquote>")
        elif line.strip() and not line.startswith("|"):
            paragraphs.append(f"<p>{html.escape(line)}</p>")
    body = "\n".join(paragraphs)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{max-width:760px;margin:40px auto;padding:0 20px;color:#202124;font:16px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif}}
h1{{font-size:2rem;line-height:1.25}}h2{{margin-top:2.2rem;border-left:4px solid #111;padding-left:10px}}
h3{{font-size:1.15rem;margin-bottom:.2rem}}a{{color:#0b57d0}}blockquote{{color:#5f6368;border-left:3px solid #dadce0;padding-left:12px}}
</style></head><body>{body}</body></html>"""


def send_feishu(webhook_url: str, secret: str, text: str) -> None:
    timestamp = str(int(time.time()))
    payload = {
        "timestamp": timestamp,
        "sign": sign_feishu_request(timestamp, secret),
        "msg_type": "text",
        "content": {"text": text},
    }
    response = http_post_json(webhook_url, payload, {})
    code = response.get("code", response.get("StatusCode", 0))
    if str(code) not in {"0", "None"}:
        raise ValueError(f"Feishu rejected message: {response}")


def write_report(report: dict[str, Any], *, root: Path, now: datetime, mode: str, pages_base_url: str) -> Path:
    docs = root / "docs"
    daily = docs / "daily"
    data_dir = docs / "data"
    daily.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    date_text = now.astimezone(LOCAL_ZONE).date().isoformat()
    title = f"AI 工程与职业速报 · {date_text}"
    generated_at = now.astimezone(LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S CST")
    markdown = render_markdown(report, title=title, generated_at=generated_at, mode=mode)
    (daily / f"{date_text}.md").write_text(markdown, encoding="utf-8")
    (daily / f"{date_text}.html").write_text(render_html(markdown, title=title), encoding="utf-8")
    (data_dir / f"{date_text}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    items = report_items(report)
    feed = render_rss(items, title="AI 工程与职业速报", link=pages_base_url)
    (docs / "feed.xml").write_text(feed, encoding="utf-8")
    index = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>AI 工程与职业速报</title><style>body{{max-width:760px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui}}a{{color:#0b57d0}}</style></head><body><h1>AI 工程与职业速报</h1><p>面向 AI 工程学习与职业判断的个人速报。</p><p><a href=\"feed.xml\">订阅 RSS</a></p><h2>最新一期</h2><p><a href=\"daily/{date_text}.html\">{html.escape(title)}</a></p></body></html>"""
    (docs / "index.html").write_text(index, encoding="utf-8")
    return daily / f"{date_text}.md"


def run(mode: str) -> Path:
    root = Path(__file__).resolve().parent
    now = datetime.now(timezone.utc)
    hours = 24 * 7 if mode == "weekly" else 48
    articles = filter_articles(collect_articles(), now=now, hours=hours, keywords=KEYWORDS)
    base_url = os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL
    model = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    report = build_report(articles, mode=mode, base_url=base_url, api_key=os.getenv("LLM_API_KEY", ""), model=model)
    pages_base_url = os.getenv("PAGES_BASE_URL", "https://example.github.io/ai-engineering-career-digest")
    path = write_report(report, root=root, now=now, mode=mode, pages_base_url=pages_base_url)
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "")
    secret = os.getenv("FEISHU_SECRET", "")
    if webhook and secret:
        message = f"{path.stem} AI 工程与职业速报已生成：{pages_base_url}/daily/{path.stem}.html"
        send_feishu(webhook, secret, message)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "weekly"), default="daily")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = run(args.mode)
    print(f"report written: {path}")


if __name__ == "__main__":
    main()
