import time

from fastapi.testclient import TestClient


def test_health() -> None:
    from api.main import app

    with TestClient(app) as client:
        r = client.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "version": "0.1.0"}


def test_city_summary() -> None:
    from api.main import app

    with TestClient(app) as client:
        r = client.get("/api/v1/_debug/city-summary")
        assert r.status_code == 200
        body = r.json()
        assert body["intersections"] == 36
        assert body["roads"] == 120


def test_sim_snapshot_unavailable_without_dev_sim() -> None:
    from api.main import app

    with TestClient(app) as client:
        r = client.get("/api/v1/sim/snapshot")
        assert r.status_code == 503


def test_sim_snapshot_available_with_dev_sim(monkeypatch) -> None:
    monkeypatch.setenv("DEV_SIM", "1")
    import importlib

    import api.main as main_module

    importlib.reload(main_module)

    with TestClient(main_module.app) as client:
        time.sleep(0.3)
        r = client.get("/api/v1/sim/snapshot")
        assert r.status_code == 200
        snapshot = r.json()
        assert "ambulances" in snapshot
        assert "AMB-1" in snapshot["ambulances"]

    importlib.reload(main_module)  # restore module state for later tests
