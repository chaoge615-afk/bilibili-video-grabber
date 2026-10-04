#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video-grabber —— B站视频快速抓取工具（独立版，不入库）
====================================================
链路: 视频链接/BVID → 下载音频 → 可配置的云 ASR 转写
      → 可配置的 OpenAI 兼容 LLM 精炼 → 输出 Markdown

特性:
  * 文件输出: 结果仅写入 .md 文件
  * 云ASR转写: 使用外部配置的兼容接口，不需要本地显卡/Whisper
  * 精炼可选: 未配置 REFINE_API_KEY 时自动跳过精炼，只产出完整转写
  * 独立可跑: 不依赖其他项目模块

用法:
  python grab.py BV1xxxxxx [BV2yyyyyy ...]          # 直接给 BVID
  python grab.py https://www.bilibili.com/video/BV1xxxxxx
  python grab.py --file links.txt                    # 每行一个 BVID/链接
  python grab.py BV1xxxxxx --keep-audio --no-refine
"""

import argparse
import os
import re
import shutil
import sys
import time
import json
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
COOKIE_TMP = PROJECT_DIR / ".bili_cookie_tmp.txt"

# ─── 环境/配置 ──────────────────────────────────────────────────────

def load_dotenv_simple(dotenv_path: Path) -> None:
    """极简 .env 加载（KEY=VALUE 行，避免额外依赖 python-dotenv）"""
    try:
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and not os.environ.get(key):
                os.environ[key] = val
    except OSError:
        pass


def resolve_cookies(args) -> str | None:
    """解析 Cookie，返回 yt-dlp 可用的 cookie 文件路径（无 cookie 返回 None）。

    优先级: --cookie-file > 环境变量 BILIBILI_COOKIE (文件路径或 Netscape 文本内容)
    """
    if args.cookie_file:
        p = Path(args.cookie_file).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"--cookie-file 不存在: {p}")
        return str(p)

    env_cookie = os.getenv("BILIBILI_COOKIE", "")
    if not env_cookie:
        return None

    # 环境变量指向已存在的文件 → 直接用
    p = Path(env_cookie).expanduser()
    if p.exists():
        return str(p)

    # 否则当作 Netscape cookie 文本内容，写入临时文件（项目目录内，用完即删）
    COOKIE_TMP.write_text(env_cookie.strip(), encoding="utf-8")
    print(f"  [cookie] 已从 BILIBILI_COOKIE 写入临时文件 {COOKIE_TMP.name}")
    return str(COOKIE_TMP)


# ─── 下载 ───────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """去除文件名非法字符"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip()
    return name[:120] or "untitled"


def normalize_target(t: str) -> str:
    """BVID / av号 / 完整链接 → 视频 URL"""
    t = t.strip()
    if not t:
        raise ValueError("空目标")
    if t.startswith(("http://", "https://")):
        return t
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", t):
        return f"https://www.bilibili.com/video/{t}"
    if re.fullmatch(r"av\d+", t, re.IGNORECASE):
        return f"https://www.bilibili.com/video/{t.lower()}"
    return f"https://www.bilibili.com/video/{t}"


