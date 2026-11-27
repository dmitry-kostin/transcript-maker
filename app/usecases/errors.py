"""Why a use case refused. The web layer turns these into status codes; the text is shown to the user."""


class UseCaseError(Exception):
    message = "Request refused"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)


class RecordNotFound(UseCaseError):
    message = "Not found"


class RecordBusy(UseCaseError):
    """Something is transcribing this record right now."""

    message = "Record is currently being processed"


class RecordNotCompleted(UseCaseError):
    """Only a finished transcript can be summarized."""

    message = "Record is not completed"


class RecordFromOtherMode(UseCaseError):
    """Demo mode may not change a real record, nor real mode a demo one."""


class ModelNotAvailable(UseCaseError):
    """The provider that serves the requested model is not configured."""


class SummaryNotFound(UseCaseError):
    message = "No summary found"


class SummaryNotSaved(UseCaseError):
    """The summary was generated but could not be written."""

    message = "Failed to save summary"
