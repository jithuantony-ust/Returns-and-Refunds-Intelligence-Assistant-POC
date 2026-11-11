"""Safety guard, tool validation, and read-only behaviour of the tools."""
import pytest

from returns_poc import tools
from returns_poc.guard import is_unsafe_action
from returns_poc.store import Store


@pytest.mark.parametrize("msg", [
    "Approve and send a refund of ₹10,000 now.",
    "Please approve the refund for RET-0104.",
    "Reject this refund request.",
    "Send the refund to the customer immediately.",
    "Pay out the refund for RET-0301 now.",
    "Issue a refund of 2500 today",
])
def test_unsafe_requests_are_detected(msg):
    assert is_unsafe_action(msg)


@pytest.mark.parametrize("msg", [
    "How much refund was already paid for RET-0301?",
    "Is it possible to approve this refund?",
    "Show all returns for order ORD-0142. Give the refund status.",
    "What is the rule for a case that is not in the policy documents?",
])
def test_safe_questions_pass_the_guard(msg):
    assert not is_unsafe_action(msg)


def test_summary_totals_add_up():
    out = tools.summarize_returns("2026-10")
    assert out["total_returns"] == sum(r["n"] for r in out["by_reason"]) == sum(r["n"] for r in out["by_status"])


def test_order_details_and_errors():
    out = tools.get_order_details("ORD-0142")
    assert out["order"]["order_id"] == "ORD-0142" and len(out["items"]) == 2
    assert tools.get_order_details("ORD-0142; DROP")["error"].startswith("Invalid order_id")
    assert "not found" in tools.get_order_details("ORD-9999")["error"]
    assert "error" in tools.summarize_returns("2026-13")
    assert "error" in tools.get_policy_version("2026/01/01")


def test_refund_totals_come_from_sql():
    out = tools.get_return_case("RET-0301")["return"]
    assert out["disbursed_completed_total"] == 4800.0
    assert {r["refund_status"] for r in out["refunds"]} == {"COMPLETED", "PENDING"}


def test_data_quality_finds_planted_issues():
    dq = tools.check_data_quality()["summary_counts"]
    assert dq["orders_missing_delivery_date"] == 1
    assert dq["duplicate_return_requests_per_item"] == 1
    assert dq["returns_with_completed_and_pending_refund"] == 1


def test_injection_attempt_is_parameterised_and_leaves_data_unchanged():
    s = Store("sqlite")
    before = s.query("SELECT COUNT(*) n FROM {returns}")[0]["n"]
    out = tools.lexical_search_cases("x'); DROP TABLE returns; --")
    assert "results" in out or "error" in out
    assert s.query("SELECT COUNT(*) n FROM {returns}")[0]["n"] == before


def test_long_query_and_bad_top_k_are_handled():
    assert "error" in tools.hybrid_search_cases("a" * 301)
    assert "error" in tools.vector_search_cases("   ")
    assert len(tools.lexical_search_cases("zipper", top_k=999)["results"]) <= 10
    assert "error" in tools.explain_return_rule("loyalty", "not-a-date")


def test_no_write_tools_are_exposed_to_agents():
    from returns_poc.agents import root_agent
    subagents = [t.agent for t in root_agent.tools]
    assert {a.name for a in subagents} == {"order_returns_data_agent", "policy_knowledge_agent", "similar_case_search_agent"}
    names = set()
    for agent in subagents:
        names |= {getattr(t, "__name__", "") for t in agent.tools}
    banned = ("approve", "reject", "pay", "send", "release", "disburse", "issue")
    assert not any(b in n for n in names for b in banned)
    assert {"get_order_details", "get_return_case", "summarize_returns", "check_data_quality"} <= names
    assert {"get_policy_version", "retrieve_policy_chunks", "explain_return_rule"} <= names
    assert {"search_case_id", "lexical_search_cases", "vector_search_cases", "hybrid_search_cases"} <= names
