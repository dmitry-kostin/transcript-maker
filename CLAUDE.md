# Transcript Maker

## Commands
- `poetry run python run.py` — start dev server on :8000
- `tail -f logs/app.log` — follow server logs (written even when the server runs in another terminal)
- `poetry run pytest` — run unit + endpoint tests (no API calls)
- `poetry run pytest -m integration` — run integration tests (needs real API key + internet)
- `poetry run pytest tests/storage/test_record_store.py -v` — run a specific test module
- `poetry run pytest tests/usecases` — run one layer's tests
- `poetry run ruff check .` — lint (`--fix` applies the safe fixes)
- `poetry run ruff format .` — format (`--check` only reports)

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
- `TM_API_TIMEOUT_SECONDS` (default: `180`) / `TM_API_MAX_RETRIES` (default: `1`) — applied to every client in `ClientFactory.client_for()`; the SDK default of 10 min × 3 attempts made a slow call look like a hang
- No real key needed for unit tests — tests that construct a client monkeypatch `settings.openai_api_key` to a dummy value per-test (never globally, so integration tests still use the real key)

## Demo Mode
- Open `http://localhost:8000?demo` — frontend routes requests to `/api/demo/` endpoints
- Runs the real use-case classes built with `StepsFactory().demo(DemoServices(settings))` (`app/adapters.py`, `app/services/demo.py`): 5s download + 5s transcription with canned text, no real APIs called, no API key needed
- Demo routes are `DemoWorkRoutes` (`app/web/demo_routes.py`), a subclass of the same `WorkRoutes` the real `/api` endpoints use, handed the demo use cases by `Wiring` — so validation, the 409 guard and failure handling are identical by construction
- Demo never reads or writes the audio cache (`Steps.audio_cache` is a `NoStoredAudio`) — placeholder audio must not be picked up by a real transcription of the same URL
- Demo records carry `demo: true` in their frontmatter. A record belongs to the mode that made it: demo retranscribe/summarize of a real record is refused with a 409 (it would replace a real transcript with placeholder text), and so is real retranscribe/summarize of a demo record (`Records.load_to_change()` in `usecases/records.py`)
- 50% chance of multi-chunk mode (2–6 chunks) to test chunk progress UI
- URL validation is skipped — type anything in the input field
- Demo records are written to `results/` like real ones and show up in the same history; retranscribe works on them too
- `DemoServices` is stateless (one instance serves every request): a run's chunk count is read back from the chunk files it wrote