def download_audio(url: str, out_dir: Path, cookie_file: str | None):
    """
    下载视频音频（bestaudio）。
    返回: (info 元数据 dict, 音频文件路径)
    """
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    has_ffmpeg = shutil.which("ffmpeg") is not None

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 60,
        "retries": 3,
        "cookiefile": cookie_file,
        # B站风控: 必须带浏览器 UA/Referer，否则 HTTP 412
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        },
        "postprocessors": ([
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"}
        ] if has_ffmpeg else []),
    }

    print(f"  下载音频: {url}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("yt-dlp 未返回视频信息")

        # 定位音频文件: 优先 m4a（FFmpeg 提取），否则原始后缀
        base = Path(ydl.prepare_filename(info))
        candidates = [base.with_suffix(".m4a"), base]
        audio_path = next((p for p in candidates if p.exists()), None)
        if audio_path is None:
            # 兜底: 按 BVID 模糊匹配下载目录
            vid = str(info.get("id", ""))
            matches = sorted(out_dir.glob(f"*{vid}*"))
            audio_path = matches[0] if matches else None
        if audio_path is None:
            raise RuntimeError(f"找不到下载的音频文件（BVID={vid}）")

    print(f"  ✓ 音频: {audio_path.name} ({audio_path.stat().st_size/1024/1024:.1f} MB)")
    return info, audio_path


# ─── 云 ASR 转写（兼容 multipart /audio/transcriptions 接口） ─────────


def transcribe_asr(audio_path: Path, api_key: str, model: str,
                   api_url: str, timeout: int = 300,
                   max_file_size: int | None = None) -> str:
    """
    单文件云 ASR 转写（POST multipart）。返回转写纯文本。

    参考主项目 bilibili-monitor/src/transcribe_asr.py
    API 文档: https://api-docs.siliconflow.cn/docs/api/audio-transcriptions-post
    """
    import requests

    size = audio_path.stat().st_size
    if max_file_size and size > max_file_size:
        raise ValueError(f"音频 {size/1024/1024:.1f}MB > {max_file_size/1024/1024:.1f}MB 接口限制，无法云转写")

    headers = {"Authorization": f"Bearer {api_key}"}
    with open(audio_path, "rb") as f:
        files = {"file": (audio_path.name, f), "model": (None, model)}
        print(f"  云ASR转写中（{model}，{audio_path.stat().st_size/1024/1024:.1f} MB）...")
        resp = requests.post(api_url, headers=headers, files=files, timeout=timeout)

    if resp.status_code != 200:
        raise RuntimeError(f"ASR API 错误 (HTTP {resp.status_code}): {resp.text[:200]}")

    text = resp.json().get("text", "")
    if not text:
        raise RuntimeError("ASR 返回空文本")
    return text.strip()


# ─── 精炼 ───────────────────────────────────────────────────────────

REFINE_PROMPT = """你是一个内容精炼助手。请将下面的视频转写文本精炼成详细、结构化的干货笔记。

【精炼要求】
1. 去噪: 忽略转写中的语气词、口误、重复句、ASR错别字(按上下文纠正)、表情符号(🎼😡等)、与主题无关的寒暄(如"关注我""点个赞""下期讲什么""之前讲过很多遍"等)。
2. 尽量详细: 完整保留视频中讲到的每一个具体方法、步骤、技巧、案例和数据，能展开就展开，不要压缩丢失细节。宁可长，不可漏。
3. 忠实原意: 如实记录视频传达的观点和建议，不要用你自己的价值观改写或评判。

【输出格式】(严格按此结构)
**核心观点**
一句话概括视频最核心的主张。

**具体方法**
逐条列出视频讲到的所有具体做法，保留操作细节和关键例子（每条一个要点，数量不限，务必详尽）。

**案例与数据**
视频中引用的案例、故事、数据（没有则写"无"）。

**原文建议**
视频给出的建议或行动号召，逐条列出。

【原始素材】
"""

# 精炼输出必须包含的标题（用于校验）
REQUIRED_HEADERS = ["**核心观点**", "**具体方法**", "**案例与数据**", "**原文建议**"]


def _clean_response(text: str) -> str:
    """清理 LLM 可能输出的思考过程标签"""
    text = re.sub(r"<thinking>[\s\S]*?</thinking>", "", text)
    text = re.sub(r"<Thought>[\s\S]*?</Thought>", "", text)
    text = re.sub(r"【思考】[\s\S]*?【/思考】", "", text)
    return text.strip()


def _validate_output(text: str) -> bool:
    return all(h in text for h in REQUIRED_HEADERS)


def strip_asr_noise(text: str) -> str:
    """去除 ASR 转写噪音：表情符号、状态标签和多余空白。

    只做符号层清洗，不删字词（字词层纠错交给精炼 LLM）。
    """
    # 通用 emoji/符号区
    emoji_pattern = re.compile(
        "[\U0001F000-\U0001FAFF"
        "\u2600-\u27BF"
        "\uFE0F"
        "\u2190-\u21FF"
        "\u2B00-\u2BFF"
        "\u2300-\u23FF]"
    )
    text = emoji_pattern.sub("", text)
    # 压缩多余空白与空行
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def refine_text(raw_text: str, api_key: str, api_url: str, model: str,
                max_raw: int = 5000, max_tokens: int = 2500,
                retries: int = 2, sleep: int = 30) -> str | None:
    """OpenAI 兼容 LLM 精炼，失败重试，返回精炼 markdown 或 None。"""
    prompt = REFINE_PROMPT + raw_text[:max_raw]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            result = _clean_response(data["choices"][0]["message"]["content"].strip())
            if _validate_output(result):
                return result
            print(f"  ⚠️ 精炼格式不完整，第 {attempt+1} 次重试")
        except Exception as e:
            print(f"  ⚠️ LLM 调用失败: {e}（第 {attempt+1} 次）")
        if attempt < retries:
            time.sleep(sleep)
    return None


# ─── 输出 md ────────────────────────────────────────────────────────

def fmt_duration(sec: int | float | None) -> str:
    if not sec:
        return "未知"
    sec = int(sec)
    return f"{sec//60:02d}:{sec%60:02d}"


def fmt_views(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "未知"
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return str(n)


def fmt_date(upload_date: str | None) -> str:
    """yt-dlp upload_date(YYYYMMDD) → YYYY-MM-DD"""
    if upload_date and len(upload_date) == 8:
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
    return "未知"


def write_md(out_dir: Path, info: dict, transcript: str, summary: str | None,
             audio_path: Path | None = None) -> Path:
    """写出 markdown: 元信息 + 精炼摘要 + 完整转写"""
    title = info.get("title") or "未命名"
    bvid = info.get("id") or ""
    url = info.get("webpage_url") or f"https://www.bilibili.com/video/{bvid}"

    md = [
        f"# {title}",
        "",
        f"> 源链接: {url}",
        "",
        "| 项目 | 值 |",
        "|------|-----|",
        f"| BVID | {bvid} |",
        f"| 发布 | {fmt_date(info.get('upload_date'))} |",
        f"| UP主 | {info.get('uploader') or '未知'} |",
        f"| 时长 | {fmt_duration(info.get('duration'))} |",
        f"| 播放 | {fmt_views(info.get('view_count'))} |",
        "",
    ]

    if summary:
        md += [
            "## 🎯 精炼摘要",
            "",
            summary,
            "",
        ]

    md += [
        "## 📝 完整转写",
        "",
        transcript.strip() or "（转写为空）",
        "",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    # title 已含 [BVID] 时避免重复追加
    if bvid and f"[{bvid}]" in title:
        md_name = title
    else:
        md_name = f"{title} [{bvid}]".strip() if bvid else title
    md_file = out_dir / f"{sanitize_filename(md_name)}.md"
    md_file.write_text("\n".join(md), encoding="utf-8")
    return md_file


# ─── 主流程 ─────────────────────────────────────────────────────────

def process_one(target: str, args, out_dir: Path, cookie_file: str | None,
                refine_cfg: dict) -> bool:
    url = normalize_target(target)
    print(f"\n{'='*60}\n▶ {url}\n{'='*60}")

    # 1. 下载音频
    info, audio_path = download_audio(url, out_dir, cookie_file)

    # 2. 时长过滤
    duration = info.get("duration") or 0
    if args.min_duration and duration and duration < args.min_duration:
        print(f"  ⏭️ 时长 {fmt_duration(duration)} < {args.min_duration}s，跳过")
        if not args.keep_audio:
            audio_path.unlink(missing_ok=True)
        return False

    # 3. 云 ASR 转写
    if not refine_cfg["asr_key"]:
        print("  ✗ 未配置完整 ASR 接口，无法转写")
        return False
    transcript = transcribe_asr(
        audio_path, refine_cfg["asr_key"], args.asr_model, args.asr_url,
        args.asr_timeout,
        int(args.asr_max_file_mb * 1024 * 1024) if args.asr_max_file_mb else None,
    )
    transcript = strip_asr_noise(transcript)  # 去 ASR 表情标签等噪音
    print(f"  ✓ 转写完成（{len(transcript)} 字，已去噪）")

    # 4. 精炼
    summary = None
    if not args.no_refine:
        if refine_cfg["refine_key"]:
            print(f"  精炼中（{refine_cfg['refine_model']}）...")
            summary = refine_text(transcript, refine_cfg["refine_key"],
                                  refine_cfg["refine_url"], refine_cfg["refine_model"])
            if summary:
                print("  ✓ 精炼完成")
            else:
                print("  ⚠️ 精炼失败，md 只含完整转写")
        else:
            print("  ⚠️ 未配置 REFINE_API_KEY，跳过精炼（只输出完整转写）")

    # 5. 输出 md
    md_file = write_md(out_dir, info, transcript, summary)
    print(f"  ✓ 已写入: {md_file.name}")

    # 6. 清理音频
    if not args.keep_audio:
        audio_path.unlink(missing_ok=True)
        print(f"  🗑️ 已删除音频（--keep-audio 可保留）")
    return True


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # 加载项目目录下 .env（若有）
    load_dotenv_simple(PROJECT_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="B站视频快速抓取 → 云ASR转写 → 精炼 → Markdown（不入库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python grab.py BV1xxxxxx BV2yyyyyy\n"
            "  python grab.py https://www.bilibili.com/video/BV1xxxxxx\n"
            "  python grab.py --file links.txt\n"
            "  python grab.py BV1xxxxxx --keep-audio --no-refine\n"
        ),
    )
    parser.add_argument("targets", nargs="*", help="BVID 或视频链接，可多个")
    parser.add_argument("--file", help="每行一个 BVID/链接 的文本文件")
    parser.add_argument("--out-dir", default=str(PROJECT_DIR / "output"), help="md 输出目录 (默认 ./output)")
    parser.add_argument("--min-duration", type=int, default=0, help="跳过时长小于该秒数的视频 (默认 0 不过滤)")
    parser.add_argument("--keep-audio", action="store_true", help="保留音频文件")
    parser.add_argument("--no-refine", action="store_true", help="跳过 LLM 精炼")
    parser.add_argument("--cookie-file", default="", help="Netscape 格式 cookie 文件路径")
    # 云 ASR（兼容的 multipart 转写接口）
    parser.add_argument("--asr-key", default="", help="ASR API Key（默认读 ASR_API_KEY）")
    parser.add_argument("--asr-model", default=os.getenv("ASR_MODEL", ""), help="ASR 模型（默认读 ASR_MODEL）")
    parser.add_argument("--asr-url", default=os.getenv("ASR_API_URL", ""), help="ASR 转写接口（默认读 ASR_API_URL）")
    parser.add_argument("--asr-timeout", type=int, default=int(os.getenv("ASR_TIMEOUT", "300")), help="ASR 请求超时秒数（默认 300）")
    parser.add_argument("--asr-max-file-mb", type=float, default=float(os.getenv("ASR_MAX_FILE_MB", "0")), help="ASR 单文件上限 MB（0 表示不预检）")
    # 精炼（OpenAI 兼容 Chat Completions 接口）
    parser.add_argument("--refine-key", default="", help="精炼 API Key（默认读 REFINE_API_KEY 环境变量）")
    parser.add_argument("--refine-url", default=os.getenv("REFINE_API_URL", ""), help="精炼接口（默认读 REFINE_API_URL）")
    parser.add_argument("--refine-model", default=os.getenv("REFINE_MODEL", ""), help="精炼模型（默认读 REFINE_MODEL）")
    args = parser.parse_args()

    # 汇总目标
    targets = list(args.targets)
    if args.file:
        f = Path(args.file).expanduser()
        if not f.exists():
            print(f"✗ --file 不存在: {f}")
            sys.exit(1)
        targets += [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    targets = [t.strip() for t in targets if t.strip()]
    if not targets:
        parser.print_help()
        sys.exit(1)

    # 解析 Cookie
    try:
        cookie_file = resolve_cookies(args)
    except Exception as e:
        print(f"✗ Cookie 解析失败: {e}")
        sys.exit(1)
    if cookie_file:
        print(f"✓ 使用 Cookie: {Path(cookie_file).name}")
    else:
        print("⚠️ 未配置 Cookie（BILIBILI_COOKIE 或 --cookie-file），部分视频可能下载受限")

    # API Key
    if args.asr_max_file_mb < 0:
        parser.error("--asr-max-file-mb 不能小于 0")
    asr_key = args.asr_key or os.getenv("ASR_API_KEY", "")
    asr_ready = bool(asr_key and args.asr_url and args.asr_model)
    if asr_ready:
        print(f"✓ 云ASR: {args.asr_model} @ {args.asr_url}")
    else:
        print("✗ ASR 配置不完整：需提供 ASR_API_KEY、ASR_API_URL、ASR_MODEL")
        sys.exit(1)
    refine_key = args.refine_key or os.getenv("REFINE_API_KEY", "")
    if refine_key and args.refine_url and args.refine_model:
        print(f"✓ 精炼 API: {args.refine_model} @ {args.refine_url}")
    elif not args.no_refine and any((refine_key, args.refine_url, args.refine_model)):
        print("⚠️ 精炼配置不完整：需提供 REFINE_API_KEY、REFINE_API_URL、REFINE_MODEL，将跳过精炼")

    refine_cfg = {
        "asr_key": asr_key,
        "refine_key": refine_key if args.refine_url and args.refine_model else "",
        "refine_url": args.refine_url,
        "refine_model": args.refine_model,
    }

    out_dir = Path(args.out_dir).expanduser().resolve()
    print(f"输出目录: {out_dir}")

    ok, fail = 0, 0
    for i, t in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}]")
        try:
            if process_one(t, args, out_dir, cookie_file, refine_cfg):
                ok += 1
        except Exception as e:
            fail += 1
            print(f"✗ 处理失败: {t}\n  {e}")
        time.sleep(2)  # 请求间隔，避免风控

    if COOKIE_TMP.exists():
        COOKIE_TMP.unlink(missing_ok=True)

    print(f"\n{'='*60}\n✅ 完成: {ok} 成功, {fail} 失败\n📁 输出: {out_dir}\n{'='*60}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
