# video-grabber

B站视频快速抓取工具（独立版，不入库）：**视频链接 → 下载音频 → 可配置云 ASR 转写 → 可配置 LLM 精炼 → 输出 Markdown**。

用于按需提取单个或少量 B 站视频内容。ASR 和精炼服务均由外部配置，不需要本地显卡或 Whisper 模型。

## 核心链路

```
BVID / 链接
   │
   ▼ ① yt-dlp 下载 bestaudio（ffmpeg 提取 m4a，可选 cookie）
   ▼ ② 配置的 ASR 服务 POST /audio/transcriptions
   ▼ ③ 配置的 LLM 精炼：转写前 5000 字 → 详细干货笔记（核心观点 / 具体方法 / 案例与数据 / 原文建议）
   ▼ ④ 写 Markdown：元信息表 + 精炼摘要 + 完整转写
   ▼
output/<标题> [BVID].md
```

## 安装

```bash
pip install -r requirements.txt   # yt-dlp, requests
# ffmpeg 需在 PATH（用于音频提取），没有也能下但可能不是 m4a
```

## 配置

复制 `.env.example` 为 `.env`（脚本会自动加载项目目录下的 `.env`），或直接用环境变量 / 命令行参数：

| 变量 | 用途 | 必填 |
|------|------|------|
| `ASR_API_KEY` | 云 ASR Key | 是（否则无法转写） |
| `ASR_API_URL` / `ASR_MODEL` | ASR 转写端点与模型 | 是 |
| `ASR_TIMEOUT` / `ASR_MAX_FILE_MB` | ASR 超时秒数与单文件上限（0 不预检） | 否（默认 300 / 0） |
| `REFINE_API_KEY` | 精炼 LLM Key | 否（缺省只出转写） |
| `REFINE_API_URL` / `REFINE_MODEL` | 精炼端点与模型 | 与 `REFINE_API_KEY` 一起配置 |
| `REFINE_API_PROTOCOL` | `chat-completions`、`responses` 或 `anthropic-messages` | 否（默认 `chat-completions`） |
| `BILIBILI_COOKIE` | B站 Netscape cookie，文本或文件路径 | 否（公开视频可不配） |

> ASR 服务需接受 `multipart/form-data` 的 `file` 和 `model` 字段，并返回 JSON `text`；精炼服务支持 OpenAI Chat Completions、Responses 或 Anthropic Messages 协议。

## 用法

```bash
# 直接给 BVID（可多个）
python grab.py BV1xxxxxx BV2yyyyyy

# 给完整链接
python grab.py https://www.bilibili.com/video/BV1xxxxxx

# 从文件批量（每行一个）
python grab.py --file links.txt

# 常用选项
python grab.py BV1xxxxxx --keep-audio      # 保留音频（默认转完即删）
python grab.py BV1xxxxxx --no-refine       # 跳过精炼，只要转写
python grab.py BV1xxxxxx --min-duration 60 # 跳过短于 60s 的视频
python grab.py BV1xxxxxx --cookie-file ~/.bilibili/cookie.txt
python grab.py BV1xxxxxx --asr-url https://example.com/v1/audio/transcriptions --asr-model my-asr-model
python grab.py BV1xxxxxx --refine-url https://example.com/v1/chat/completions --refine-model my-llm
```

## 输出示例

```markdown
# 视频标题
> 源链接: https://www.bilibili.com/video/BV1xxxxxx

| 项目 | 值 |
|------|-----|
| BVID | BV1xxxxxx |
| 发布 | 2026-05-01 |
| UP主 | 桃姐不emo- |
| 时长 | 12:34 |
| 播放 | 20.5万 |

## 🎯 精炼摘要

**核心观点**
...

**案例摘要**
...

**可行动建议**
...

## 📝 完整转写

（全文）
```

## 注意事项

- **文件限制**：以所选 ASR 服务的规则为准。设置 `ASR_MAX_FILE_MB` 或 `--asr-max-file-mb` 可在上传前预检；0 表示不预检。
- **风控 412**：yt-dlp 请求已内置浏览器 UA/Referer/Origin（默认 412 可直接过）。若仍遇 -352/412，多半是 IP 风控或视频需登录，配 `BILIBILI_COOKIE`（浏览器导出 Netscape 格式）后重试即可；SESSDATA 过期同理。
- **精炼截断**：只取转写前 5000 字喂 LLM，完整转写仍全文写入 md。
- **精炼去噪**：转写先剥离 ASR 表情标签（🎼😡 等）；精炼 prompt 要求忽略语气词/口误/寒暄，并尽量详细保留具体方法、案例与数据。
- **精炼重试**：输出必须含 `**核心观点**`/`**具体方法**`/`**案例与数据**`/`**原文建议**` 四个标题，格式不全会自动重试（最多 3 次）。
- **间歇**：多个视频之间有 2s 间隔，避免触发风控。
