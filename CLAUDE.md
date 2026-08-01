# Transcript Maker

## Commands
- `poetry run python run.py` — start dev server on :8000
- `poetry run pytest` — run unit + endpoint tests (no API calls)
- `poetry run pytest -m integration` — run integration tests (needs real API key + internet)
- `poetry run pytest tests/test_history.py -v` — run a specific test module

## Environment
- Requires `TM_OPENAI_API_KEY` env var or `.env` file (not needed for demo mode)
- `GOOGLE_API_KEY` env var (no `TM_` prefix) — required when using Gemini models
- `TM_SUMMARIZE_MODEL` env var (default: `gpt-4o`) — model used for AI summarization (supports `gemini-*` models)
- `TM_TRANSCRIBE_MODEL` env var (default: `gpt-4o-transcribe`) — model used for transcription (supports `gemini-*` models)
- Per-provider model defaults (used by provider selector when switching providers):
  - `TM_OPENAI_TRANSCRIBE_MODEL` (default: `gpt-4o-transcribe`)
  - `TM_OPENAI_SUMMARIZE_MODEL` (default: `gpt-4o`)
  - `TM_GEMINI_TRANSCRIBE_MODEL` (default: `gemini-3-flash-preview`)
  - `TM_GEMINI_SUMMARIZE_MODEL` (default: `gemini-3-flash-preview`)
- No real key needed for unit tests — tests that construct a client monkeypatch `settings.openai_api_key` to a dummy value per-test (never globally, so integration tests still use the real key)

## Demo Mode
- Open `http://localhost:8000?demo` — frontend routes requests to `/api/demo/` endpoints
- Simulates 5s download + 5s transcription with fake transcript text, no real APIs called
- 50% chance of multi-chunk mode (2–6 chunks) to test chunk progress UI
- URL validation is skipped — type anything in the input field
- History records are real (written to `results/`), retranscribe works too

## Architecture
- FastAPI app with SSE-based transcription pipeline: download → chunk → transcription API → save (with chunk cache for resume)
- History stored as markdown files with YAML frontmatter in `results/` — no database
- AI summarization via Chat Completions API (OpenAI or Gemini) — summaries stored as sidecar files `results/{record_id}_summary.md`
- Frontend is vanilla HTML/CSS/JS in `app/static/` — no build step

## Key Patterns
- Record IDs: 8-char hex from `uuid4().hex[:8]`, validated via regex before any file op
- `_resolve_path()` in `history.py`: validates ID format + glob lookup + path traversal guard
- SSE generators in `api.py`: yield progress events, handle client disconnect, `finally` marks interrupted records as failed
- SSE progress events include `chunk`, `chunks_total`, and `eta_seconds` fields during transcription (ETA from rolling average of per-chunk times)
- `complete_record()` / `fail_record()` rebuild the full YAML meta dict from parsed record — always include all fields
- `_write_md()` uses `sort_keys=False` to preserve frontmatter key order
- Audio cached in `results/{record_id}.mp3` after download — reused by retranscribe and same-URL re-transcriptions, deleted with record
- `find_cached_audio_by_url()` in `history.py`: scans existing records to skip re-download when the same URL is transcribed again
- Summary sidecar: `results/{record_id}_summary.md` with YAML frontmatter (prompt, created_at) + body
- `delete_record()` cascades to delete summary sidecar, audio cache, and chunk cache
- Video title prepended as first line of transcript and summary body in `api.py` (all codepaths: transcribe, demo, retranscribe, summarize, demo summarize)
- Chunk cache sidecar: `results/{record_id}_chunks.json` — stores completed chunk transcriptions for resume after interruption
- `_chunk_cache_key()` hashes `model|diarize|total` (SHA256, 16-char prefix) — cache invalidated when any parameter changes
- `load_chunk_cache()` / `save_chunk_cache()` / `delete_chunk_cache()` in `history.py` manage the chunk cache lifecycle
- Chunk cache saved after each chunk completes; deleted on successful transcription completion

## Transcription Models
- `gpt-4o-transcribe` (default) — OpenAI Whisper, plain text output
- `gpt-4o-transcribe-diarize` — OpenAI Whisper with speaker detection, returns `A: text`, `B: text` speaker labels
- `gemini-*` models — Google Gemini via OpenAI-compatible endpoint, uses chat + `input_audio` for transcription; diarization is prompt-driven (not API-native like Whisper) using same `A: B:` label format
- `TM_TRANSCRIBE_MODEL` overrides the default; frontend sends `diarize: true` as a separate boolean
- Model resolved once at request time via `resolve_model(model, diarize)` in `api.py` — same resolved values used for both storage (`get_stored_model`) and execution (`transcribe_chunk`)
- `resolve_model()` extracts base model from `-diarize` suffix for backward compat (e.g. `"gemini-2.0-flash-diarize"` → `("gemini-2.0-flash", True)`)
- Shared Gemini helpers (`is_gemini_model`, `get_client`) live in `app/clients.py` — used by both transcriber and summarizer
- Gemini retry logic: `MAX_GEMINI_RETRIES = 3` with exponential backoff (`2^attempt` seconds) on empty content — raises `RuntimeError` after all retries exhausted
- `duration_limit` API field is in minutes; converted to seconds (`* 60`) in the handler before storage
- `duration_limit` validation: `0 <= v <= 480` (0 = no limit, max = 8 hours)

