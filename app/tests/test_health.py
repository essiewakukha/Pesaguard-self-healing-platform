from fastapi.testclient import TestClient

from pesaguard.main import create_app
from pesaguard.repository import InMemoryRepository, RepositoryUnavailable

from .conftest import make_settings


def test_health_reports_version_and_color(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": "v9.9.9-test", "color": "blue"}


def test_every_response_carries_version_and_request_id(client):
    resp = client.get("/health", headers={"x-request-id": "abc-123"})
    assert resp.headers["x-app-version"] == "v9.9.9-test"
    assert resp.headers["x-request-id"] == "abc-123"


def test_ready_when_database_reachable(client):
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


class BrokenRepo(InMemoryRepository):
    def ping(self):
        raise RepositoryUnavailable("connection refused")


def test_not_ready_when_database_unreachable():
    app = create_app(make_settings(), repository=BrokenRepo())
    with TestClient(app) as c:
        resp = c.get("/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["database"] == "unreachable"
        # Liveness must stay green: restarting the container would not fix the DB.
        assert c.get("/health").status_code == 200