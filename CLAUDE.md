# Transcript Maker

## Commands
- `poetry run python run.py` — start dev server on :8000
- `poetry run pytest` — run unit + endpoint tests (no API calls)
- `poetry run pytest -m integration` — run integration tests (needs real API key + internet)
- `poetry run pytest tests/test_history.py -v` — run a specific test module

## Environment
- Requires `TM_OPENAI_API_KEY` env var or `.env` file (not needed for demo mode)
- Tests set a dummy key in `conftest.py` — no real key needed for unit tests

## Demo Mode
- Open `http://localhost:8000?demo` — frontend routes requests to `/api/demo/` endpoints
- Simulates 10s download + 10s transcription with fake transcript text, no real APIs called
- 50% chance of multi-chunk mode (2–6 chunks) to test chunk progress UI
- URL validation is skipped — type anything in the input field
- History records are real (written to `results/`), retranscribe works too

## Architecture
- FastAPI app with SSE-based transcription pipeline: download → chunk → whisper API → save
- History stored as markdown files with YAML frontmatter in `results/` — no database
- Frontend is vanilla HTML/CSS/JS in `app/static/` — no build step

## Key Patterns
- Record IDs: 8-char hex from `uuid4().hex[:8]`, validated via regex before any file op
- `_resolve_path()` in `history.py`: validates ID format + glob lookup + path traversal guard
- SSE generators in `api.py`: yield progress events, handle client disconnect, `finally` marks interrupted records as failed
- `complete_record()` / `fail_record()` rebuild the full YAML meta dict from parsed record — always include all fields
- `_write_md()` uses `sort_keys=False` to preserve frontmatter key order
- Audio cached in `results/{record_id}.mp3` after download — reused by retranscribe, deleted with record

## Two Whisper Models
- `gpt-4o-transcribe` (default) — plain text output
- `gpt-4o-transcribe-diarize` — speaker detection, returns "Speaker: text" lines
- Model is selected per-request, stored in record frontmatter as `model` field
- Frontend toggle "Speaker detection" maps to diarize model

## Code Style
- Type hints on all function signatures
- `dict | None` union syntax (Python 3.10+)
- Logging via `logging.getLogger(__name__)`
- No classes for data — records are plain dicts
- YAML frontmatter fields: title, url, status, duration, model, created_at, error (in this order)

## Testing Conventions
- `tmp_results` fixture monkeypatches `history.RESULTS_DIR` to a temp dir
- Test classes group related tests (e.g. `TestLifecycle`, `TestEdgeCases`)
- Mock external deps (yt-dlp, ffmpeg, OpenAI) in unit tests
- Integration tests marked with `@pytest.mark.integration`

## Gotchas
- `_parse_md()` coerces all YAML values to strings (line 57) except duration (explicitly int)
- Old records may lack newer frontmatter fields — always use `.get("field", "")` with defaults
- `get_history()` strips `body` and `path` from returned dicts (metadata only)
- Temp files use UUID suffixes for isolation — cleanup uses glob patterns to find chunks
