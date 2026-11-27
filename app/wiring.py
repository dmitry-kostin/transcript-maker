"""The composition root: every object the app is made of, built once and handed its collaborators."""

from app.adapters import StepsFactory
from app.config import Settings
from app.domain.limits import DurationLimits
from app.domain.model_policy import ModelPolicy
from app.services.chunker import AudioChunker
from app.services.clients import ClientFactory
from app.services.demo import DemoServices
from app.services.desktop import Desktop
from app.services.downloader import Downloader
from app.services.summarizer import Summarizer
from app.services.temp_files import TempDirectory
from app.services.transcriber import Transcriber
from app.storage.history import History
from app.usecases.active_runs import ActiveRuns
from app.usecases.maintenance import Maintenance
from app.usecases.providers import Providers
from app.usecases.records import Records
from app.usecases.steps import Steps
from app.usecases.summarization import Summarization
from app.usecases.transcription import RunTools, Transcription


class Wiring:
    """Real and demo mode are the same use-case classes built with different ``Steps``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._policy = ModelPolicy()
        self._history = History.in_results_dir(settings)
        self._temp = TempDirectory(settings)
        self._active_runs = ActiveRuns()

        self.records = Records(self._history, Desktop(), self._active_runs)
        self.providers = Providers(settings, self._policy)
        self.maintenance = Maintenance(self._history, self._temp, self._active_runs)

        real, demo = self._real_steps(), StepsFactory().demo(DemoServices(settings))
        self.transcription = self._transcription(real)
        self.summarization = self._summarization(real)
        self.demo_transcription = self._transcription(demo)
        self.demo_summarization = self._summarization(demo)

    def _real_steps(self) -> Steps:
        clients = ClientFactory(self._settings, self._policy)
        return StepsFactory().real(
            Downloader(self._settings),
            AudioChunker(self._settings),
            Transcriber(clients, self._policy),
            Summarizer(clients, self._policy),
            clients,
            self._history,
        )

    def _transcription(self, steps: Steps) -> Transcription:
        tools = RunTools(steps, self._history, self._temp, self._active_runs, DurationLimits())
        return Transcription(tools, self.records, self._settings, self._policy)

    def _summarization(self, steps: Steps) -> Summarization:
        return Summarization(steps, self._history, self.records, self._settings, self._policy)