## Code Style
- Type hints on all function signatures
- `dict | None` union syntax (Python 3.10+)
- Logging via `logging.getLogger(__name__)`
- No classes for data — records are plain dicts
- YAML frontmatter fields: title, url, status, duration, duration_limit, model, words (on complete), created_at, error (in this order)

## Logging
- Custom formatter configured in `app/main.py` (not `run.py`); `run.py` passes `log_config=None` to uvicorn
- Applies to `app`, `uvicorn`, `uvicorn.error`, `uvicorn.access` loggers — propagation disabled
- Startup log shows enabled providers and active models

## Testing Conventions
- `tmp_results` fixture monkeypatches `history.RESULTS_DIR` to a temp dir
- Test classes group related tests (e.g. `TestLifecycle`, `TestEdgeCases`)
- Mock external deps (yt-dlp, ffmpeg, OpenAI) in unit tests
- Integration tests marked with `@pytest.mark.integration`

## Integration Tests
- Run with: `poetry run pytest -m integration -v --log-cli-level=INFO`
- Requires: internet, ffmpeg, `TM_OPENAI_API_KEY` (OpenAI tests), `GOOGLE_API_KEY` (Gemini tests + LLM judge)
- Tests with missing keys are skipped automatically
- Audio caching: downloaded audio cached in `tmp/test_cache/` (in-memory + disk); only `test_download_returns_valid_audio` downloads fresh
- LLM judge: Gemini validates transcription/summary quality, returns JSON `{confidence, justification}`; threshold is 70%
- Debug report: each run writes `tests/debug/integration_YYYYMMDD_HHMMSS.md` with full outputs, word counts, and judge scores
- `tests/debug/` is gitignored

## Summarize Feature
- Summarize button appears on completed transcript cards (expands card if collapsed)
- User can enter a custom prompt or use the default
- Summary stored as `results/{record_id}_summary.md` with YAML frontmatter (prompt, created_at)
- Video title prepended as first line of both transcript body and summary body
- Expanded cards show Transcript/Summary tab toggle when a summary exists
- Copy button copies based on active tab (transcript or summary), toast says "Transcript copied" / "Summary copied"
- Demo mode: simulated 2s delay, canned summary text
- `SummarizeRequest` accepts optional `model` field — frontend passes `getSummarizeModel()` based on selected provider
- Endpoints: `POST /api/history/{id}/summarize`, `GET /api/history/{id}/summary`

## Provider Selector
- `GET /api/providers` — returns available providers based on configured API keys (`openai_api_key`, `google_api_key`)
- Frontend widget in bottom-left corner toggles between providers, persists selection to `localStorage` key `tm_provider`
- Each provider entry includes `transcribe_model` and `summarize_model` — frontend passes these to all API calls
- Provider only appears if its API key is configured; widget hidden when fewer than 2 providers available

## Obsidian Export
- "Obsidian" button exports directly to Obsidian via `obsidian://new` URI + clipboard
- First click prompts for vault name + optional subfolder, stored in `localStorage` keys `tm_obsidian_vault` and `tm_obsidian_subfolder`
- `exportToObsidian(id)` replaces `copyObsidianMarkdown(id)` — copies markdown to clipboard, then opens `obsidian://new?vault=...&file=...&clipboard&overwrite`
- `buildObsidianMarkdown(id)` builds the frontmatter + body (reuses `formatObsidianDate`, `escapeYamlString`, `getFullRecord`)
- `slugify(title)` generates the note filename (lowercase, hyphens, max 80 chars)
- `showObsidianConfig(id)` / `addObsidianConfigUI(id, card)` — horizontal path bar (`VaultName / folder/path [Connect]`) with one-time setup hint
- `clearObsidianConfig()` removes localStorage keys; "Reset vault" link appears in card actions when vault is configured
- Fallback: if Obsidian isn't installed, markdown is still on clipboard for manual paste
- Toast: "Sent to Obsidian"

## Duration Limit
- "First N min" toggle + input in the frontend, sends `duration_limit` in minutes
- API converts minutes → seconds (`* 60`) before storage and passing to `prepare_chunks()`
- `prepare_chunks()` truncates audio via ffmpeg before chunking when `duration_limit` is set
- Stored in YAML frontmatter as `duration_limit` (seconds); card displays "first Nm" badge and "Nm of Xh Ym" duration format
- Validation: `0 <= duration_limit <= 480` minutes (0 = no limit, max = 8 hours)

## Gotchas
- `_parse_md()` coerces all YAML values to strings except `duration`, `duration_limit`, and `words` (explicitly int)
- Old records may lack newer frontmatter fields — always use `.get("field", "")` with defaults
- `get_history()` strips `body` and `path` from returned dicts (metadata only)
- Temp files use UUID suffixes for isolation — cleanup uses glob patterns to find chunks
- Chunk cache JSON (`_chunks.json`) files are safely ignored by history glob (same pattern isolation as summary sidecars)
