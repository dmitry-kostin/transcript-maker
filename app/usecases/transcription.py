"""The transcription use case: audio → chunks → text → saved record.

Knows nothing about HTTP. ``Transcription`` refuses what can be refused up front
(by raising a ``UseCaseError``) and otherwise returns a stream of
``(event_name, payload)`` tuples; the web layer turns those into SSE and says when
to stop via ``is_cancelled``. New transcriptions and re-transcriptions run the same
``TranscriptionRun``, and so does demo mode — it is built with simulated ``Steps``.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path

from app.config import Settings
from app.domain.limits import DurationLimits
from app.domain.model_policy import ModelPolicy
from app.domain.record import STATUS_IN_PROGRESS, RecordLifecycle
from app.services.temp_files import TempDirectory
from app.storage.history import History
from app.usecases.active_runs import ActiveRun, ActiveRuns
from app.usecases.errors import RecordBusy
from app.usecases.records import Records
from app.usecases.steps import DownloadFailed, Steps

logger = logging.getLogger(__name__)

Event = tuple[str, dict]
IsCancelled = Callable[[], Awaitable[bool]]


class Events:
    """The events a run reports. The web layer relays them as they are."""

    @staticmethod
    def progress(stage: str, message: str, **fields: object) -> Event:
        return "progress", {"stage": stage, "message": message, **fields}

    @staticmethod
    def error(message: str, record_id: str | None = None) -> Event:
        return "error", {"message": message, "record_id": record_id}

    @staticmethod
    def transcript(**fields: object) -> Event:
        return "transcript", fields

    @staticmethod
    def done() -> Event:
        return "done", {}


@dataclass(frozen=True)
class RunTools:
    """What every run works with."""

    steps: Steps
    history: History
    temp: TempDirectory
    active_runs: ActiveRuns
    limits: DurationLimits


@dataclass(frozen=True)
class RunRequest:
    url: str
    model: str  # already resolved — this exact value is stored and executed
    duration_limit: int  # seconds, 0 = no limit
    is_cancelled: IsCancelled
    existing: dict | None  # the record being re-transcribed, if any


@dataclass(frozen=True)
class AcquiredAudio:
    """A run's audio and what is known about the video it came from."""

    path: Path
    title: str
    duration: float  # seconds, of the whole video
    needs_caching: bool  # False when the record already has this audio beside it


class Transcription:
    def __init__(
        self,
        tools: RunTools,
        records: Records,
        settings: Settings,
        policy: ModelPolicy,
    ) -> None:
        self._tools = tools
        self._records = records
        self._settings = settings
        self._policy = policy

    def transcribe_url(
        self,
        url: str,
        requested_model: str,
        duration_limit: int,
        is_cancelled: IsCancelled,
    ) -> AsyncIterator[Event]:
        """Transcribe ``url`` into a new record, yielding progress as it goes.

        ``duration_limit`` is in seconds (0 = no limit). Raises ``ModelNotAvailable``
        before anything starts; everything later is reported as an ``error`` event.
        """
        return self._start(url, requested_model, duration_limit, is_cancelled, existing=None)

    def retranscribe(
        self,
        record_id: str,
        requested_model: str,
        duration_limit: int,
        is_cancelled: IsCancelled,
    ) -> AsyncIterator[Event]:
        """Transcribe an existing record again, in place, yielding progress as it goes.

        Raises ``RecordNotFound``, ``RecordFromOtherMode``, ``RecordBusy`` or
        ``ModelNotAvailable`` before anything starts. The record is only touched once
        the run has taken it: until then a failure leaves it exactly as it was, and
        afterwards its previous transcript stays in the file unless the new one completes.
        """
        record = self._records.load_to_change(record_id, demo=self._tools.steps.demo)
        if RecordLifecycle.is_in_progress(record):
            # The early answer; RecordStore.reset() refusing an in-progress record is the actual guard
            raise RecordBusy
        return self._start(record["url"], requested_model, duration_limit, is_cancelled, existing=record)

    def _start(
        self,
        url: str,
        requested_model: str,
        duration_limit: int,
        is_cancelled: IsCancelled,
        existing: dict | None,
    ) -> AsyncIterator[Event]:
        model = self._policy.resolve_transcribe_model(requested_model, self._settings.transcribe_model)
        # Before any download: a missing API key would otherwise only show at the first chunk
        self._tools.steps.check_model(model)
        request = RunRequest(url, model, duration_limit, is_cancelled, existing)
        return TranscriptionRun(self._tools, request).events()


class _Cancelled(Exception):
    """The caller went away; stop without saving."""


class _TooLong(Exception):
    """The audio is longer than one transcription may process."""


class _RecordTaken(Exception):
    """The record to re-transcribe could not be put in progress."""


