# Transcript Maker

## Commands
- `poetry run python run.py` — start dev server on :8000
- `tail -f logs/app.log` — follow server logs (written even when the server runs in another terminal)
- `poetry run pytest` — run unit + endpoint tests (no API calls)
- `poetry run pytest -m integration` — run integration tests (needs real API key + internet)
- `poetry run pytest tests/storage/test_history.py -v` — run a specific test module
- `poetry run pytest tests/usecases` — run one layer's tests

## Environment
- Requires `TM_OPENAI_API_KEY` env var or `.env` file (not needed for demo mode)
- `GOOGLE_API_KEY` env var (no `TM_` prefix) — required when using Gemini models
- `TM_SUMMARIZE_MODEL` env var (default: `gpt-6-luna`) — model used for AI summarization (supports `gemini-*` models)
- `TM_TRANSCRIBE_MODEL` env var (default: `gpt-transcribe`) — model used for transcription (supports `gemini-*` models)
- Per-provider model defaults (used by provider selector when switching providers):
  - `TM_OPENAI_TRANSCRIBE_MODEL` (default: `gpt-transcribe`)
  - `TM_OPENAI_SUMMARIZE_MODEL` (default: `gpt-6-luna`)
  - `TM_GEMINI_TRANSCRIBE_MODEL` (default: `gemini-3.5-transcribe`)
  - `TM_GEMINI_SUMMARIZE_MODEL` (default: `gemini-3.8-flash`)
- `TM_API_TIMEOUT_SECONDS` (default: `180`) / `TM_API_MAX_RETRIES` (default: `1`) — applied to every client in `get_client()`; the SDK default of 10 min × 3 attempts made a slow call look like a hang
- No real key needed for unit tests — tests that construct a client monkeypatch `settings.openai_api_key` to a dummy value per-test (never globally, so integration tests still use the real key)

## Demo Mode
- Open `http://localhost:8000?demo` — frontend routes requests to `/api/demo/` endpoints
- Runs the real use cases with fake `Steps` from `app/usecases/demo.py` (`demo_steps()`, `demo_summarize`): 5s download + 5s transcription with canned text, no real APIs called
- Demo routes (`app/web/demo_routes.py`) call the same shared handlers as the real ones, so validation, the 409 guard and failure handling are identical
- Demo never reads or writes the audio cache (`Steps.cache_audio=False`) — placeholder audio must not be picked up by a real transcription of the same URL
- 50% chance of multi-chunk mode (2–6 chunks) to test chunk progress UI
- URL validation is skipped — type anything in the input field
- History records are real (written to `results/`), retranscribe works too

## Architecture
- FastAPI app with SSE-based transcription pipeline: download → chunk → transcription API → save (with chunk cache for resume)
- Packages under `app/`, dependencies point downward only (`web` → `usecases` → `domain` / `storage` / `services`):
  - `web/` — HTTP only. `schemas.py` (request models/validation), `routes.py` (real endpoints), `demo_routes.py` (`/api/demo/*`), `shared.py` (record lookup, `ApiError` → `{"error": ...}`, SSE serialization, the calls into the use cases). No orchestration, no file access
  - `usecases/` — what the app does; never imports FastAPI
    - `transcription.py` — `run_transcription()` is one async generator shared by new transcriptions, retranscribes (`record=`) and demo (`steps=`). It composes `_acquire_audio()` → `_open_record()` → `_transcribe_chunks()`, yields `(event_name, payload)` tuples, and takes cancellation as an `is_cancelled` callable
    - `summarization.py` — `summarize_record()`: status check, title heading, save sidecar
    - `maintenance.py` — `clean_up()` (backs `POST /api/cleanup`) and `fail_records_left_by_previous_run()` (startup)
    - `demo.py` — fake services for demo mode
  - `domain/` — pure rules, no clients, no I/O: `model_policy.py` (provider table, `resolve_model`, `available_providers`), `limits.py` (duration limits)
  - `storage/history.py` — records, summaries, audio cache, chunk cache
  - `services/` — `clients.py`, `downloader.py`, `transcriber.py`, `summarizer.py`
- `main.py` is the composition root: `create_app()` wires routers and the `ApiError` handler; startup work (startup log, failing leftover records) runs in the FastAPI `lifespan`, never at import
- The outside world reaches the transcription use case through `Steps` (`download`, `prepare_chunks`, `transcribe_chunk`, `cache_audio`); `real_steps()` is the default
- History stored as markdown files with YAML frontmatter in `results/` — no database
- AI summarization via Chat Completions API (OpenAI or Gemini); `temperature` is only sent to Gemini because OpenAI's GPT-6 models reject non-default values — summaries stored as sidecar files `results/{record_id}_summary.md`
- Frontend is vanilla HTML/CSS/JS in `app/static/` — no build step

