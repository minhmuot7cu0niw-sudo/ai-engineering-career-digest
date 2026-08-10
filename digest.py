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
import subprocess
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
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

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


@dataclass(frozen=True)
class GitHubProject:
    full_name: str
    url: str
    description: str
    stars: int
    pushed_at: str
    language: str = ""
    topics: tuple[str, ...] = ()
    source: str = "GitHub API"


@dataclass(frozen=True)
class ReportBundle:
    markdown_path: Path
    html_path: Path
    json_path: Path
    png_path: Path


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
    digest = hmac.new(payload, digestmod=hashlib.sha256).digest()
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


def http_get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 25) -> dict[str, Any]:
    request_headers = {"Accept": "application/vnd.github+json", **(headers or {})}
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub response must be an object")
    return payload


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


def http_post_multipart(
    url: str,
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    headers: dict[str, str],
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    boundary = "----ai-digest-" + hashlib.sha256(str(time.time_ns()).encode("ascii")).hexdigest()[:20]
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    filename = file_path.name or "digest.png"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"),
            b"Content-Type: image/png\r\n\r\n",
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request_headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "ai-engineering-career-digest/1.0",
        **headers,
    }
    request = urllib.request.Request(url, data=b"".join(chunks), headers=request_headers, method="POST")
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


def _github_project_from_item(item: dict[str, Any], source: str) -> GitHubProject | None:
    full_name = str(item.get("full_name") or item.get("name") or "").strip()
    url = str(item.get("html_url") or item.get("url") or "").strip()
    if not full_name or not url:
        return None
    topics = item.get("topics") or []
    return GitHubProject(
        full_name=full_name,
        url=url,
        description=str(item.get("description") or "").strip(),
        stars=int(item.get("stargazers_count") or item.get("stars") or 0),
        pushed_at=str(item.get("pushed_at") or item.get("updated_at") or ""),
        language=str(item.get("language") or ""),
        topics=tuple(str(topic) for topic in topics if topic),
        source=source,
    )


def fetch_github_projects(now: datetime, *, token: str = "") -> list[GitHubProject]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=90)
    headers = {"User-Agent": "ai-engineering-career-digest/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    pushed_since = cutoff.date().isoformat()
    queries = (
        f"topic:ai OR topic:agent OR topic:developer-tools pushed:>={pushed_since}",
        f"codex OR mcp OR llm OR developer productivity pushed:>={pushed_since}",
        f"topic:automation OR topic:knowledge-management pushed:>={pushed_since}",
    )
    projects: list[GitHubProject] = []
    for query in queries:
        try:
            params = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 30})
            payload = http_get_json(f"https://api.github.com/search/repositories?{params}", headers=headers)
            for item in payload.get("items", []):
                if not isinstance(item, dict):
                    continue
                project = _github_project_from_item(item, "GitHub API")
                if project and project.pushed_at and parse_datetime(project.pushed_at) >= cutoff:
                    projects.append(project)
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("GitHub API source failed (%s)", exc)
    return dedupe_github_projects(projects)


