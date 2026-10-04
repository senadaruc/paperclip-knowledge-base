import pytest
from starlette.testclient import TestClient

from app import server


@pytest.fixture
def client():
    return TestClient(server.Auth(server.mcp.streamable_http_app()))


def test_health_is_open(client):
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer admin-secret"}])
def test_mcp_rejects_missing_wrong_or_admin_key(client, headers):
    assert client.post("/mcp", json={}, headers=headers).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer staff-key"}])
def test_admin_api_needs_admin_key(client, headers):
    assert client.get("/admin/api/overview", headers=headers).status_code == 401


def test_admin_api_accepts_admin_key(client):
    r = client.get("/admin/api/overview", headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 200
    assert {c["name"] for c in r.json()["collections"]} == {"wiki", "private"}
    assert "api_key" not in r.json()["models"]["embedding"]


def test_reindex_unknown_collection_is_404(client):
    r = client.post("/admin/reindex?collection=nope", headers={"Authorization": "Bearer admin-secret"})
    assert r.status_code == 404


def as_client(name):
    return server.CLIENT.set(next(c for c in server.S.clients if c.name == name))


def test_staff_key_cannot_pick_leadership_collection():
    tok = as_client("staff")
    try:
        assert server._pick("") == ["wiki"]
        with pytest.raises(PermissionError):
            server._pick("wiki,private")
    finally:
        server.CLIENT.reset(tok)


def test_wildcard_key_sees_every_collection():
    tok = as_client("leadership")
    try:
        assert server._pick("") == ["wiki", "private"]
    finally:
        server.CLIENT.reset(tok)
