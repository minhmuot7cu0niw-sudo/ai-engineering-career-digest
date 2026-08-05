# AI 工程与职业速报视觉长图改造 Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

Goal: 将现有日报改为同时产出 C 坐标点阵风格的响应式 HTML 与 PNG 长图，并在飞书直接推送 PNG、失败时保留 Webhook 文本兜底。

Architecture: 保留 digest.py 作为采集、模型调用和归档入口；将编辑型 HTML 渲染封装为纯函数，将 PNG 截图交给固定版本的 Playwright Chromium 脚本；飞书图片发送封装为独立的认证、上传和消息发送函数。GitHub Actions 负责安装 Node/Chromium、运行 Python 管线、提交 docs 并部署 Pages。

Tech Stack: Python 3.11 标准库；Node.js 22；Playwright 1.62.1；Chromium；GitHub Actions；飞书 Open API 与现有自定义机器人 Webhook。

## Global Constraints

- 底层纹理采用 C 方案：接近白色的浅底，加低对比度坐标点阵。
- 不显示没有真实来源的“第 N 期”；顶部不放仿模板时间。
- 长图正文不展示外部 URL；来源只显示编号和来源类别，完整索引留在归档页底部。
- 每条内容必须显示“发生了什么、为什么重要、你现在做什么”。
- GitHub Pages 同时保留日期 HTML、PNG、JSON 与 RSS。
- 飞书图片推送缺配置或失败时，必须发送明确的文本兜底，不得伪装成图片推送成功。
- 现有模型、信息源、日报/周报频率和降级报告语义保持兼容。

---

### Task 1: 建立编辑型报告模型与 HTML 渲染器

Files:
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\digest.py
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\tests\test_digest.py

Interfaces:
- Add render_editorial_html(report: dict[str, Any], *, title: str, generated_at: str, mode: str) -> str.
- Add ReportBundle with markdown_path, html_path, json_path and png_path, all typed as Path.
- Change write_report to return ReportBundle and preserve Markdown, JSON, RSS and index outputs.

- [ ] Step 1: Add a fixture report test. Assert the renderer contains low-contrast dot-pattern CSS, the four section labels, the visible labels “发生了什么”“为什么重要”“你现在做什么”, escaped text, and no item-title anchor or external href in the editorial body.
- [ ] Step 2: Run python -m unittest tests.test_digest.DigestTests.test_render_editorial_html_contract -v. Expected: FAIL because the renderer and ReportBundle do not exist.
- [ ] Step 3: Implement the renderer directly from the report JSON. Emit a masthead, today-judgment block, numbered sections, source labels, titles, three labeled paragraphs and action blocks. Escape all model text and keep source URLs out of the visual body. Update write_report to write the new HTML and return all four paths.
- [ ] Step 4: Run the focused test, then python -m unittest discover -s tests -v. Expected: PASS.
- [ ] Step 5: Commit with git add digest.py tests/test_digest.py followed by git commit -m "feat: render editorial digest pages".

---

### Task 2: Add deterministic Chromium PNG rendering

Files:
- Create: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\package.json
- Create: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\package-lock.json
- Create: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\scripts\render_report.mjs
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\digest.py
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\tests\test_digest.py

Interfaces:
- Node accepts --input HTML and --output PNG.
- Add render_report_png(html_path: Path, png_path: Path) -> Path.
- run calls PNG rendering after write_report and before Feishu notification.

- [ ] Step 1: Patch subprocess.run in a unit test and assert the command includes node, scripts/render_report.mjs, --input, --output and check=True.
- [ ] Step 2: Run python -m unittest tests.test_digest.DigestTests.test_render_report_png_invocation -v. Expected: FAIL.
- [ ] Step 3: Pin Playwright 1.62.1. The Node script parses arguments, launches Chromium headless, sets viewport width 1080 and device scale factor 1, waits for document.fonts.ready, captures fullPage=true, closes in finally, and exits nonzero for invalid arguments or output errors. Python reports input/output paths on failure.
- [ ] Step 4: Run the focused Python test and node --check scripts/render_report.mjs. Expected: PASS and exit code 0.
- [ ] Step 5: Commit with git add package.json package-lock.json scripts/render_report.mjs digest.py tests/test_digest.py followed by git commit -m "feat: render digest PNG with Playwright".

---

### Task 3: Implement Feishu app image upload and safe fallback

Files:
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\digest.py
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\tests\test_digest.py

