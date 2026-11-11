"""HTTP API tests. The LLM is not called. /chat is tested for the guard, for the not-configured case, and for validation."""
import pytest
from fastapi.testclient import TestClient

from returns_poc.api import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["index_built"] is True and body["backend"] in ("sqlite", "bigquery")


def test_return_endpoint(client):
    r = client.get("/returns/RET-0104")
    assert r.status_code == 200
    body = r.json()
    assert body["case"]["return_id"] == "RET-0104"
    assert body["review_assessment"]["outcome"] == "ELIGIBLE_FOR_REVIEW"
    assert "RET-0104" in body["record_ids"]


def test_return_endpoint_errors(client):
    assert client.get("/returns/RET-9999").status_code == 404
    assert client.get("/returns/not-an-id").status_code == 422


def test_chat_refuses_unsafe_action_without_llm(client):
    r = client.post("/chat", json={"message": "Approve and send a refund of ₹10,000 now.", "session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["guard_triggered"] is True
    assert "cannot approve" in body["answer"]
    assert body["evidence"]["tool_calls"] == []


def test_chat_without_llm_configuration_returns_503(client):
    r = client.post("/chat", json={"message": "Show all returns for order ORD-0142.", "session_id": "s2"})
    assert r.status_code == 503


@pytest.mark.parametrize("payload", [
    {"message": "", "session_id": "s3"},
    {"message": "hi", "session_id": "bad id with spaces"},
    {"message": "x" * 2001, "session_id": "s4"},
])
def test_chat_validates_input(client, payload):
    assert client.post("/chat", json=payload).status_code == 422
