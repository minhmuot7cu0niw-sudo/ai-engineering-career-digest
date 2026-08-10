# AI Engineering & Career Digest

面向 AI 工程学习与职业判断的个人速报：每日约 5 分钟阅读，每周日补充职业雷达。

## 输出内容

- 今日关键变化：模型、平台、政策和公司动作中的高影响事件。
- 工程实践：Codex、Agent、MCP、RAG、推理部署、AI 编程工具和重要开源项目。
- 学习优先级：论文或技术文章，以及值得学习的原因和下一步动作。
- 职业信号：只保留影响软件、AI、金融科技岗位技能或企业落地的内容。

完整报告发布到 `docs/`，可由 GitHub Pages 托管：同一天会保留 HTML、PNG、Markdown 和 JSON；`docs/feed.xml` 是 RSS 订阅源。飞书优先推送 PNG 长图，图片发送不可用时自动回退到带 Pages 地址的文本消息。

## GitHub 配置

在仓库 `Settings -> Secrets and variables -> Actions` 中添加以下 Secrets：

| Name | Value |
| --- | --- |
| `LLM_BASE_URL` | `https://api.aijws.com/v1` |
| `LLM_API_KEY` | 你的中转 API Key |
| `LLM_MODEL` | `gpt-5.6-sol` |
| `GITHUB_TOKEN` | 可选；Actions 自动提供，用于提高 GitHub API 限额 |

## 个人工具与项目侦察周报

周日推送不做 GitHub 热门榜复刻，而是从 GitHub API 与 Trending 候选中挑选最多 3 个真正适合当前用户的项目：一个值得安装的工具、一个适合学习或作品集的项目、一个值得继续观察的新方向。没有合格候选时允许少推；已推荐项目会被排除，避免短期重复。
| `FEISHU_WEBHOOK_URL` | 飞书自定义机器人 Webhook |
| `FEISHU_SECRET` | 飞书机器人签名密钥 |
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_CHAT_ID` | 接收图片的群聊 Chat ID |

密钥不要提交到仓库、Issue、日志或报告中。

在 `Settings -> Pages` 中将部署来源设置为 **GitHub Actions**。要启用飞书图片，需要在飞书开放平台创建自建应用，开启机器人能力，把应用加入目标群聊，并为应用配置发送消息权限；然后把 App ID、App Secret 和目标群聊 Chat ID 写入上表。Webhook 仍建议保留，作为图片发送失败时的文本兜底。

之后可在 `Actions` 页面手动运行 `Daily AI Digest` 验证配置；定时任务按 UTC 执行，日报约为北京时间 08:00，周报约为北京时间周日 09:00。

## 本地验证

项目使用 Python 标准库和固定版本的 Playwright：

```bash
python -m unittest discover -s tests -v
npm ci
npx playwright install chromium
python digest.py --mode daily
```

没有配置 API Key 时会生成降级报告；没有配置完整的飞书应用凭据时会保留 PNG 和 Pages 归档，并在 Webhook 可用时发送文本兜底。
