"""Which transcriptions this process is running right now."""

from dataclasses import dataclass


@dataclass(eq=False)
class ActiveRun:
    record_id: str | None = None  # set once the run has put a record in progress


class ActiveRuns:
    """The server is a single process, so this in-memory registry is exact: cleanup asks
    it what is alive instead of guessing from file timestamps. Records left in
    progress by a previous process are handled at startup, when nothing can be running.
    """

    def __init__(self) -> None:
        self._runs: set[ActiveRun] = set()

    def start(self) -> ActiveRun:
        run = ActiveRun()
        self._runs.add(run)
        return run

    def finish(self, run: ActiveRun) -> None:
        self._runs.discard(run)

    def any_active(self) -> bool:
        """True while any transcription is anywhere between download and save."""
        return bool(self._runs)

    def record_ids(self) -> set[str]:
        return {run.record_id for run in self._runs if run.record_id}
