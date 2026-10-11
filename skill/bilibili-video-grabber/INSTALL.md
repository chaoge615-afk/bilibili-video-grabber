# Install this Skill

This folder follows the `SKILL.md` convention. Install the entire `bilibili-video-grabber` directory in the skills directory recognized by the target agent, or register that directory with the agent if it supports external skill paths. The host determines the exact location and how skills are enabled.

## Requirements

- Python 3.10 or later
- The packages listed in `requirements.txt` (`yt-dlp` and `requests`)
- `ffmpeg` on `PATH` for m4a audio extraction; downloads may still work without it
- Credentials for an ASR endpoint that accepts multipart `file` and `model` fields and returns JSON with a `text` field
- An optional supported LLM endpoint for summary refinement

Install Python dependencies in the environment that will run the Skill:

```sh
<python-command> -m pip install -r <skill-directory>/requirements.txt
```

Set the required `ASR_API_KEY`, `ASR_API_URL`, and `ASR_MODEL` variables in that environment. Set `REFINE_API_KEY`, `REFINE_API_URL`, and `REFINE_MODEL` to enable summaries, and optionally set `REFINE_API_PROTOCOL` to `chat-completions`, `responses`, or `anthropic-messages`.

The script can also read a `.env` file placed beside `scripts/grab.py`. Start from `config.example.env` if needed, but keep the populated `.env` private and out of source control or shared archives. `BILIBILI_COOKIE` is optional and should only contain cookies provided by the user.

When invoking the Skill, pass user-supplied BVIDs or video URLs and choose an output directory in the user's workspace. See `SKILL.md` for invocation options and result handling.