## Key Patterns
- Record IDs: 8-char hex from `uuid4().hex[:8]`, validated via regex before any file op
- `_resolve_path()` in `storage/history.py`: validates ID format + glob lookup + path traversal guard
- `run_transcription()` in `usecases/transcription.py`: yields progress events, checks `is_cancelled` between steps (`_stop_if_cancelled()` raises the private `_Cancelled`), `finally` marks interrupted records as failed; `_as_sse()` in `web/shared.py` serializes the events
- A run only fails or interrupts a record it has opened (`job.owns_record`): a retranscribe whose download fails leaves the record exactly as it was
- Route handlers raise `ApiError(status, message)` from `web/shared.py`; use `load_record()` / `require_valid_id()` instead of repeating the 400/404 checks
- SSE progress events include `chunk`, `chunks_total`, and `eta_seconds` fields during transcription (ETA from rolling average of per-chunk times)
- `complete_record()` / `fail_record()` / `reset_record()` all go through `_transition()` in `storage/history.py` — the only place that rebuilds the YAML meta dict; add new frontmatter fields there
- `_transition()` keeps the stored body unless a new one is given: a failed or interrupted retranscribe leaves the previous transcript in the file (status `error`), only `complete_record()` replaces it
- `_transition()` refuses `in_progress` → `in_progress`, so `reset_record()` returns False for a record another run holds — that is the guard against two concurrent retranscribes; the 409 in the route is only the early check
- Status values are `STATUS_IN_PROGRESS` / `STATUS_DONE` / `STATUS_ERROR` from `storage/history.py`; ID format check is `is_valid_record_id()` — don't re-inline the strings or the regex
- `fail_stale_records(reason, older_than_seconds)` in `storage/history.py` backs both startup cleanup and `POST /api/cleanup`. Staleness is measured from the last activity — the record file or its chunk cache, whichever is newer — because the record file is not rewritten during a long multi-chunk run
- `clean_up()` skips `purge_temp_dir()` while any record is in progress (its chunks live in `tmp/`), and otherwise spares files written in the last 60s (a download still in flight)
- `_write_md()` uses `sort_keys=False` to preserve frontmatter key order
- Audio cached in `results/{record_id}.mp3` after download — reused by retranscribe and same-URL re-transcriptions, deleted with record
- `find_cached_audio_by_url()` in `storage/history.py`: scans existing records to skip re-download when the same URL is transcribed again
- Summary sidecar: `results/{record_id}_summary.md` with YAML frontmatter (prompt, created_at) + body
- `delete_record()` cascades to delete summary sidecar, audio cache, and chunk cache
- Video title prepended as first line of transcript (`with_title()` in `usecases/transcription.py`) and as a heading of the summary body (`with_heading()` in `usecases/summarization.py`)
- Chunk cache sidecar: `results/{record_id}_chunks.json` — stores completed chunk transcriptions for resume after interruption
- `_chunk_cache_key()` hashes `model|total` (SHA256, 16-char prefix) — cache invalidated when any parameter changes
- `load_chunk_cache()` / `save_chunk_cache()` / `delete_chunk_cache()` in `storage/history.py` manage the chunk cache lifecycle
- Chunk cache saved after each chunk completes; deleted on successful transcription completion

## Transcription Models
- `gpt-transcribe` (default) — OpenAI speech-to-text, plain text output
- `gemini-3.5-transcribe` (Gemini default) and other `gemini-*` models — Google Gemini via OpenAI-compatible endpoint, uses chat + `input_audio` for transcription
- `TM_TRANSCRIBE_MODEL` overrides the default
- Model resolved once per request via `resolve_model(requested)` in `domain/model_policy.py` (called by `run_transcription()`) — the same value is stored on the record and used for execution. `transcribe_chunk()` requires an already-resolved model; summaries use `resolve_summarize_model()`
- Providers are a table: `PROVIDERS` in `domain/model_policy.py` holds one frozen `Provider` per vendor (model prefix, API key setting, base URL, default-model settings, `transcribe_via`, `summary_temperature`). `provider_for(model)` picks one. Don't branch on model-name prefixes elsewhere — add a field to `Provider`; a new vendor is one entry
- Speaker detection (diarization) was removed. Old records still carry `*-diarize` model names: `resolve_model()` strips the suffix, the frontend still colours `A:`/`B:` labels in old transcripts and shows a "speakers" badge for them. A `diarize` field sent by an old client is ignored
- `get_client()` lives in `app/services/clients.py` — used by both transcriber and summarizer; it builds the client from `provider_for(model)`
- Gemini transcription uses `gemini-3.5-transcribe`, Google's dedicated speech-to-text model, through the same chat + `input_audio` call. It is not a reasoning model: it rejects `reasoning_effort` with a 400, and it is not served on the OpenAI-compatible `/audio/transcriptions` route (404). Avoid general Flash models here — with default thinking `gemini-3-flash-preview` took 18s to over 50s on a 2-minute clip that the transcribe model does in ~4s
- Gemini retry logic: `MAX_GEMINI_RETRIES = 3` with exponential backoff (`2^attempt` seconds) on empty content — raises `RuntimeError` after all retries exhausted
- `duration_limit` API field is in minutes (`DurationLimitMinutes` in `web/schemas.py`, `0 <= v <= 480`, 0 = no limit); `TranscriptionOptions.duration_limit_seconds` is the one place it becomes seconds
- Length rules live in `domain/limits.py`: a request may transcribe at most `MAX_DURATION_SECONDS` (4 h) of audio, and a duration limit counts — the first 10 minutes of a 5-hour video are accepted. Retranscribes are exempt

