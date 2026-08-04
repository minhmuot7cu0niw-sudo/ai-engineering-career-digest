# AI Engineering & Career Digest

面向 AI 工程学习与职业判断的个人速报：每日约 5 分钟阅读，每周日补充职业雷达。

## 输出内容

- 今日关键变化：模型、平台、政策和公司动作中的高影响事件。
- 工程实践：Codex、Agent、MCP、RAG、推理部署、AI 编程工具和重要开源项目。
- 学习优先级：论文或技术文章，以及值得学习的原因和下一步动作。
- 职业信号：只保留影响软件、AI、金融科技岗位技能或企业落地的内容。

完整报告发布到 `docs/`，可由 GitHub Pages 托管；`docs/feed.xml` 是 RSS 订阅源。每次报告写入成功后，工作流再向飞书群机器人推送链接。

## GitHub 配置

在仓库 `Settings -> Secrets and variables -> Actions` 中添加以下 Secrets：

| Name | Value |
| --- | --- |
| `LLM_BASE_URL` | `https://api.aijws.com/v1` |
| `LLM_API_KEY` | 你的中转 API Key |
| `LLM_MODEL` | `gpt-5.6-sol` |
| `FEISHU_WEBHOOK_URL` | 飞书自定义机器人 Webhook |
| `FEISHU_SECRET` | 飞书机器人签名密钥 |

密钥不要提交到仓库、Issue、日志或报告中。

在 `Settings -> Pages` 中将部署来源设置为 **GitHub Actions**。之后可在 `Actions` 页面手动运行 `Daily AI Digest` 验证配置；定时任务按 UTC 执行，日报约为北京时间 08:00，周报约为北京时间周日 09:00。

## 本地验证

项目只使用 Python 标准库：

```bash
python -m unittest discover -s tests -v
python digest.py --mode daily
```

没有配置 API Key 时会生成降级报告，仅用于验证采集、网页和 RSS 输出。
