import inspect

import app.usecases.errors as use_case_errors
from app.usecases.errors import UseCaseError
from app.web.errors import ErrorResponses


class TestErrorResponses:
    def test_every_use_case_error_has_a_status_code(self):
        """A new refusal must say how it reads over HTTP — not fall back to the default by accident."""
        refusals = [
            error for _, error in inspect.getmembers(use_case_errors, inspect.isclass)
            if issubclass(error, UseCaseError) and error is not UseCaseError
        ]
        assert set(refusals) == set(ErrorResponses.STATUS_FOR)

    def test_error_shape(self, client):
        res = client.get("/api/history/00000000")
        assert (res.status_code, res.json()) == (404, {"error": "Not found"})
        res = client.get("/api/history/not-an-id")
        assert (res.status_code, res.json()) == (400, {"error": "Invalid ID"})