def fetch_github_trending(*, token: str = "") -> list[GitHubProject]:
    try:
        payload = http_get("https://github.com/trending?since=weekly").decode("utf-8", "ignore")
    except (OSError, urllib.error.URLError) as exc:
        LOGGER.warning("GitHub Trending source failed (%s)", exc)
        return []
    projects: list[GitHubProject] = []
    headers = {"User-Agent": "ai-engineering-career-digest/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for match in re.finditer(r"<article[^>]+Box-row[\s\S]*?</article>", payload, re.IGNORECASE):
        block = match.group(0)
        repo_match = re.search(r"href=\"/([^\"]+/[^\"]+)\"", block)
        stars_match = re.search(r"([\d,]+)\s+stars", block, re.IGNORECASE)
        if not repo_match:
            continue
        full_name = html.unescape(repo_match.group(1)).strip()
        description = re.sub(r"<[^>]+>", " ", block)
        description = re.sub(r"\s+", " ", html.unescape(description)).strip()[:400]
        try:
            detail = http_get_json(f"https://api.github.com/repos/{full_name}", headers=headers)
            project = _github_project_from_item(detail, "GitHub Trending")
            if project:
                projects.append(project)
                continue
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("GitHub Trending detail failed for %s (%s)", full_name, exc)
        projects.append(GitHubProject(full_name, f"https://github.com/{full_name}", description, int((stars_match.group(1) if stars_match else "0").replace(",", "")), "", source="GitHub Trending"))
    return dedupe_github_projects(projects)


def dedupe_github_projects(projects: Iterable[GitHubProject]) -> list[GitHubProject]:
    seen: set[str] = set()
    result: list[GitHubProject] = []
    for project in projects:
        key = project.full_name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(project)
    return result


PROJECT_SCOUT_SECTIONS = ("本周值得安装", "学习与作品集", "值得观察")


def load_previous_project_urls(root: Path) -> set[str]:
    """Return projects already used in an earlier personal scout report."""
    urls: set[str] = set()
    paths = list((root / "docs" / "weekly-data").glob("*.json"))
    paths.extend((root / "docs" / "data").glob("*.json"))
    for path in paths:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        sections = report.get("sections", []) if isinstance(report, dict) else []
        is_scout = report.get("report_type") == "project_scout"
        if not is_scout:
            is_scout = any(
                isinstance(item, dict) and item.get("maturity")
                for section in sections if isinstance(section, dict)
                for item in section.get("items", []) if isinstance(item, dict)
            )
        if not is_scout:
            continue
        for section in sections:
            for item in section.get("items", []) if isinstance(section, dict) else []:
                if isinstance(item, dict) and item.get("url"):
                    urls.add(canonical_url(str(item["url"])))
    return urls


def build_project_prompt(projects: list[GitHubProject]) -> str:
    candidates = [asdict(project) for project in projects[:50]]
    return f"""You are editing a personal tools and projects scouting weekly report for one user. Select at most 3 projects, never fill a slot with a weak recommendation.
This is not a GitHub popularity chart. The user wants practical value: better Codex usage, AI coding, developer productivity, personal workflow, AI engineering learning, career portfolio, and useful non-AI tools are all valid.
Use at most one project in each section: 本周值得安装, 学习与作品集, 值得观察. Omit a section when no candidate honestly qualifies.
Hard rules: recent activity within 90 days is required; do not invent activity, stars, features, or fit. Prefer maintained, runnable projects. A lower-star project is allowed only when fit is unusually high.
For each selected project write Chinese, substantive but concise fields: title, url, source, summary (what it is and problem solved), why (specific fit and how it improves on the user's likely current method), action (whether to try now and the first step this week), maturity (maintenance, usability and practical risk), importance (1-5).
Return strict JSON only: {{"overview":"...","sections":[{{"name":"本周值得安装","items":[{{"title":"","url":"","source":"","summary":"","why":"","action":"","maturity":"","importance":5}}]}},{{"name":"学习与作品集","items":[]}},{{"name":"值得观察","items":[]}}],"degraded":false}}
CANDIDATES:
{json.dumps(candidates, ensure_ascii=False)}"""


def build_project_report(projects: list[GitHubProject], *, base_url: str, api_key: str, model: str) -> dict[str, Any]:
    if not projects:
        return {"overview": "本周没有获得可验证的 GitHub 候选项目。", "sections": [{"name": "本周最值得试", "items": []}], "degraded": True}
    try:
        raw = call_responses(base_url, api_key, model, build_project_prompt(projects)) if api_key else ""
        parsed = parse_model_json(raw) if raw else {}
        sections = parsed.get("sections")
        if not isinstance(sections, list):
            raise ValueError("project report missing sections")
        normalized: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for name in PROJECT_SCOUT_SECTIONS:
            section = next((value for value in sections if isinstance(value, dict) and value.get("name") == name), None)
            items = section.get("items", []) if section else []
            item = next((value for value in items if isinstance(value, dict) and value.get("url")), None)
            if not item:
                continue
            url = canonical_url(str(item["url"]))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            normalized.append({"name": name, "items": [item]})
        if not normalized:
            raise ValueError("project report contains no usable recommendation")
        return {"overview": str(parsed.get("overview", "")), "sections": normalized, "degraded": False, "report_type": "project_scout"}
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("project model unavailable; using verified fallback (%s)", exc)
        sections = []
        for name, project in zip(PROJECT_SCOUT_SECTIONS, projects[:3]):
            item = {"title": project.full_name, "url": project.url, "source": project.source, "summary": project.description or "GitHub project", "why": "候选项目满足近期活跃条件，但与现有方法的具体差异仍需人工复核。", "action": "先阅读 README 并运行最小示例，再决定是否纳入长期工作流。", "maturity": f"{project.stars:,} stars；来源：{project.source}", "importance": 3}
            sections.append({"name": name, "items": [item]})
        return {"overview": "模型不可用；以下仅列出已验证的候选，个性化适配度需要人工复核。", "sections": sections, "degraded": True, "report_type": "project_scout"}


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
        "max_output_tokens": 2400,
    }
    response = http_post_json(endpoint, payload, {"Authorization": f"Bearer {api_key}"})
    text = extract_response_text(response)
    if not text:
        raise ValueError("Responses API returned no text")
    return text


