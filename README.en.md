<div align="center">

# 🤖 HoloJarvis

**A Chinese voice butler for your Mac — just say "Jarvis" and it gets things done.**

Local speech recognition · any LLM (via your own gateway / DeepSeek / GPT…) · tool calling · cloned voice · Iron-Man-style holographic desk pet

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows-black.svg)](#-quick-start)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)

[简体中文](./README.md) · **English**

<img src="docs/demo.gif" width="440" alt="HoloJarvis demo" />

<sub>Demo: say "Jarvis" → listen → think → reply in a cloned voice; the HUD arc reactor shifts color by state</sub>

</div>

---

## ✨ What is this

HoloJarvis is a **Chinese-language voice assistant** for **macOS / Windows**, inspired by Iron Man's AI butler.
Say "Jarvis" to your computer and it wakes up, listens, understands what you want, calls tools to do it,
and answers you by voice — while a cyan holographic console pet floats on your desktop showing the time,
system telemetry, and live conversation captions.

> 🪟 It started as a macOS project (originally named `jarvis-mac`) and is now **cross-platform from a single codebase**:
> platform-specific bits (speech synthesis, screenshots, clipboard, media/volume, recycle bin, telemetry…) switch automatically, centralized in `jarvis/winops.py`.

Its brain talks to an **OpenAI-compatible API**, so you can plug in **any model through your own gateway**
(DeepSeek, GPT, Claude…) and switch on demand. Its voice can optionally use **GPT-SoVITS** to speak in a cloned voice.

> 💡 A personal project for tinkerers who want a desktop voice assistant that is *local-first* and *gets to know you over time*.

## 🌟 Features

- 🎙️ **Local speech recognition** — transcribed on-device with [faster-whisper](https://github.com/SYSTRAN/faster-whisper); your audio never leaves your machine.
- 🔑 **Fuzzy pinyin wake word** — say "贾维斯" (Jarvis); homophone mis-hearings still match, with noise/hallucination filtering so the TV won't trigger it.
- 🧠 **Any LLM** — connect DeepSeek / GPT / Claude etc. through your OpenAI-compatible gateway; change one line to swap models.
- 🧰 **23 built-in tools + MCP** — open apps, check weather, control music, read the screen, manage memory, review file changes, tidy files, set timers… and extend further via [MCP](https://modelcontextprotocol.io/).
- 🗣️ **Cloned voice** — optionally connect [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) to speak in a cloned voice; falls back to the system `say` voice if the service is offline.
- 🧬 **Long-term memory** — tell it "remember…" and it keeps your name, preferences, and habits across restarts.
- 🪟 **Holographic HUD pet** — an Iron-Man-style cyan console: an arc reactor that changes color by state, clock & weather, disk/battery/CPU telemetry, conversation captions, and a notes panel. Click the reactor to talk.
- 🔒 **Local-first** — recognition, the pet, and memory all run locally; the LLM goes through *your* gateway, and all secrets stay on your machine, never in the repo.

## 🧱 Architecture

```mermaid
flowchart LR
    Mic[🎙️ Microphone] --> VAD[VAD / segmentation]
    VAD --> ASR[faster-whisper ASR]
    ASR --> Wake{wake word?}
    Wake -- no --> Mic
    Wake -- yes --> Brain[🧠 Brain]
    Brain <--> LLM[(Gateway<br/>OpenAI-compatible)]
    Brain <--> Tools[🧰 local tools + MCP]
    Brain --> Mem[(long-term memory)]
    Brain --> TTS[🗣️ GPT-SoVITS / system voice]
    TTS --> Speaker[🔊 speak]
    Brain -.state/captions.-> Pet[🪟 HUD pet]
```

| Module | Files | Role |
|---|---|---|
| Main loop | `jarvis/__main__.py` | Wake word, state machine, wiring |
| Recognition | `jarvis/asr.py` `jarvis/audio.py` | Microphone + faster-whisper |
| Brain | `jarvis/brain.py` | Gateway calls, tool-calling loop, multi-step tasks |
| Tools | `jarvis/tools.py` `jarvis/mcp_bridge.py` | Local tools + MCP tools |
| Memory | `jarvis/memory.py` | SQLite `memory.db`; imports legacy `memory.json` |
| Tasks | `jarvis/tasks.py` | Local SQLite `tasks.db` task board |
| Voice | `jarvis/tts.py` | GPT-SoVITS cloned voice / system voice (say · SAPI) |
| Pet | `jarvis/pet.py` | Holographic HUD (tkinter + Pillow) |
| Platform | `jarvis/winops.py` | Windows low-level ops (clipboard/media/screenshot/recycle/telemetry…) |
| Config | `jarvis/config.py` | Central configuration |

## 🚀 Quick start

> Requires **Python 3.12** (macOS or Windows). The first run downloads the Whisper model — please be patient.

<details open>
<summary><b>🍎 macOS</b></summary>

```bash
# 1) Clone
git clone https://github.com/2026lbw-ops/holojarvis.git
cd holojarvis

# 2) Create a venv and install deps
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3) Configure your gateway (OpenAI-compatible)
cp base_url.txt.example base_url.txt   # your gateway URL, e.g. https://xxx/v1
cp api_key.txt.example  api_key.txt    # your API key
cp model.txt.example    model.txt      # pick a model, e.g. deepseek-v4-flash

# 4) Run (with the desk pet)
./run.sh
# or headless: ./run.sh --no-pet
```

> ⚠️ On first run macOS asks for **Microphone** permission; some tools (screenshot / read-screen / WeChat)
> also need **Screen Recording** and **Accessibility** permission under *System Settings → Privacy & Security*.
</details>

<details>
<summary><b>🪟 Windows (PowerShell)</b></summary>

```powershell
# 1) Clone
git clone https://github.com/2026lbw-ops/holojarvis.git
cd holojarvis

# 2) Create a venv and install deps
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3) Configure your gateway (OpenAI-compatible)
copy base_url.txt.example base_url.txt   # your gateway URL, e.g. https://xxx/v1
copy api_key.txt.example  api_key.txt    # your API key
copy model.txt.example    model.txt      # pick a model, e.g. deepseek-v4-flash

# 4) Run (with the desk pet)
.\run.bat
# or headless: .\run.bat --no-pet
```

> ⚠️ On Windows, allow **Microphone** access on first run (Settings → Privacy & security → Microphone).
> The system voice uses built-in **SAPI** — install a Chinese voice (e.g. *Microsoft Huihui*) under
> *Time & language → Speech*. WeChat sending uses UI automation, so WeChat must be logged in and focusable.
</details>

Then say "**贾维斯**" (Jarvis), or click the arc reactor at the center of the pet, to start talking.

### 🗣️ (Optional) Cloned voice

By default it speaks with the system Chinese voice (macOS `say` / Windows SAPI) — zero config. For a cloned voice:

1. Deploy [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) and start its `api_v2` listening on `127.0.0.1:9880`;
2. Prepare a few-second reference clip of the voice you want, and set:
   ```bash
   export JARVIS_TTS=gptsovits
   export GPTSOVITS_REF=/abs/path/to/your_reference.wav
   export GPTSOVITS_PROMPT="the exact text spoken in that reference clip"
   ```
3. Re-run `./run.sh`. If port 9880 is unreachable it auto-falls back to `say`.

> 💡 On Apple Silicon, set GPT-SoVITS `device` to `mps` for GPU acceleration — 2–3× faster synthesis.

## ⚙️ Configuration

All sensitive config lives in a few text files in the project root (excluded by `.gitignore`, never committed):

| File | Purpose | Required |
|---|---|---|
| `base_url.txt` | Gateway URL (ending in `/v1`) | ✅ |
| `api_key.txt` | Gateway / LLM API key | ✅ |
| `model.txt` | Model name (default `deepseek-v4-flash`) | ⬜ |
| `mcp.json` | MCP tool config | ⬜ |
| `notes.txt` | HUD notes panel content | ⬜ |

Environment variables override these (higher priority): `JARVIS_BASE_URL`, `JARVIS_API_KEY`, `JARVIS_MODEL`,
`JARVIS_MAX_TOKENS`, `JARVIS_CLOUD_MEMORY`, `JARVIS_TTS`, `JARVIS_VOICE`, `JARVIS_WHISPER`, etc. — see `jarvis/config.py`.
On Windows, set `JARVIS_OUTPUT_DEVICE` to an output index or name fragment (for example, `Conexant SmartAudio HD`) when the system default routes audio incorrectly.
If a Windows USB microphone works in Sound Recorder but not in Python, set `JARVIS_AUDIO_BACKEND=soundcard`; use `JARVIS_MIC_THRESHOLD` to tune low-level voice activation (default: `400`).
Increase `JARVIS_SILENCE_TAIL` (seconds) when brief USB capture gaps split an utterance too early.
The `soundcard` compatibility backend uses five-second capture blocks for older USB drivers, so responses may be about five seconds slower than the default backend.
For CPU-only local models, set `JARVIS_MAX_TOKENS=256` to reduce latency for short voice replies.

> 🔧 **Switch models**: edit one line in `model.txt` and restart. Pick a model that **supports tool calling**,
> otherwise opening apps / reading the screen / memory won't work.

## 🧰 Built-in tools

| Tool | Description |
|---|---|
| `open_app` / `open_url` / `web_search` | Open apps, URLs, search |
| `get_time` / `get_weather` | Time, weather |
| `control_music` / `set_volume` | Control Music, adjust volume |
| `set_timer` | Countdown voice reminder |
| `take_screenshot` / `read_screen` | Screenshot, read & summarize the screen |
| `send_wechat` | Send a WeChat message (confirms verbally first; macOS / Windows) |
| `system_power` | Lock / sleep |
| `remember` / `list_memories` | Add and view core, long-term, or project memories |
| `update_memory` / `forget` / `clear_memories` | Edit, remove by keyword, or clear memories (confirmation required) |
| `export_memories` | Export JSON inside `workspace/` (confirmation required) |
| `read_text_file` / `propose_file_change` | Read confirmed text and create file proposals without changing targets |
| `list_directory` / `run_shell` / `move_to_trash` | Multi-step file tasks (deletes go to Trash) |

Session memory is the bounded current conversation and is cleared by `/reset`; `core`, `long_term`, and
`project` memories persist in SQLite. Editing, exporting, and clearing are high-risk operations that must be enabled and confirmed each time.

With a cloud model, the current conversation and tool results are sent to the configured endpoint; persistent
memory is not sent by default. Set `JARVIS_CLOUD_MEMORY=core,project` to allow selected categories, or use
`all` / `none`. Loopback endpoints (`localhost`, `127.0.0.1`, `::1`) allow every category by default. Startup
and `python -m jarvis --check --text` show the effective scope and reject invalid values.

### Local task board

Text mode supports local-only task commands that are never sent to the model:

```text
/task add Finish the project notes
/task list
/task start 1
/task progress 1 25 Requirements reviewed
/task remind 1 2026-08-28 09:00 Check the attachment first
/task reminders
/task unremind 1
/task done 1
/task reopen 1
/task list all
```

Tasks use `todo`, `doing`, and `done` states; the default list shows active tasks only. Reaching 100% completes
a task automatically. A completed task must be reopened before its progress can be reduced.

Each unfinished task can hold one local absolute-time reminder. While text mode is running, a due reminder is
shown once within about one second and survives restarts. Completing a task cancels its reminder. Reminder data
is never sent to the model.

### File change review

The agent can only propose changes to UTF-8 text files up to 256 KiB inside `workspace/`; target files remain
untouched until acceptance. Reading an existing file is high risk and requires dangerous tools to be enabled
plus a separate confirmation. Review locally with:

```text
/diff list
/diff show 1
/diff accept 1
/diff reject 1
```

A successful acceptance creates a local undo record:

```text
/undo list
/undo show 1
/undo apply 1
```

Acceptance or undo refuses to overwrite a target that has changed. Binary files, partial acceptance,
multi-file transactions, repeated undo, and redo are not supported in the first version.

## 🔌 MCP extensions

Edit `mcp.json` to connect [MCP](https://modelcontextprotocol.io/) servers (filesystem, browser automation, web fetch, …);
a filesystem example ships with the repo. MCP remains globally disabled by `JARVIS_ENABLE_MCP`. When enabled,
every server must explicitly assign each exposed tool either `"allow"` or `"confirm"` in its `permissions` map.
Unlisted tools are denied and never shown to the model. A missing, empty, or invalid permission map prevents that
server from starting; startup logs report allowed, confirmed, and denied counts. Code-level browser payment gates
cannot be downgraded by configuration. The filesystem example is read-only and limited to `workspace/`.

Text mode exposes `/skills`, `/skills builtin`, and `/skills mcp` to show each callable Skill's source and current
permission. These commands are handled locally and never reveal MCP environment variables or secrets. In this
version, executable Skills are built-in and MCP tools; there is no standalone `SKILL.md` installer yet.

### Pre-release validation

Run `.\.venv\Scripts\python.exe scripts\release_check.py` on Windows (or the equivalent `.venv/bin/python`
command on macOS), then complete the [manual device and release checklist](docs/RELEASE_CHECKLIST.md). Never
release from a working tree with uncommitted changes.

The browser example is disabled by default. To enable it, set `JARVIS_ENABLE_MCP=1` and change
`_示例_隔离浏览器.enabled` to `true`. It uses an in-memory profile, fixes its working directory to
`workspace/`, and writes downloads and other output only to `workspace/browser-output/`; it never reuses an
existing Chrome or Edge login. The bridge rejects Playwright configurations that disable these boundaries.

Uploads can only select existing files inside `workspace/`. Uploads, every page click, Enter/Space submission,
`submit=true`, accepting a browser dialog, and browser-side script execution pause until the user replies
“确认执行” in the next turn. Authorization is single-use; cancellation or unrelated input invalidates it, and
the event is written to the local redacted audit log.

## 🗺️ Roadmap

- [x] Windows support (cross-platform from a single codebase)
- [ ] Launch at login (macOS launchd / Windows Task Scheduler)
- [ ] Click-through / adjustable opacity for the pet
- [ ] More built-in tools (Calendar, Reminders, Mail)
- [ ] Waveform driven by real mic levels

Issues and PRs welcome — see the [Contributing Guide](./CONTRIBUTING.md).

## 🙏 Acknowledgements

- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) — few-shot voice cloning
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — local speech recognition
- [Model Context Protocol](https://modelcontextprotocol.io/) — tool extension protocol
- [skyfireitdiy/Jarvis](https://github.com/skyfireitdiy/Jarvis) — same-name project, README style reference

## 📄 License

[MIT](./LICENSE) © 2026 wang64862
