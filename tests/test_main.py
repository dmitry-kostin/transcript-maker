from fastapi.testclient import TestClient

from app.main import Application


class TestStartup:
    def test_building_the_app_does_not_touch_records(self, history):
        """Stale-record cleanup belongs to server startup, not to importing or constructing the app."""
        rid = history.records.create("Running", "https://youtube.com/watch?v=abc", 60)
        Application()
        assert history.records.get(rid)["status"] == "in_progress"

    def test_server_startup_fails_records_left_by_the_previous_run(self, history):
        rid = history.records.create("Left over", "https://youtube.com/watch?v=abc", 60)
        with TestClient(Application()):
            pass
        record = history.records.get(rid)
        assert record["status"] == "error"
        assert record["error"] == "Server restarted during transcription"


class TestNothingAtImport:
    def test_module_does_not_build_an_app(self):
        """The server builds it through the factory; importing app.main configures nothing."""
        import app.main
        assert not hasattr(app.main, "app")


class TestPages:
    def test_index_is_served_with_cache_busted_assets(self, client):
        res = client.get("/")
        assert res.status_code == 200
        assert "/static/app.js?v=" in res.text

    def test_each_app_has_its_own_wiring(self, tmp_results):
        """Two apps never share run state — nothing lives in module globals."""
        first, second = Application(), Application()
        assert first._wiring.maintenance is not second._wiring.maintenance