## Architecture
- FastAPI app with SSE-based transcription pipeline: download → chunk → transcription API → save (with chunk cache for resume)
- **Everything is a class.** `app/` has no module-level functions: behaviour lives in classes that receive their collaborators through the constructor, and `app/wiring.py` (`Wiring`) is the one place that builds them. No module-level mutable state, no singletons other than `settings`
- Packages under `app/`, dependencies point downward only (`web` → `usecases` → `domain` / `storage` / `services`):
  - `web/` — HTTP only. `schemas.py` (request models/validation), `routes.py` (`ReadRoutes`, `WorkRoutes`, `RealWorkRoutes` — handlers are methods, registered in each class's `router()`), `demo_routes.py` (`DemoWorkRoutes`), `errors.py` (`ApiError`, `ErrorResponses`), `sse.py` (`EventStream`). No orchestration, no file access
  - `usecases/` — what the app does; never imports FastAPI
    - `transcription.py` — `Transcription` (`transcribe_url()`, `retranscribe()`) refuses up front by raising a `UseCaseError`, then hands a `RunRequest` to a `TranscriptionRun` — a method object holding one run's state — and returns its `events()` stream of `(event_name, payload)` tuples. `Events` builds the events, `RunTools` bundles what every run works with
    - `summarization.py` — `Summarization.summarize_record(record_id, …)`: status check, model resolution, title heading, save sidecar
    - `records.py` — `Records`: list / load / remove / reveal a record, load its summary; `load_to_change()` holds the demo-vs-real rule
    - `providers.py` — `Providers.available()` (backs `GET /api/providers` and the startup log)
    - `maintenance.py` — `Maintenance.clean_up()` (backs `POST /api/cleanup`) and `fail_records_left_by_previous_run()` (startup)
    - `active_runs.py` — `ActiveRuns`: in-memory registry of the transcriptions this process is running
    - `steps.py` — the port: `Steps` (the outside world as a bundle of operations), the `CachedAudio` protocol, the null objects `NoStoredAudio` / `AnyModel`, and `DownloadFailed` (what a `download` step raises). Imports no service and no store — what fills it lives in `app/adapters.py`
    - `errors.py` — `UseCaseError` and its subclasses (`RecordNotFound`, `RecordBusy`, `RecordNotCompleted`, `RecordFromOtherMode`, `ModelNotAvailable`, `SummaryNotFound`, `SummaryNotSaved`)
  - `domain/` — pure rules, no clients, no I/O, no `settings`: `record.py` (`RecordId`, the `STATUS_*` values, `RecordLifecycle`), `model_policy.py` (`Provider`, `ModelPolicy`), `limits.py` (`DurationLimits`). Defaults are passed in by the caller
  - `storage/` — one class per file: `results_directory.py` (`ResultsDirectory`), `markdown_file.py` (`MarkdownFile`), `record_store.py` (`RecordStore`), `summary_store.py` (`SummaryStore`), `audio_cache.py` (`AudioCache`), `chunk_cache.py` (`ChunkCache`), `history.py` (`History` — the four stores wired on one directory)
  - `services/` — `clients.py` (`ClientFactory`), `downloader.py` (`Downloader`), `chunker.py` (`AudioChunker`), `transcriber.py` (`Transcriber`), `summarizer.py` (`Summarizer`), `temp_files.py` (`TempDirectory`), `desktop.py` (`Desktop`), `demo.py` (`DemoServices`)
- `adapters.py` fills the `Steps` port: `StepsFactory.real(...)` / `.demo(...)`, plus `YoutubeDownload` (turns the downloader's `DownloadError` into `DownloadFailed`), `StoredAudio` and `ConfiguredModels` (turns `ProviderNotConfigured` into `ModelNotAvailable`). A service error the use cases must react to is translated here — `usecases/` never imports a service's exception
- `wiring.py` is the composition root: `Wiring(settings)` builds the stores, services and use cases — the real and the demo set are the same classes built with different `Steps`, sharing one `History` and one `ActiveRuns`
- `main.py`: `Application(FastAPI)` — constructing it configures logging, builds a `Wiring` and registers routes; startup work (startup log, failing leftover records) runs in its lifespan. Nothing is built at import; `run.py` (`Launcher`) starts uvicorn with `"app.main:Application", factory=True`
- History stored as markdown files with YAML frontmatter in `results/` — no database
- AI summarization via Chat Completions API (OpenAI or Gemini); `temperature` is only sent to Gemini because OpenAI's GPT-6 models reject non-default values — summaries stored as sidecar files `results/{record_id}_summary.md`
- Frontend is vanilla HTML/CSS/JS in `app/static/` — no build step

## Key Patterns
- Record IDs: 8-char hex (`RecordId.new()` in `domain/record.py`), validated via `RecordId.is_valid()` before any file op
- `ResultsDirectory` is the only place that builds paths from record IDs; `record_file()` validates ID format + glob lookup + path traversal guard. It reads `settings.results_dir` on every call
- `TranscriptionRun.events()` yields progress events, checks `is_cancelled` between steps (`_stop_if_cancelled()` raises the private `_Cancelled`), and `_finish()` (its `finally`) marks interrupted records as failed and removes the run's temp files; `EventStream` in `web/sse.py` serializes the events
- A run's audio is one value, `AcquiredAudio` (path in `tmp/`, title, duration, `needs_caching`), set once by `TranscriptionRun._acquire_audio()`; `_cached_audio()` is a pure lookup. The length rule is applied in one place (`_work()`, right after acquisition) — the download step is additionally handed it as `accept` so a too-long video is refused before it is fetched
- A run only fails or interrupts a record it has opened (`_record_id` is `None` until then; `_target_id` names the record for messages): a retranscribe whose download fails leaves the record exactly as it was
- Use cases raise `UseCaseError` subclasses; `ErrorResponses.STATUS_FOR` in `web/errors.py` maps each to a status code and the client always gets `{"error": message}`. Routes only raise `ApiError` themselves for HTTP-level problems (`Routes._require_valid_id()` → 400). A new refusal = a new subclass + one `STATUS_FOR` entry (`tests/web/test_errors.py` fails if the entry is missing)
- Things that are refused before work starts: unknown record (404), record in progress (409), deleting a record a run holds (409), record from the other mode (409), provider without an API key (400, `Steps.check_model`) — a missing key never surfaces at the first chunk
- SSE progress events include `chunk`, `chunks_total`, and `eta_seconds` fields during transcription (ETA from rolling average of per-chunk times)
- `RecordStore.complete()` / `fail()` / `reset()` all go through `_transition()` — the only place that rebuilds the YAML meta dict; add new frontmatter fields there
- `_transition()` keeps the stored body unless a new one is given: a failed or interrupted retranscribe leaves the previous transcript in the file (status `error`), only `complete()` replaces it
- `_transition()` refuses `in_progress` → `in_progress`, so `reset()` returns False for a record another run holds — that is the guard against two concurrent retranscribes; the 409 from `Transcription.retranscribe()` is only the early check
- Status values are `STATUS_IN_PROGRESS` / `STATUS_DONE` / `STATUS_ERROR` from `domain/record.py` — don't re-inline the strings or the ID regex
- What may happen to a record in which status is `RecordLifecycle` (`domain/record.py`): `may_enter()` (used by `RecordStore._transition()`), `is_in_progress()`, `can_be_summarized()`, `belongs_to_mode()`. Ask it instead of comparing `record["status"]` in a use case or a store
- `RecordStore.fail_in_progress(reason, except_ids)` backs both startup cleanup and `POST /api/cleanup`. Liveness is known, not guessed from file ages: `ActiveRuns` registers every run from before its download until its `_finish()`, and cleanup spares exactly the records those runs hold. This relies on the server being a single process
- `Maintenance.clean_up()` skips `TempDirectory.purge()` while any run is active (its download and chunks live in `tmp/`), and otherwise deletes everything in it
- `MarkdownFile.write()` uses `sort_keys=False` to preserve frontmatter key order
- Audio cached in `results/{record_id}.mp3` after download (`AudioCache`) — reused by retranscribe and same-URL re-transcriptions, deleted with record
- `RecordStore.find_cached_audio_for_url()` scans existing records to skip re-download when the same URL is transcribed again; the use case reaches it through `Steps.audio_cache` (`StoredAudio` in `app/adapters.py`)
- Summary sidecar: `results/{record_id}_summary.md` with YAML frontmatter (prompt, model, created_at) + body (`SummaryStore`)
- `RecordStore.delete()` cascades to delete summary sidecar, audio cache, and chunk cache. `Records.remove()` refuses (`RecordBusy`, 409) while a run in `ActiveRuns` holds the record; a record merely left `in_progress` with no run behind it can be deleted
- `TranscriptionRun._save()` raises when `RecordStore.complete()` returns False, so a transcript that is not in the history is reported as an `error` event, never as `transcript` + `done`
- Video title prepended as first line of transcript (`TranscriptionRun._save()`) and as a heading of the summary body (`Summarization._with_heading()`)
- Chunk cache sidecar: `results/{record_id}_chunks.json` (`ChunkCache`) — stores completed chunk transcriptions for resume after interruption; saved after each chunk completes, deleted on successful completion
- `ChunkCache._key()` hashes `model|total|duration_limit` (SHA256, 16-char prefix) — cache invalidated when any parameter changes

## Transcription Models
- `gpt-transcribe` (default) — OpenAI speech-to-text, plain text output
- `gemini-3.5-transcribe` (Gemini default) and other `gemini-*` models — Google Gemini via OpenAI-compatible endpoint, uses chat + `input_audio` for transcription
- `TM_TRANSCRIBE_MODEL` overrides the default
- Model resolved once per request via `ModelPolicy.resolve_transcribe_model(requested, default)` (called by `Transcription` with `settings.transcribe_model`) — the same value is stored on the record and used for execution. Summaries do the same with `resolve_summarize_model()` in `Summarization`, and the model is stored in the sidecar. `Transcriber.transcribe_chunk()` and `Summarizer.summarize()` require an already-resolved model
- Providers are a table: `PROVIDERS` in `domain/model_policy.py` holds one frozen `Provider` per vendor with what it can do (model prefix, `transcribe_via`, `summary_temperature`). How it is configured here — API key, base URL, default models — is `settings.provider(provider.id)` in `app/config.py` (`ProviderSettings`). `ModelPolicy.provider_for(model)` picks one. Don't branch on model-name prefixes elsewhere — add a field to `Provider`. A new vendor is a `Provider` entry plus a branch in `Settings.provider()`; `tests/test_config.py` fails if the second is forgotten
- Speaker detection (diarization) was removed. Old records still carry `*-diarize` model names: `resolve_transcribe_model()` strips the suffix, the frontend still colours `A:`/`B:` labels in old transcripts and shows a "speakers" badge for them. A `diarize` field sent by an old client is ignored
- `ClientFactory` (`app/services/clients.py`) — used by both `Transcriber` and `Summarizer`: `client_for(model)` builds the client, `require_provider(model)` raises `ProviderNotConfigured` when the key is missing
- Gemini transcription uses `gemini-3.5-transcribe`, Google's dedicated speech-to-text model, through the same chat + `input_audio` call. It is not a reasoning model: it rejects `reasoning_effort` with a 400, and it is not served on the OpenAI-compatible `/audio/transcriptions` route (404). Avoid general Flash models here — with default thinking `gemini-3-flash-preview` took 18s to over 50s on a 2-minute clip that the transcribe model does in ~4s
- Chat-based transcription (`Transcriber._via_chat()`, used by Gemini) retries on empty content: `MAX_EMPTY_RESPONSE_RETRIES = 3` with exponential backoff — raises `RuntimeError` after all retries exhausted. The audio format sent is the chunk file's extension
- `duration_limit` API field is in minutes (`DurationLimitMinutes` in `web/schemas.py`, `0 <= v <= MAX_DURATION_LIMIT_MINUTES` = 240, 0 = no limit — derived from `MAX_DURATION_SECONDS`, so a limit that passes validation is never refused for length); `TranscriptionOptions.duration_limit_seconds` is the one place it becomes seconds
- Length rules live in `DurationLimits` (`domain/limits.py`): a request may transcribe at most `MAX_DURATION_SECONDS` (4 h) of audio, and a duration limit counts — the first 10 minutes of a 5-hour video are accepted. Retranscribes are exempt. The check runs before the download: `Downloader.download(url, accept)` fetches metadata first and calls `accept(duration)`, which raises to refuse (`_fetch` uses yt-dlp's `extract_info(download=False)` then `process_ie_result(info, download=True)`)

## Code Style
- No standalone functions in `app/` or `run.py`: every `def` is a method. Put behaviour on the class that owns the data or the dependency it needs; pass collaborators into `__init__` and build them in `Wiring`
- Keep classes small and single-purpose (one per file in `storage/` and `services/`); public methods first, private helpers (`_name`) below them, in the order they are called
- When a method needs many related arguments, group them (`RunTools`, `RunRequest`) instead of growing the signature; when a function grows long local state, turn it into a method object (`TranscriptionRun`)
- Prefer a null object over a flag (`NoStoredAudio`, `AnyModel`) and a subclass over a parameterised branch (`RealWorkRoutes` / `DemoWorkRoutes`)
- Method names must not shadow builtins used in annotations (`list_all()`, not `list()`)
- Ruff is the linter and the formatter (`[tool.ruff]` in `pyproject.toml`: line length 120, rules `E`, `W`, `F`, `I`, `B`, `UP`). Run `ruff check .` and `ruff format .` before committing; both must pass clean
- Type hints on all signatures; `dict | None` union syntax (Python 3.10+)
- Logging via `logging.getLogger(__name__)`
- Records themselves stay plain dicts (not classes). Frozen dataclasses are for fixed wiring and value bundles (`Provider`, `Steps`, `History`, `RunTools`, `RunRequest`)
- YAML frontmatter fields: title, url, status, duration, duration_limit, model, words (on complete), created_at, error, demo (only on demo records) (in this order)

## Logging
- `LoggingSetup(settings).install()` in `app/logging_setup.py`, called when `Application` is constructed (not from `run.py`); `run.py` passes `log_config=None` to uvicorn
- Applies to `app`, `uvicorn`, `uvicorn.error`, `uvicorn.access` loggers — propagation disabled
- Startup log shows enabled providers and active models
- Everything is mirrored to a rotating file at `logs/app.log` (`TM_LOG_FILE`, 5 MB × 3 backups) so logs are readable while the server runs in another process — `tail -f logs/app.log`, or `grep -n "=== startup ===" logs/app.log | tail -1` to find where the current run begins
- Console handler uses `%H:%M:%S`; file handler uses full `%Y-%m-%d %H:%M:%S` since the file spans days
- `TM_LOG_FILE=""` disables file logging; `tests/conftest.py` sets it so test runs don't append to the log
- `logs/` is gitignored

## Testing Conventions
- `tests/` mirrors `app/`: tests for `app/<pkg>/<module>.py` live in `tests/<pkg>/test_<module>.py` (`tests/test_main.py` for `app/main.py`, `tests/test_wiring.py` for `app/wiring.py`, `tests/test_adapters.py` for `app/adapters.py`). Only `tests/test_integration.py` spans layers. Put a new test next to the module it exercises, not next to the endpoint that happens to reach it
- Tests are methods of `Test*` classes; shared helpers are methods of a base class the test classes extend (`TranscriberTest`, `MaintenanceTest`, `DemoTest`). The only module-level functions in `tests/` are pytest fixtures
- Fixtures in `tests/conftest.py`: `tmp_results` (points `settings.results_dir` at a temp dir), `history` (a `History` on it), `client` (TestClient on it). The use-case `env` fixture (`Env`: fake `Steps`, the use cases built on them, `run()`) lives in `tests/usecases/conftest.py`
- Build the class under test with its collaborators (`Summarizer(clients, ModelPolicy())`, `Maintenance(history, TempDirectory(settings), ActiveRuns())`) and pass fakes for the ones that reach outside — don't patch modules
- Patch settings on the singleton (`from app.config import settings`; `monkeypatch.setattr(settings, ...)`); every class reads it at call time
- `StepsFactory.real()` binds the services' methods when the app is wired, so replacing a service method must happen before the app is built — request such a fixture before `client` (see `summarizer` in `tests/web/test_routes.py`)
- `TestClient(Application())` does not run the lifespan; use it as a context manager when a test needs startup behaviour
- Mock external deps (yt-dlp, ffmpeg, OpenAI) in unit tests
- Test the transcription flow through `env.run()` / `env.run(record_id=...)` in `tests/usecases/test_transcription.py`, not through the HTTP endpoints
- Integration tests marked with `@pytest.mark.integration`

## Integration Tests
- Run with: `poetry run pytest -m integration -v --log-cli-level=INFO`
- The sandbox blocks the macOS trust store that `openai` 3.x uses for TLS, so real API calls fail there with `SSLCertVerificationError` — run integration tests outside the sandbox
- Requires: internet, ffmpeg, `TM_OPENAI_API_KEY` (OpenAI tests), `GOOGLE_API_KEY` (Gemini tests + LLM judge)
- Tests with missing keys are skipped automatically
- Audio caching (`CachedTestAudio`): downloaded audio cached in `tmp/test_cache/` (in-memory + disk); only `test_download_returns_valid_audio` downloads fresh
- LLM judge (`LlmJudge` in the test module): Gemini validates transcription/summary quality, returns JSON `{confidence, justification}`; threshold is 70%
- Debug report (`DebugReport`): each run writes `tests/debug/integration_YYYYMMDD_HHMMSS.md` with full outputs, word counts, and judge scores
- `tests/debug/` is gitignored

## Summarize Feature
- Summarize button appears on completed transcript cards (expands card if collapsed)
- User can enter a custom prompt or use the default
- Summary stored as `results/{record_id}_summary.md` with YAML frontmatter (prompt, model, created_at)
- Video title prepended as first line of both transcript body and summary body
- Expanded cards show Transcript/Summary tab toggle when a summary exists
- Copy button copies based on active tab (transcript or summary), toast says "Transcript copied" / "Summary copied"
- Demo mode: `DemoServices.summarize` — simulated 2s delay, canned summary text
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
- `TranscriptionOptions.duration_limit_seconds` converts minutes → seconds before storage and passing to `AudioChunker.prepare_chunks()`
- `AudioChunker.prepare_chunks()` truncates audio via ffmpeg before chunking when `duration_limit` is set
- Stored in YAML frontmatter as `duration_limit` (seconds); card displays "first Nm" badge and "Nm of Xh Ym" duration format
- Validation: `0 <= duration_limit <= 240` minutes (0 = no limit, max = 4 hours — the most one transcription processes)

## Gotchas
- `MarkdownFile.read()` returns every YAML value as a string; `RecordStore._parse()` turns `duration`, `duration_limit`, and `words` into ints and `demo` into a bool
- Old records may lack newer frontmatter fields — always use `.get("field", "")` with defaults
- `RecordStore.list_all()` strips `body` from returned dicts (metadata only); no record dict ever carries the file path — use `RecordStore.file_path()` when one is needed
- Temp files use UUID suffixes for isolation — cleanup uses glob patterns to find chunks
- Chunk cache JSON (`_chunks.json`) files are safely ignored by history glob (same pattern isolation as summary sidecars)