## Code Style
- Type hints on all function signatures
- `dict | None` union syntax (Python 3.10+)
- Logging via `logging.getLogger(__name__)`
- No classes for data — records are plain dicts. Frozen dataclasses are fine for fixed wiring (`Provider`, `Steps`)
- YAML frontmatter fields: title, url, status, duration, duration_limit, model, words (on complete), created_at, error (in this order)

## Logging
- Custom formatter configured by `configure_logging()` in `app/logging_setup.py`, called from `create_app()` (not `run.py`); `run.py` passes `log_config=None` to uvicorn
- Applies to `app`, `uvicorn`, `uvicorn.error`, `uvicorn.access` loggers — propagation disabled
- Startup log shows enabled providers and active models
- Everything is mirrored to a rotating file at `logs/app.log` (`TM_LOG_FILE`, 5 MB × 3 backups) so logs are readable while the server runs in another process — `tail -f logs/app.log`, or `grep -n "=== startup ===" logs/app.log | tail -1` to find where the current run begins
- Console handler uses `%H:%M:%S`; file handler uses full `%Y-%m-%d %H:%M:%S` since the file spans days
- `TM_LOG_FILE=""` disables file logging; `tests/conftest.py` sets it so test runs don't append to the log
- `logs/` is gitignored

## Testing Conventions
- `tests/` mirrors `app/`: tests for `app/<pkg>/<module>.py` live in `tests/<pkg>/test_<module>.py` (`tests/test_main.py` for `app/main.py`). Only `tests/test_integration.py` spans layers. Put a new test next to the module it exercises, not next to the endpoint that happens to reach it
- `tmp_results` (temp `RESULTS_DIR`) and `client` (TestClient on it) live in `tests/conftest.py`; the use-case `env` fixture (fake `Steps` + `run()`) lives in `tests/usecases/conftest.py`
- Patch settings on the singleton (`from app.config import settings`; `monkeypatch.setattr(settings, ...)`) — modules do not re-export `settings` or each other's functions for tests
- `TestClient(create_app())` does not run the lifespan; use it as a context manager when a test needs startup behaviour
- Test classes group related tests (e.g. `TestLifecycle`, `TestEdgeCases`)
- Mock external deps (yt-dlp, ffmpeg, OpenAI) in unit tests
- Test the transcription flow through `run_transcription()` in `tests/usecases/test_transcription.py` by passing fake `Steps` (see the `env` fixture), not by patching modules and not through the HTTP endpoints
- Integration tests marked with `@pytest.mark.integration`

## Integration Tests
- Run with: `poetry run pytest -m integration -v --log-cli-level=INFO`
- The sandbox blocks the macOS trust store that `openai` 3.x uses for TLS, so real API calls fail there with `SSLCertVerificationError` — run integration tests outside the sandbox
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
- Demo mode: `demo_summarize` passed into `summarize_record()` — simulated 2s delay, canned summary text
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
- `TranscriptionOptions.duration_limit_seconds` converts minutes → seconds before storage and passing to `prepare_chunks()`
- `prepare_chunks()` truncates audio via ffmpeg before chunking when `duration_limit` is set
- Stored in YAML frontmatter as `duration_limit` (seconds); card displays "first Nm" badge and "Nm of Xh Ym" duration format
- Validation: `0 <= duration_limit <= 480` minutes (0 = no limit, max = 8 hours)

## Gotchas
- `_parse_md()` coerces all YAML values to strings except `duration`, `duration_limit`, and `words` (explicitly int)
- Old records may lack newer frontmatter fields — always use `.get("field", "")` with defaults
- `get_history()` strips `body` and `path` from returned dicts (metadata only)
- Temp files use UUID suffixes for isolation — cleanup uses glob patterns to find chunks
- Chunk cache JSON (`_chunks.json`) files are safely ignored by history glob (same pattern isolation as summary sidecars)
