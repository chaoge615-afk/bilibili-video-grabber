---
name: bilibili-video-grabber
description: Download a user-supplied Bilibili video, transcribe its audio through a configured cloud ASR endpoint, optionally refine it through a configured OpenAI-compatible LLM endpoint, and save an auditable Markdown note. Use when the user wants information extracted from supplied Bilibili videos; not for browsing feeds or building a long-running monitor.
metadata:
  short-description: Bilibili video to Markdown notes
---

# Bilibili Video Grabber

Turn explicitly supplied BVIDs or Bilibili video URLs into Markdown notes containing source metadata, an optional structured summary, and the full ASR transcript.

## Configure

- Confirm the user supplied the target BVIDs/URLs and wants them processed. Do not discover or scrape unrelated videos.
- Never print secret values. A Netscape-format Bilibili cookie may be supplied through `BILIBILI_COOKIE` or `--cookie-file` for login-restricted content; do not bypass access controls.
- `ffmpeg` on `PATH` is recommended for m4a extraction. Install the Python requirements if `yt-dlp` or `requests` is unavailable:

  ```powershell
  <python-command> -m pip install -r <skill-directory>\requirements.txt
  ```

  Use `python` when available; otherwise try `py -3`.

Set ASR configuration through environment variables or the matching command-line options. All three are required:

| Environment variable | Command-line option | Purpose |
|---|---|---|
| `ASR_API_KEY` | `--asr-key` | ASR credential |
| `ASR_API_URL` | `--asr-url` | ASR endpoint |
| `ASR_MODEL` | `--asr-model` | ASR model |
| `ASR_TIMEOUT` | `--asr-timeout` | Request timeout in seconds |
| `ASR_MAX_FILE_MB` | `--asr-max-file-mb` | Upload precheck limit; `0` disables it |

The ASR service must accept multipart fields `file` and `model`, and return JSON containing `text`.

Refinement is optional. When used, configure `REFINE_API_KEY`, `REFINE_API_URL`, and `REFINE_MODEL` (or `--refine-key`, `--refine-url`, and `--refine-model`). The endpoint must accept the OpenAI Chat Completions request shape and return `choices[0].message.content`.

## Run

Keep generated files outside the skill folder. Unless the user chooses another directory, pass an `output` directory under the current workspace.

```powershell
$env:ASR_API_KEY = '<provided securely>'
$env:ASR_API_URL = 'https://provider.example/v1/audio/transcriptions'
$env:ASR_MODEL = 'provider-asr-model'
$env:REFINE_API_KEY = '<optional provided securely>'
$env:REFINE_API_URL = 'https://provider.example/v1/chat/completions'
$env:REFINE_MODEL = 'provider-chat-model'
<python-command> <skill-directory>\scripts\grab.py <BVID-or-URL> --out-dir .\output
```

- Pass multiple targets as positional arguments or use `--file <path>` for one target per line.
- Use `--no-refine` for a transcript-only result.
- Use `--keep-audio` only when the user asks to retain downloaded media.
- Use `--min-duration <seconds>` to omit short videos.

## Results

Report each Markdown file path and any failed target. The document includes the source URL and full transcript so the summary can be checked. Respect the selected provider's limits; do not split or re-encode media without user direction. For Bilibili 412/-352 or access errors, explain that a valid user-provided cookie or a different network context may be needed; do not retry indefinitely.