def build_prompt(articles: list[Article], mode: str) -> str:
    compact = [
        {"title": a.title, "url": a.url, "source": a.source, "published": a.published, "summary": a.summary[:300]}
        for a in articles[:24]
    ]
    return f"""你是个人 AI 工程与职业速报编辑。当前模式：{mode}。
只从给定条目中选择，不得编造事实；保留原文 URL。
日报需分为：今日关键变化、工程实践、学习优先级、职业信号；职业周报需重点分析岗位技能、招聘趋势和企业落地。
每条必须给出对读者的实际影响或下一步动作。今日关键变化最多 3 条，工程实践最多 4 条，学习优先级最多 2 条，职业信号最多 2 条。输出严格 JSON，不要 Markdown 代码围栏：
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
                    f"- 成熟度与风险：{item.get('maturity', '')}" if item.get("maturity") else "",
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


def _display_text(value: Any, fallback: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return text or fallback


def render_editorial_html(report: dict[str, Any], *, title: str, generated_at: str, mode: str) -> str:
    """Render the link-free editorial page used as the source for the daily PNG."""

    def esc(value: Any, fallback: str = "") -> str:
        return html.escape(_display_text(value, fallback))

    sections_html: list[str] = []
    for index, section in enumerate(report.get("sections", []), start=1):
        if not isinstance(section, dict):
            continue
        name = esc(section.get("name"), "未分类")
        items_html: list[str] = []
        for item_index, item in enumerate(section.get("items", []), start=1):
            if not isinstance(item, dict):
                continue
            source = esc(item.get("source"), "未标注来源")
            title_text = esc(item.get("title"), "未命名条目")
            summary = esc(item.get("summary"), "暂无摘要。")
            why = esc(item.get("why"), "暂未给出重要性判断。")
            action = esc(item.get("action"), "暂未给出下一步动作。")
            maturity = esc(item.get("maturity"))
            if maturity:
                action = f"{action} 成熟度与风险：{maturity}"
            importance = esc(item.get("importance"), "-")
            items_html.append(
                f'''<article class="brief-item">
  <div class="item-topline"><span class="item-number">{index:02d}.{item_index:02d}</span><span class="item-source">{source}</span><span class="item-importance">重要度 {importance}</span></div>
  <h3>{title_text}</h3>
  <div class="item-grid">
    <div class="item-field"><span class="field-label">发生了什么</span><p>{summary}</p></div>
    <div class="item-field"><span class="field-label">为什么重要</span><p>{why}</p></div>
    <div class="item-field action-field"><span class="field-label">你现在做什么</span><p>{action}</p></div>
  </div>
</article>'''
            )
        if not items_html:
            items_html.append('<p class="empty-section">今天没有纳入这一栏的条目。</p>')
        sections_html.append(
            f'''<section class="brief-section">
  <div class="section-heading"><span class="section-number">{index:02d}</span><h2>{name}</h2></div>
  {"".join(items_html)}
</section>'''
        )

    degraded_note = ""
    if report.get("degraded"):
        degraded_note = '<p class="status-note">模型摘要暂不可用，本期保留已抓取条目，重要性判断需人工复核。</p>'
    overview = esc(report.get("overview"), "本期没有额外判断。")
    mode_label = "每日版" if mode == "daily" else "周报版"
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #17212b;
      --muted: #66727d;
      --line: rgba(35, 53, 67, .16);
      --accent: #d96b3b;
      --paper: rgba(255, 255, 255, .88);
      --paper-strong: rgba(255, 255, 255, .96);
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: #e9eff2; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: Inter, "Noto Sans CJK SC", "Noto Sans SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at 1px 1px, rgba(42, 65, 80, .15) 1px, transparent 1.35px) 0 0 / 18px 18px,
        linear-gradient(180deg, #eef3f5 0%, #f8fafb 48%, #e9eff2 100%);
    }}
    .page {{ max-width: 1080px; margin: 0 auto; padding: 54px 48px 68px; }}
    .masthead {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 28px; align-items: end; padding-bottom: 28px; border-bottom: 1px solid var(--line); }}
    .eyebrow {{ color: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }}
    h1 {{ margin: 10px 0 0; max-width: none; font-size: clamp(34px, 4.2vw, 48px); line-height: 1.08; letter-spacing: 0; }}
    .meta {{ color: var(--muted); font-size: 14px; line-height: 1.6; text-align: right; white-space: nowrap; }}
    .meta strong {{ display: block; color: var(--ink); font-size: 16px; }}
    .judgment {{ margin: 34px 0 46px; padding: 24px 28px; border: 1px solid var(--line); border-left: 5px solid var(--accent); background: var(--paper-strong); box-shadow: 0 12px 30px rgba(30, 49, 62, .08); }}
    .judgment-label, .field-label {{ color: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }}
    .judgment p {{ margin: 10px 0 0; font-size: 21px; line-height: 1.55; font-weight: 650; }}
    .status-note {{ margin: 12px 0 0; color: #8b4c2e; font-size: 13px; }}
    .brief-section {{ margin-top: 42px; }}
    .section-heading {{ display: flex; align-items: baseline; gap: 14px; margin-bottom: 16px; }}
    .section-number {{ color: var(--accent); font-size: 14px; font-weight: 850; letter-spacing: .12em; }}
    h2 {{ margin: 0; font-size: 26px; line-height: 1.2; }}
    .brief-item {{ margin: 0 0 18px; padding: 22px 24px 24px; background: var(--paper); border: 1px solid var(--line); box-shadow: 0 8px 24px rgba(30, 49, 62, .06); }}
    .item-topline {{ display: flex; gap: 12px; align-items: center; color: var(--muted); font-size: 12px; }}
    .item-number {{ color: var(--accent); font-weight: 800; }}
    .item-source {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .item-importance {{ margin-left: auto; }}
    h3 {{ margin: 12px 0 18px; font-size: 23px; line-height: 1.35; }}
    .item-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }}
    .item-field {{ min-width: 0; padding-top: 12px; border-top: 1px solid var(--line); }}
    .item-field p {{ margin: 8px 0 0; color: #33414c; font-size: 15px; line-height: 1.72; }}
    .action-field {{ padding: 12px 14px 0; border: 1px solid rgba(217, 107, 59, .28); background: rgba(217, 107, 59, .07); }}
    .empty-section {{ margin: 0; color: var(--muted); font-size: 14px; }}
    .footer {{ margin-top: 54px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; line-height: 1.6; }}
    @media (max-width: 760px) {{
      .page {{ padding: 34px 20px 46px; }}
      .masthead {{ display: block; }}
      .meta {{ margin-top: 16px; text-align: left; white-space: normal; }}
      .item-grid {{ grid-template-columns: 1fr; gap: 14px; }}
      h1 {{ font-size: 38px; }}
      .judgment p {{ font-size: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <header class="masthead">
      <div><div class="eyebrow">AI Engineering / Career Brief</div><h1>{esc(title)}</h1></div>
      <div class="meta"><strong>{mode_label}</strong>{esc(generated_at)}</div>
    </header>
    <section class="judgment"><div class="judgment-label">今日判断</div><p>{overview}</p>{degraded_note}</section>
    {"".join(sections_html)}
    <footer class="footer">本页只呈现经过筛选和摘要的判断，不在正文展开外部链接；完整来源索引保留在同日期归档数据中。</footer>
  </main>
</body>
</html>'''


def render_report_png(html_path: Path, png_path: Path) -> Path:
    """Render an editorial HTML file to a full-page PNG with the pinned Node tool."""

    root = Path(__file__).resolve().parent
    png_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "node",
        "scripts/render_report.mjs",
        "--input",
        str(html_path),
        "--output",
        str(png_path),
    ]
    try:
        subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"PNG rendering failed for {html_path}") from exc
    return png_path


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


def _require_feishu_success(response: dict[str, Any], operation: str) -> None:
    code = response.get("code", response.get("StatusCode", 0))
    if str(code) not in {"0", "None"}:
        raise ValueError(f"Feishu {operation} failed with code {code}")


def get_feishu_tenant_access_token(app_id: str, app_secret: str) -> str:
    response = http_post_json(
        f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        {},
    )
    _require_feishu_success(response, "token request")
    token = response.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Feishu token response did not include tenant_access_token")
    return token


def send_feishu_image(app_id: str, app_secret: str, chat_id: str, image_path: Path) -> None:
    token = get_feishu_tenant_access_token(app_id, app_secret)
    upload_response = http_post_multipart(
        f"{FEISHU_API_BASE}/im/v1/images",
        {"image_type": "message"},
        "image",
        image_path,
        {"Authorization": f"Bearer {token}"},
    )
    _require_feishu_success(upload_response, "image upload")
    data = upload_response.get("data") if isinstance(upload_response.get("data"), dict) else {}
    image_key = data.get("image_key") or upload_response.get("image_key")
    if not isinstance(image_key, str) or not image_key:
        raise ValueError("Feishu image upload did not return image_key")
    message_response = http_post_json(
        f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
        {
            "receive_id": chat_id,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
        },
        {"Authorization": f"Bearer {token}"},
    )
    _require_feishu_success(message_response, "image message")


def notify_feishu(
    *,
    image_path: Path,
    report_url: str,
    app_id: str,
    app_secret: str,
    chat_id: str,
    webhook_url: str,
    secret: str,
) -> None:
    text = f"AI 工程与职业速报已生成：{report_url}"
    if app_id and app_secret and chat_id:
        try:
            send_feishu_image(app_id, app_secret, chat_id, image_path)
            return
        except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
            LOGGER.warning("Feishu image delivery failed; falling back to text: %s", exc)
            text = f"图片推送失败，已回退到文本消息：{report_url}"
    if webhook_url and secret:
        send_feishu(webhook_url, secret, text)
    elif app_id or app_secret or chat_id:
        LOGGER.warning("Feishu notification skipped because credentials are incomplete")


def report_page_url(pages_base_url: str, report_path: Path, *, cache_buster: int) -> str:
    base_url = pages_base_url.rstrip("/")
    archive_name = report_path.parent.name
    if archive_name in {"", "."}:
        archive_name = "daily"
    return f"{base_url}/{archive_name}/{report_path.stem}.html?v={cache_buster}"


def write_report(
    report: dict[str, Any], *, root: Path, now: datetime, mode: str, pages_base_url: str
) -> ReportBundle:
    docs = root / "docs"
    archive_dir = docs / ("weekly" if mode == "weekly" else "daily")
    data_dir = docs / ("weekly-data" if mode == "weekly" else "data")
    archive_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    date_text = now.astimezone(LOCAL_ZONE).date().isoformat()
    title = f"个人工具与项目侦察周报 · {date_text}" if mode == "weekly" else f"AI 工程与职业速报 · {date_text}"
    generated_at = now.astimezone(LOCAL_ZONE).strftime("%Y-%m-%d %H:%M:%S CST")
    markdown = render_markdown(report, title=title, generated_at=generated_at, mode=mode)
    markdown_path = archive_dir / f"{date_text}.md"
    html_path = archive_dir / f"{date_text}.html"
    json_path = data_dir / f"{date_text}.json"
    png_path = archive_dir / f"{date_text}.png"
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(
        render_editorial_html(report, title=title, generated_at=generated_at, mode=mode),
        encoding="utf-8",
    )
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if mode == "daily":
        items = report_items(report)
        feed = render_rss(items, title="AI 工程与职业速报", link=pages_base_url)
        (docs / "feed.xml").write_text(feed, encoding="utf-8")
    latest_daily = sorted((docs / "daily").glob("*.html"))
    latest_weekly = sorted((docs / "weekly").glob("*.html"))
    daily_link = f'<p><a href="daily/{latest_daily[-1].name}">最新日报 HTML</a> · <a href="daily/{latest_daily[-1].stem}.png">PNG 长图</a></p>' if latest_daily else ""
    weekly_link = f'<p><a href="weekly/{latest_weekly[-1].name}">最新个人侦察周报 HTML</a> · <a href="weekly/{latest_weekly[-1].stem}.png">PNG 长图</a></p>' if latest_weekly else ""
    index = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>AI 工程与职业速报</title><style>body{{max-width:760px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui}}a{{color:#0b57d0}}</style></head><body><h1>AI 工程与职业速报</h1><p>面向 AI 工程学习、职业判断和个人工具发现的专属速报。</p><p><a href=\"feed.xml\">订阅日报 RSS</a></p><h2>最新归档</h2>{daily_link}{weekly_link}</body></html>"""
    (docs / "index.html").write_text(index, encoding="utf-8")
    return ReportBundle(
        markdown_path=markdown_path,
        html_path=html_path,
        json_path=json_path,
        png_path=png_path,
    )


