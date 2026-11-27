"""Everything the app keeps in ``results/``, wired together."""
from dataclasses import dataclass

from app.config import Settings
from app.storage.audio_cache import AudioCache
from app.storage.chunk_cache import ChunkCache
from app.storage.record_store import RecordStore
from app.storage.results_directory import ResultsDirectory
from app.storage.summary_store import SummaryStore


@dataclass(frozen=True)
class History:
    records: RecordStore
    summaries: SummaryStore
    audio: AudioCache
    chunks: ChunkCache

    @classmethod
    def in_results_dir(cls, settings: Settings) -> "History":
        directory = ResultsDirectory(settings)
        summaries, audio, chunks = SummaryStore(directory), AudioCache(directory), ChunkCache(directory)
        return cls(RecordStore(directory, summaries, audio, chunks), summaries, audio, chunks)