Interfaces:
- Add http_post_multipart(url: str, fields: dict[str, str], file_field: str, file_path: Path, headers: dict[str, str], *, timeout: int = 60) -> dict[str, Any].
- Add get_feishu_tenant_access_token(app_id: str, app_secret: str) -> str.
- Add send_feishu_image(app_id: str, app_secret: str, chat_id: str, image_path: Path) -> None.
- Add notify_feishu that prefers image delivery when app credentials and chat id are complete and otherwise uses the existing signed Webhook text.

- [ ] Step 1: Mock HTTP helpers and test token retrieval, multipart image_type=message upload, image_key extraction, image-message JSON content, missing-credential fallback, and image-failure Webhook fallback.
- [ ] Step 2: Run python -m unittest tests.test_digest.DigestTests.test_send_feishu_image_contract -v. Expected: FAIL.
- [ ] Step 3: Implement tenant-token retrieval, multipart upload and image-message creation. Raise ValueError for nonzero API codes or missing token/image_key. Never log secrets or Authorization headers. The fallback text includes the Pages URL and an explicit image-delivery failure marker.
- [ ] Step 4: Run the focused test and python -m unittest discover -s tests -v. Expected: PASS.
- [ ] Step 5: Commit with git add digest.py tests/test_digest.py followed by git commit -m "feat: push digest images to Feishu".

---

### Task 4: Wire GitHub Actions and configuration documentation

Files:
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\.github\workflows\daily.yml
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\.github\workflows\weekly.yml
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\README.md
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\tests\test_digest.py

Interfaces:
- Workflows install Node 22, run npm ci, install Chromium with dependencies, then run the existing daily/weekly Python entry point.
- New secrets are FEISHU_APP_ID, FEISHU_APP_SECRET and FEISHU_CHAT_ID; existing LLM and Webhook secrets remain.

- [ ] Step 1: Add a validation test that loads both workflow files as text and checks setup-node, npm ci, playwright install and all three Feishu app environment names.
- [ ] Step 2: Run python -m unittest tests.test_digest.DigestTests.test_workflows_install_renderer_and_expose_feishu_app_secrets -v. Expected: FAIL.
- [ ] Step 3: Add setup-node with node-version 22, npm ci and npx playwright install --with-deps chromium after checkout/setup-python. Add the three app secrets to the digest environment while preserving Pages upload/deploy steps.
- [ ] Step 4: Document the one-time Feishu self-built app prerequisite, app secrets, chat id, PNG fallback behavior and local validation commands without real credentials.
- [ ] Step 5: Run the validation test, then git add .github/workflows/daily.yml .github/workflows/weekly.yml README.md tests/test_digest.py and git commit -m "chore: configure image rendering in workflows".

---

### Task 5: Add fixture-based rendering smoke test and finalize archive behavior

Files:
- Create: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\tests\fixtures\sample-editorial.html
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\tests\test_digest.py
- Modify: D:\Workspace\Project-023-AI工程与职业速报\03-交付成果\ai-engineering-career-digest\digest.py

Interfaces:
- Fixture is a static C-texture editorial page with the four section labels and one action block.
- Smoke test runs the real Node renderer when Playwright is installed and skips with an explicit reason otherwise.

- [ ] Step 1: Create sample-editorial.html and test that a temporary PNG exists, has nonzero bytes and begins with PNG signature bytes 89 50 4E 47.
- [ ] Step 2: Run python -m unittest tests.test_digest.DigestTests.test_editorial_png_smoke -v. Expected: local SKIP before dependency installation, PASS after npm and Chromium setup.
- [ ] Step 3: Assert write_report writes date HTML and date PNG paths, index.html links to HTML, feed.xml remains valid, and no visual report title link is added to the editorial body.
- [ ] Step 4: Run python -m unittest discover -s tests -v; python -m py_compile digest.py tests/test_digest.py; node --check scripts/render_report.mjs; npm ci; npx playwright install chromium; python -m unittest tests.test_digest.DigestTests.test_editorial_png_smoke -v. Expected: all tests PASS and PNG has nonzero dimensions and bytes.
- [ ] Step 5: Commit with git add digest.py tests/test_digest.py tests/fixtures/sample-editorial.html followed by git commit -m "test: verify editorial digest image output".

---

## Plan Self-Review

- Spec coverage: C texture and editorial layout are Tasks 1 and 5; the three-layer content contract is Task 1; PNG generation is Tasks 2 and 5; Feishu image delivery and fallback are Task 3; Pages, workflow and secret wiring are Task 4; no-link body and source index are Tasks 1 and 5.
- Placeholder scan: no TBD, TODO or vague implementation step remains; paths, function names, commands and expected outcomes are explicit.
- Type consistency: ReportBundle is introduced in Task 1 and consumed by PNG rendering and Feishu notification in Tasks 2-3; function names and return types match across tasks.