def run(mode: str) -> ReportBundle:
    root = Path(__file__).resolve().parent
    now = datetime.now(timezone.utc)
    base_url = os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL
    model = os.getenv("LLM_MODEL") or DEFAULT_MODEL
    api_key = os.getenv("LLM_API_KEY", "")
    if mode == "weekly":
        github_token = os.getenv("GITHUB_TOKEN", "")
        projects = dedupe_github_projects(fetch_github_projects(now, token=github_token) + fetch_github_trending(token=github_token))
        cutoff = now.astimezone(timezone.utc) - timedelta(days=90)
        projects = [project for project in projects if project.pushed_at and parse_datetime(project.pushed_at) >= cutoff]
        previous_urls = load_previous_project_urls(root)
        projects = [project for project in projects if canonical_url(project.url) not in previous_urls]
        report = build_project_report(projects, base_url=base_url, api_key=api_key, model=model)
    else:
        articles = filter_articles(collect_articles(), now=now, hours=48, keywords=KEYWORDS)
        report = build_report(articles, mode=mode, base_url=base_url, api_key=api_key, model=model)
    pages_base_url = os.getenv("PAGES_BASE_URL", "https://example.github.io/ai-engineering-career-digest")
    bundle = write_report(report, root=root, now=now, mode=mode, pages_base_url=pages_base_url)
    render_report_png(bundle.html_path, bundle.png_path)
    report_url = report_page_url(pages_base_url, bundle.html_path, cache_buster=int(now.timestamp()))
    notify_feishu(
        image_path=bundle.png_path,
        report_url=report_url,
        app_id=os.getenv("FEISHU_APP_ID", ""),
        app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        chat_id=os.getenv("FEISHU_CHAT_ID", ""),
        webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
        secret=os.getenv("FEISHU_SECRET", ""),
    )
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "weekly"), default="daily")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    bundle = run(args.mode)
    print(f"report written: {bundle.html_path}")


if __name__ == "__main__":
    main()
