# Transcript Maker

Paste a YouTube URL, get a transcript. A single-page web app that downloads audio from YouTube and transcribes it using OpenAI Whisper.

<img width="788" height="555" alt="image" src="https://github.com/user-attachments/assets/a92752ee-fc64-4d5b-9f2a-35847deac464" />



## Features

- **YouTube audio download** via yt-dlp (any public YouTube video up to 4 hours)
- **OpenAI Whisper transcription** with automatic language detection
- **Speaker detection** — optional diarization with speaker labels (`gpt-4o-transcribe-diarize`)
- **Re-transcribe** — re-run any completed or failed transcription, optionally switching models
- **Expandable history** — click any completed transcript to preview the text inline
- **Real-time progress** streamed to the browser via Server-Sent Events
- **Cancel** an in-progress transcription from the UI
- **History** with status tracking — persists across page refreshes and server restarts
- **Show in Finder** — reveal any saved transcript file on disk
- **Copy / Download** — copy transcript to clipboard or save as `.txt`
- **Markdown-based storage** — each transcript is a `.md` file, no database
- **Playlist rejection** — only single video URLs accepted

## Tech Stack

- Python 3.11+, FastAPI, uvicorn
- yt-dlp (YouTube download)
- OpenAI Whisper API (transcription)
- ffmpeg / ffprobe (audio chunking)
- pydantic-settings (configuration)
- sse-starlette (SSE streaming)
- Vanilla HTML / CSS / JS (no build step)

## Project Structure

```
transcript-maker/
├── pyproject.toml          # Poetry deps & metadata
├── run.py                  # Single-script launcher (uvicorn)
├── .env.example            # Template for API key
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI app factory + static mount
│   ├── config.py           # pydantic-settings (env vars)
│   ├── api.py              # API routes (transcribe + history endpoints)
│   ├── downloader.py       # yt-dlp: download + extract audio
│   ├── transcriber.py      # ffmpeg chunking + OpenAI Whisper API
│   ├── history.py          # Persistence layer (markdown files)
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── app.js
├── tests/
│   ├── conftest.py         # Shared fixtures
│   ├── test_history.py     # History module tests
│   ├── test_downloader.py  # Downloader unit tests (mocked yt-dlp)
│   ├── test_transcriber.py # Transcriber unit tests (mocked ffmpeg)
│   ├── test_validation.py  # URL validation tests
│   ├── test_api_endpoints.py # API endpoint tests (TestClient)
│   └── test_integration.py # End-to-end tests (real APIs)
├── tmp/                    # Runtime temp files (gitignored)
└── results/                # Saved transcripts as .md files (gitignored)
```

## Prerequisites

