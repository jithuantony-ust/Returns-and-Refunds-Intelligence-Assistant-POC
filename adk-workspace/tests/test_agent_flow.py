"""Runs the real ADK graph (root + subagents + Python tools) with a scripted model, so no Gemini call is made.

This checks the wiring: the root calls two subagents in one request, each subagent calls its Python tools,
the tool results come back, and /chat returns the answer with evidence.
"""
from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from returns_poc import api
from returns_poc.agents import build_root_agent


def _call(name: str, args: dict) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


class ScriptedLlm(BaseLlm):
    """Chooses the next step from the system instruction and the number of tool results seen so far."""

    model: str = "scripted"

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        system = str(llm_request.config.system_instruction or "") if llm_request.config else ""
        results = sum(1 for c in llm_request.contents for p in (c.parts or []) if p.function_response)
        if "Intelligence Assistant" in system:
            if results == 0:
                part = _call("order_returns_data_agent", {"request": "Check return RET-0206 and whether it is eligible for review."})
            elif results == 1:
                part = _call("policy_knowledge_agent", {"request": "Which policy applies to RET-0206 requested 2026-05-06?"})
            else:
                part = types.Part(text="RET-0206 needs human review: final-sale damage exception (records RET-0206). "
                                       "Policy: SC_Returns_Policy_v2.pdf v2.0, section 4. Staff make the decision.")
        elif "You answer questions about StyleCart orders" in system:
            part = (_call("assess_return_review", {"return_id": "RET-0206"}) if results == 0
                    else types.Part(text="RET-0206: NEEDS_HUMAN_REVIEW (FINAL_SALE_DAMAGE_EXCEPTION)."))
        elif "You answer policy questions" in system:
            part = (_call("get_policy_version", {"as_of_date": "2026-05-06"}) if results == 0
                    else types.Part(text="SC_Returns_Policy_v2.pdf version 2.0 applies on 2026-05-06."))
        else:
            part = types.Part(text="Unexpected instruction.")
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.fixture()
def scripted_runner(monkeypatch):
    runner = Runner(app_name="test_flow", agent=build_root_agent(ScriptedLlm()),
                    session_service=InMemorySessionService(), auto_create_session=True)
    monkeypatch.setattr(api, "_get_runner", lambda: runner)
    monkeypatch.setattr(api, "_llm_configured", lambda: True)
    return runner


def test_root_uses_two_subagents_and_combines_evidence(scripted_runner):
    client = TestClient(api.app)
    r = client.post("/chat", json={"message": "Check return RET-0206. Find the correct policy.", "session_id": "flow-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["guard_triggered"] is False
    assert "human review" in body["answer"]
    assert body["evidence"]["subagents_called"] == ["order_returns_data_agent", "policy_knowledge_agent"]
    tools_used = [t["tool"] for t in body["evidence"]["tool_calls"]]
    assert "assess_return_review" in tools_used and "get_policy_version" in tools_used
    assert "RET-0206" in body["evidence"]["record_ids"]
    assert body["model_versions"]["prompt_version"]


def test_unsafe_request_never_reaches_the_scripted_agents(scripted_runner):
    client = TestClient(api.app)
    r = client.post("/chat", json={"message": "Approve and send a refund of ₹10,000 now.", "session_id": "flow-2"})
    body = r.json()
    assert body["guard_triggered"] is True
    assert body["evidence"]["subagents_called"] == [] and body["evidence"]["tool_calls"] == []