class TranscriptionRun:
    """One transcription from audio to saved record. Use once: ``events()``."""

    def __init__(self, tools: RunTools, request: RunRequest) -> None:
        self._steps = tools.steps
        self._history = tools.history
        self._temp = tools.temp
        self._active_runs = tools.active_runs
        self._limits = tools.limits
        self._request = request
        self._existing = request.existing
        self._record_id: str | None = None  # the record this run has put in progress — None until it has
        self._audio: AcquiredAudio | None = None  # this run's own copy in tmp/ — None until it has one
        self._parts: list[str] = []

    async def events(self) -> AsyncIterator[Event]:
        run = self._active_runs.start()
        try:
            async for event in self._work(run):
                yield event
        except _Cancelled:
            pass  # _finish() marks the record interrupted
        except _TooLong as e:
            logger.warning("Duration guard: %s", e)
            yield Events.error(str(e))
        except _RecordTaken:
            yield Events.error("Record is being processed by another request or no longer exists", self._target_id)
        except DownloadFailed as e:
            logger.error("Download error: %s", e)
            yield self._failed(str(e), str(e))
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            yield self._failed(str(e), f"An error occurred: {e}")
        finally:
            self._finish(run)

    # --- the flow, top to bottom ---------------------------------------------------

    async def _work(self, run: ActiveRun) -> AsyncIterator[Event]:
        request = self._request
        logger.info("Request: %s%s", request.url, f" (retranscribe {self._target_id})" if self._existing else "")

        async for event in self._acquire_audio():
            yield event
        # The one place the length rule is applied, wherever the audio came from
        self._refuse_if_too_long(self._audio.duration)
        await self._stop_if_cancelled("after download")

        await self._open_record(run)
        yield Events.progress("processing", "Processing...", record_id=self._record_id)

        chunks = await self._in_thread(self._steps.prepare_chunks, self._audio.path, request.duration_limit or None)
        if len(chunks) > 1:
            yield Events.progress("transcribing", f"Audio split into {len(chunks)} chunks", record_id=self._record_id)
        async for event in self._transcribe_chunks(chunks):
            yield event
        # Don't save a transcript nobody is waiting for
        await self._stop_if_cancelled("after the last chunk, not saving")

        yield self._save()
        yield Events.done()

    async def _acquire_audio(self) -> AsyncIterator[Event]:
        """Put the audio in tmp/ as ``self._audio`` — from the cache when there is one, otherwise by downloading."""
        cached = self._cached_audio()
        if cached:
            yield Events.progress("downloading", "Using cached audio...")
            prefix = f"retranscribe_{self._existing['id']}" if self._existing else "cached"
            # A copy, so chunking never touches the cached original
            own_copy = await self._in_thread(self._temp.copy_in, cached.path, prefix)
            self._audio = replace(cached, path=own_copy)
            return

        yield Events.progress("downloading", "Downloading audio from YouTube...")
        self._audio = await self._download()
        file_size_mb = self._audio.path.stat().st_size / (1024 * 1024)
        yield Events.progress("downloading", f"Download complete ({file_size_mb:.1f} MB)")

    def _cached_audio(self) -> AcquiredAudio | None:
        """Audio kept from an earlier transcription, described by the record it belongs to.

        Its ``path`` is the cached original: the record's own audio when re-transcribing,
        otherwise that of any earlier record of the same URL.
        """
        cache = self._steps.audio_cache
        if self._existing:
            path = cache.for_record(self._existing["id"])
            # The record already has this audio beside it
            return self._audio_of(self._existing, path, needs_caching=False) if path else None
        found = cache.for_url(self._request.url)
        if not found:
            return None
        path, source_record = found
        # A new record gets its own copy
        return self._audio_of(source_record, path, needs_caching=True)

    async def _download(self) -> AcquiredAudio:
        """Fetch the audio. The step is told the length rule, so a too-long video is refused before it is fetched."""
        path, duration, title = await self._steps.download(self._request.url, self._refuse_if_too_long)
        if self._existing:
            # A re-transcription keeps what the record says about its video
            return self._audio_of(self._existing, path, needs_caching=True)
        return AcquiredAudio(path, title, duration, needs_caching=True)

    def _audio_of(self, record: dict, path: Path, needs_caching: bool) -> AcquiredAudio:
        return AcquiredAudio(path, record["title"], record["duration"], needs_caching)

    async def _open_record(self, run: ActiveRun) -> None:
        """Put the record in progress — reset the existing one or create a new one — and keep its audio."""
        request, records = self._request, self._history.records
        if self._existing:
            if not records.reset(self._existing["id"], model=request.model, duration_limit=request.duration_limit):
                raise _RecordTaken  # another run has it, or it is gone
            self._record_id = self._existing["id"]
        else:
            self._record_id = records.create(
                self._audio.title,
                request.url,
                self._audio.duration,
                model=request.model,
                duration_limit=request.duration_limit,
                demo=self._steps.demo,
            )
        run.record_id = self._record_id
        if self._audio.needs_caching:
            # Cached from the local copy (safe even if the original is deleted concurrently)
            await self._in_thread(self._steps.audio_cache.save, self._record_id, self._audio.path)

    async def _transcribe_chunks(self, chunks: list[Path]) -> AsyncIterator[Event]:
        """Transcribe each chunk into ``self._parts``, resuming from the chunk cache."""
        record_id, total = self._record_id, len(chunks)
        cache_key = (self._request.model, total)
        duration_limit = self._request.duration_limit
        cached_parts = self._history.chunks.load(record_id, *cache_key, duration_limit)
        self._parts = list(cached_parts)
        if cached_parts:
            logger.info("Loaded %d cached chunks for %s", len(cached_parts), record_id)

        chunk_times: list[float] = []
        for index, chunk_path in enumerate(chunks):
            number = index + 1
            position = {"record_id": record_id, "chunk": number, "chunks_total": total}
            if index < len(cached_parts):
                logger.info("Chunk %d/%d cached (%d words), skipping", number, total, len(cached_parts[index].split()))
                if total > 1:
                    yield Events.progress("transcribing", f"Chunk {number} of {total} (cached)", **position)
                continue

            await self._stop_if_cancelled(f"before chunk {number}/{total}")
            if chunk_times and total > 1:
                # ETA from the rolling average of the chunks done so far
                position["eta_seconds"] = round(sum(chunk_times) / len(chunk_times) * (total - index))
            label = f" chunk {number} of {total}" if total > 1 else ""
            yield Events.progress("transcribing", f"Transcribing{label}...", **position)

            text, elapsed = await self._transcribe(chunk_path, number, total)
            chunk_times.append(elapsed)
            self._parts.append(text)
            self._history.chunks.save(record_id, *cache_key, self._parts, duration_limit)

    async def _transcribe(self, chunk_path: Path, number: int, total: int) -> tuple[str, float]:
        """One chunk → (text, seconds it took). An empty answer fails the run."""
        chunk_size_mb = chunk_path.stat().st_size / (1024 * 1024)
        logger.info("Transcribing chunk %d/%d (%.1f MB) for %s", number, total, chunk_size_mb, self._record_id)
        started = time.monotonic()
        text = await self._steps.transcribe_chunk(chunk_path, self._request.model)
        elapsed = time.monotonic() - started
        logger.info(
            "Chunk %d/%d done: %d words, %.1fs, %.1f MB for %s",
            number,
            total,
            len(text.split()),
            elapsed,
            chunk_size_mb,
            self._record_id,
        )
        if not text.strip():
            logger.error("Chunk %d/%d returned empty text for %s, aborting", number, total, self._record_id)
            raise RuntimeError(f"Chunk {number} of {total} returned empty transcription")
        return text, elapsed

    def _save(self) -> Event:
        """Store the transcript — it always starts with the video title — and report it."""
        full_text = f"{self._audio.title}\n\n{' '.join(self._parts)}"
        if not self._history.records.complete(self._record_id, full_text):
            # Never report a transcript that is not in the history
            raise RuntimeError("The transcript could not be saved — its record is gone or unreadable")
        self._history.chunks.delete(self._record_id)
        logger.info(
            "Transcription done: %s (%d chunks, %d words saved)",
            self._record_id,
            len(self._parts),
            sum(len(part.split()) for part in self._parts),
        )
        return Events.transcript(
            text=full_text,
            duration_seconds=self._audio.duration,
            duration_limit=self._request.duration_limit,
            title=self._audio.title,
            record_id=self._record_id,
            model=self._request.model,
        )

    # --- stopping, failing, cleaning up ----------------------------------------------

    @property
    def _target_id(self) -> str | None:
        """The record this run is about, whether or not it has taken it yet."""
        return self._record_id or (self._existing["id"] if self._existing else None)

    async def _stop_if_cancelled(self, where: str) -> None:
        if await self._request.is_cancelled():
            logger.warning("Client disconnected %s%s", where, f" for {self._target_id}" if self._target_id else "")
            raise _Cancelled

    def _refuse_if_too_long(self, duration: float) -> None:
        """A stored record's length was accepted when it was first transcribed, so only new ones are checked."""
        if self._existing:
            return
        too_long = self._limits.too_long_message(duration, self._request.duration_limit)
        if too_long:
            raise _TooLong(too_long)

    def _failed(self, reason: str, message: str) -> Event:
        """Fail the record — only if this run has taken it — and report ``message``."""
        if self._record_id:
            self._history.records.fail(self._record_id, reason)
        return Events.error(message, self._target_id)

    def _finish(self, run: ActiveRun) -> None:
        self._active_runs.finish(run)
        records = self._history.records
        if self._record_id and records.status(self._record_id) == STATUS_IN_PROGRESS:
            records.fail(self._record_id, "Transcription interrupted")
            logger.warning("Marked interrupted record as failed: %s", self._record_id)
        if self._audio:
            self._temp.remove_run_files(self._audio.path)

    async def _in_thread(self, blocking: Callable, *args: object) -> object:
        """Run blocking file or ffmpeg work off the event loop."""
        return await asyncio.get_running_loop().run_in_executor(None, blocking, *args)