- **Python 3.11+**
- **Poetry** — [install instructions](https://python-poetry.org/docs/#installation)
- **ffmpeg** — `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux)
- **OpenAI API key** — with access to the Whisper model

## Setup & Launch

```bash
# Install dependencies
poetry install

# Configure API key (pick one)
export TM_OPENAI_API_KEY=sk-...          # shell variable
echo "TM_OPENAI_API_KEY=sk-..." > .env   # or .env file

# Start the server
poetry run python run.py
```

Open http://127.0.0.1:8000 in your browser.

### Demo Mode

To test the UI without a real API key or internet connection, add `?demo` to the URL:

```
http://127.0.0.1:8000?demo
```

Demo mode simulates the full pipeline (10s download + 10s transcription) with fake data. No YouTube downloads or OpenAI API calls are made. Multi-chunk progress appears randomly (~50% of the time) to exercise the chunk waveform UI.

## Configuration

All settings use the `TM_` prefix and can be set via environment variables or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `TM_OPENAI_API_KEY` | *(required)* | OpenAI API key |
| `TM_TEMP_DIR` | `./tmp` | Directory for temporary audio files |
| `TM_RESULTS_DIR` | `./results` | Directory for saved transcript `.md` files |
| `TM_MAX_CHUNK_SIZE_MB` | `24.0` | Max size per audio chunk sent to Whisper |
| `TM_AUDIO_FORMAT` | `mp3` | Audio format for yt-dlp extraction |

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serve the single-page UI |
| `GET` | `/static/{path}` | Serve CSS / JS assets |
| `POST` | `/api/transcribe` | Start transcription (returns SSE stream) |
| `GET` | `/api/history` | List all saved transcription records |
| `GET` | `/api/history/{id}` | Get single record with transcript body |
| `POST` | `/api/history/{id}/retranscribe` | Re-transcribe with optional model change (SSE stream) |
| `POST` | `/api/history/{id}/reveal` | Open Finder with the transcript file selected |
| `DELETE` | `/api/history/{id}` | Delete a saved transcript |
| `POST` | `/api/cleanup` | Clean up temp files and stale records |
| `POST` | `/api/demo/transcribe` | Demo: simulated transcription (SSE stream) |
| `POST` | `/api/demo/history/{id}/retranscribe` | Demo: simulated re-transcription (SSE stream) |

### POST /api/transcribe

**Request:**
```json
{ "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "model": "gpt-4o-transcribe-diarize" }
```

- `url` — YouTube video URL (required)
- `model` — Whisper model to use (optional). `""` or omitted for default (`gpt-4o-transcribe`), `"gpt-4o-transcribe-diarize"` for speaker detection.

Accepted YouTube hostnames: `youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`. Returns 422 for non-YouTube URLs or playlist URLs.

**Response:** Server-Sent Events stream with these event types:

| Event | Payload | When |
|---|---|---|
| `progress` | `{"stage": "...", "message": "...", "record_id": "..."}` | Each pipeline stage |
| `transcript` | `{"text": "...", "title": "...", "duration_seconds": N, "record_id": "..."}` | Transcription complete |
| `error` | `{"message": "...", "record_id": "..."}` | On failure |
| `done` | `{}` | Stream finished |

### POST /api/history/{id}/retranscribe

Re-transcribes an existing record using its stored URL. Returns an SSE stream identical to `/api/transcribe`. Audio is cached after the first download. Re-transcribe reuses cached audio without re-downloading from YouTube.

**Request:**
```json
{ "model": "gpt-4o-transcribe-diarize" }
```

- `model` — Whisper model to use (optional, same as `/api/transcribe`)

Returns 400 for invalid ID, 404 if not found, 409 if the record is currently `in_progress`.

### GET /api/history

**Response:**
```json
[
  {
    "id": "a1b2c3d4",
    "title": "Never Gonna Give You Up",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "status": "done",
    "duration": 213,
    "model": "gpt-4o-transcribe-diarize",
    "created_at": "2026-02-19T10:30:00",
    "error": ""
  }
]
```

Note: `body` and `path` are stripped from the list response. Use `GET /api/history/{id}` to fetch the full record with transcript body.

Records are sorted newest-first by `created_at`.

## Processing Pipeline

1. **Validate** — reject non-YouTube URLs and playlist URLs (422)
2. **Download** — yt-dlp extracts audio as 64kbps MP3 (async via thread pool)
3. **Guard** — reject videos longer than 4 hours; check for client disconnect
4. **Create record** — write `.md` file with `status: in_progress` and selected model
5. **Chunk** — ffmpeg splits audio into segments under 24 MB (if needed)
6. **Transcribe** — send each chunk to OpenAI Whisper API sequentially (using selected model)
7. **Complete** — update `.md` to `status: done`, write transcript as body
8. **Cleanup** — delete temporary audio files

On error at any step, the record is updated to `status: error`. On client disconnect, the record stays `in_progress` (no partial saves).

## History & Persistence

Each transcription is stored as a markdown file in `results/` with YAML frontmatter:

```markdown
---
title: "Rick Astley - Never Gonna Give You Up"
url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
status: "done"
duration: 213
model: "gpt-4o-transcribe-diarize"
created_at: "2026-02-19T10:30:00"
error: ""
---

Full transcript text here...
```

**Filename format:** `{slugified-title}_{8-hex-id}.md`

**Status lifecycle:** `in_progress` → `done` | `error`

On server startup, any leftover `in_progress` records (from a prior crash) are automatically marked as `error`.

## Testing

```bash
# Unit + endpoint tests (fast, no external API calls)
poetry run pytest tests/ -m "not integration" -v

# Integration tests (requires real OpenAI API key + internet)
poetry run pytest tests/ -m integration -v

# All tests
poetry run pytest tests/ -v
```

Integration tests use a short YouTube video and are skipped automatically when no valid API key is configured.

## Security

- **URL validation** — only YouTube hostnames accepted, enforced server-side via pydantic
- **Show in Finder** — record ID validated as exactly 8 hex chars; file path resolved by scanning `results/` (never from user input); path traversal guard checks resolved parent matches `results/`; `open -R` is read-only
- **No shell injection** — all subprocess calls use list arguments, never shell strings
- **Temp file isolation** — UUID suffixes prevent filename collisions between concurrent requests
