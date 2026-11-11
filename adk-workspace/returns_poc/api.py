"""FastAPI service: POST /chat, GET /returns/{return_id}, GET /health.

Run from adk-workspace/:  uvicorn returns_poc.api:app --reload
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from returns_poc import tools
from returns_poc.config import (
    BACKEND,
    GEMINI_MODEL,
    MAX_LLM_CALLS,
    MODEL_PROMPT_VERSION,
    REQUEST_TIMEOUT_S,
)
from returns_poc.guard import REFUSAL, is_unsafe_action
from returns_poc.retrieval import index_manifest

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("returns_poc.api")

APP_NAME = "returns_refunds_poc"
app = FastAPI(title="StyleCart Returns & Refunds Assistant (POC)", version="1.0.0")

_runner = None  # created on first /chat call, so /health and /returns work without an LLM


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    guard_triggered: bool
    evidence: dict
    model_versions: dict
    latency_ms: int


def _llm_configured() -> bool:
    return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_USE_VERTEXAI"))


def _get_runner():
    global _runner
    if _runner is None:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from returns_poc.agents import root_agent

        _runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=InMemorySessionService(),
                         auto_create_session=True)
    return _runner


def _model_versions() -> dict:
    m = index_manifest()
    return {"llm_model": GEMINI_MODEL, "prompt_version": MODEL_PROMPT_VERSION,
            "embedding_model": m.get("embedding_model_version"), "index_built_at": m.get("built_at")}


async def _run_agent(message: str, session_id: str) -> tuple[str, dict]:
    from google.genai import types
    from google.adk.agents.run_config import RunConfig

    runner = _get_runner()
    content = types.Content(role="user", parts=[types.Part(text=message)])
    run_cfg = RunConfig(max_llm_calls=MAX_LLM_CALLS)
    answer = ""
    agent_calls: list[str] = []
    sink: list[dict] = []
    token = tools.EVIDENCE.set(sink)
    try:
        async for event in runner.run_async(user_id="staff", session_id=session_id, new_message=content,
                                            run_config=run_cfg):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if getattr(part, "function_call", None) and part.function_call.name:
                    agent_calls.append(part.function_call.name)
            if event.is_final_response():
                answer = "".join(p.text or "" for p in event.content.parts if getattr(p, "text", None))
    finally:
        tools.EVIDENCE.reset(token)
    record_ids = sorted({rid for s in sink for rid in s["record_ids"]})
    evidence = {
        "subagents_called": agent_calls,
        "tool_calls": [{"tool": s["tool"], "record_ids": s["record_ids"], "error": s["error"]} for s in sink],
        "record_ids": record_ids,
    }
    return answer, evidence


@app.get("/health")
def health():
    manifest_ok = bool(index_manifest())
    return {"status": "ok", "backend": BACKEND, "index_built": manifest_ok,
            "llm_configured": _llm_configured(), "model": GEMINI_MODEL}


@app.get("/returns/{return_id}")
def get_return(return_id: str):
    case = tools.get_return_case(return_id)
    if "error" in case:
        status = 404 if "not found" in case["error"] else 422
        return JSONResponse(status_code=status, content=case)
    review = tools.assess_return_review(return_id)
    return {"return_id": return_id, "case": case["return"], "review_assessment": review,
            "record_ids": case["record_ids"]}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    start = time.perf_counter()
    request_id = uuid.uuid4().hex[:8]
    # Log IDs and sizes only. Never log the message text or the answer.
    log.info("chat request_id=%s session=%s chars=%d", request_id, req.session_id, len(req.message))
    if is_unsafe_action(req.message):
        log.info("guard triggered request_id=%s", request_id)
        return ChatResponse(session_id=req.session_id, answer=REFUSAL, guard_triggered=True,
                            evidence={"subagents_called": [], "tool_calls": [], "record_ids": []},
                            model_versions=_model_versions(),
                            latency_ms=int((time.perf_counter() - start) * 1000))
    if not _llm_configured():
        raise HTTPException(status_code=503, detail="The LLM is not configured. Set GOOGLE_API_KEY or Vertex AI settings. "
                                                   "The /returns and /health endpoints still work.")
    try:
        answer, evidence = await asyncio.wait_for(_run_agent(req.message, req.session_id), timeout=REQUEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("chat timeout request_id=%s", request_id)
        raise HTTPException(status_code=504, detail="The request took too long. Try a narrower question.")
    except Exception as exc:
        log.exception("chat failed request_id=%s", request_id)
        raise HTTPException(status_code=500, detail=f"The assistant failed ({type(exc).__name__}). See service logs with request_id {request_id}.")
    log.info("chat done request_id=%s tools=%s ms=%d", request_id, evidence["tool_calls"],
             int((time.perf_counter() - start) * 1000))
    return ChatResponse(session_id=req.session_id, answer=answer or "No answer was produced.", guard_triggered=False,
                        evidence=evidence, model_versions=_model_versions(),
                        latency_ms=int((time.perf_counter() - start) * 1000))
