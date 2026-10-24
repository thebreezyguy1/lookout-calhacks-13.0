from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_redirects_to_docs() -> None:
    resp = TestClient(app).get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/docs"
