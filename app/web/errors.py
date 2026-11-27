"""How failures reach the client: always ``{"error": message}`` with a status code."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.usecases.errors import (
    ModelNotAvailable,
    RecordBusy,
    RecordFromOtherMode,
    RecordNotCompleted,
    RecordNotFound,
    SummaryNotFound,
    SummaryNotSaved,
    UseCaseError,
)


class ApiError(Exception):
    """An error the client should see as ``{"error": message}`` with this status code."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ErrorResponses:
    """A use case that refuses says why; this says how that reads over HTTP."""

    STATUS_FOR: dict[type[UseCaseError], int] = {
        RecordNotFound: 404,
        SummaryNotFound: 404,
        RecordBusy: 409,
        RecordFromOtherMode: 409,
        RecordNotCompleted: 400,
        ModelNotAvailable: 400,
        SummaryNotSaved: 500,
    }

    def install(self, app: FastAPI) -> None:
        app.add_exception_handler(ApiError, self.for_api_error)
        app.add_exception_handler(UseCaseError, self.for_use_case_error)

    async def for_api_error(self, request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    async def for_use_case_error(self, request: Request, exc: UseCaseError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=self.STATUS_FOR.get(type(exc), 400))
