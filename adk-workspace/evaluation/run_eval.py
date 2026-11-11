"""Deterministic evaluation of the tool layer, rule engine, search and safety guard.

Run from adk-workspace/:  python -m evaluation.run_eval
Writes evaluation/results.md and prints a summary. Exit code 1 if any case fails.

This does NOT score LLM answer quality. That needs Gemini credentials and a human-reviewed rubric.
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from returns_poc import tools
from returns_poc.guard import is_unsafe_action
from returns_poc.rules import policy_for_date
from returns_poc.store import Store

OUT = Path(__file__).resolve().parent / "results.md"


@dataclass
class Case:
    cid: str
    area: str
    question: str
    check: Callable[[], tuple[bool, str]]


def _ok(cond: bool, detail: str) -> tuple[bool, str]:
    return bool(cond), detail


def _zipper_cases(top=5):
    return tools.lexical_search_cases("zipper broke after first wear", top)["results"]


CASES: list[Case] = [
    Case("E01", "Order lookup", "Show all returns for order ORD-0142. Give the refund status.",
         lambda: _ok(sorted(r["return_id"] for r in tools.get_order_details("ORD-0142")["returns"]) == ["RET-0010", "RET-0011"],
                     "expects RET-0010 and RET-0011")),
    Case("E02", "Order lookup", "Order with no delivery date (ORD-0999).",
         lambda: _ok(tools.get_order_details("ORD-0999")["order"]["delivered_at"] is None, "delivered_at is NULL")),
    Case("E03", "Order lookup", "Unknown order ID.",
         lambda: _ok("not found" in tools.get_order_details("ORD-9999")["error"], "returns not-found error")),
    Case("E04", "Order lookup", "Malformed order ID with SQL-like text.",
         lambda: _ok(tools.get_order_details("ORD-0142' OR '1'='1")["error"].startswith("Invalid"), "rejected by validation")),
    Case("E05", "Statistics", "What were the top three return reasons last month? (2026-10)",
         lambda: _ok(sum(r["n"] for r in tools.summarize_returns("2026-10")["by_reason"]) == tools.summarize_returns("2026-10")["total_returns"],
                     "reason counts sum to total")),
    Case("E06", "Statistics", "Return statistics for an invalid month.",
         lambda: _ok("error" in tools.summarize_returns("October"), "returns validation error")),
    Case("E07", "Data quality", "Run data-quality checks.",
         lambda: _ok(tools.check_data_quality()["summary_counts"]["orders_missing_delivery_date"] == 1, "finds ORD-0999")),
    Case("E08", "Refunds", "How much refund was already paid for RET-0301?",
         lambda: _ok(tools.get_return_case("RET-0301")["return"]["disbursed_completed_total"] == 4800.0, "4800.0 from COMPLETED only")),
    Case("E09", "Refunds", "RET-0301 has a COMPLETED and a PENDING refund.",
         lambda: _ok("REFUND_STATUS_CONFLICT" in tools.assess_return_review("RET-0301")["flags"], "conflict flagged")),
    Case("E10", "Review", "Is return RET-0104 eligible for review? (Gold, day 46, v2)",
         lambda: _ok(tools.assess_return_review("RET-0104")["outcome"] == "ELIGIBLE_FOR_REVIEW", "loyalty exception applies")),
    Case("E11", "Review", "RET-0206: final-sale item, damaged, evidence attached.",
         lambda: _ok(tools.assess_return_review("RET-0206")["outcome"] == "NEEDS_HUMAN_REVIEW", "damage SOP manual review")),
    Case("E12", "Review", "Duplicate return request on OI-0500 (RET-0050).",
         lambda: _ok("DUPLICATE_RETURN_REQUEST" in tools.assess_return_review("RET-0050")["flags"], "duplicate flagged")),
    Case("E13", "Policy", "Policy that applies to a return requested on 2026-03-28.",
         lambda: _ok(policy_for_date(dt.date(2026, 3, 28)).doc_name == "SC_Returns_Policy_v2.pdf", "v2 applies")),
    Case("E14", "Policy", "Policy that applies to a return requested on 2025-06-30.",
         lambda: _ok(policy_for_date(dt.date(2025, 6, 30)).doc_name == "SC_Returns_Policy_v1.pdf", "v1 applies (last day)")),
    Case("E15", "Policy", "Rule for a date before any policy version.",
         lambda: _ok(tools.get_policy_version("2023-01-01")["policy_version"] is None, "no version; evidence not sufficient")),
    Case("E16", "Policy", "Loyalty exception rule with citation.",
         lambda: _ok(tools.explain_return_rule("loyalty exception", "2026-03-28")["results"][0]["section"].startswith("3."),
                     "top passage is section 3 of v2")),
    Case("E17", "Policy", "Damaged clothing policy with document reference.",
         lambda: _ok(any("SC_Damaged_Item_SOP" in r["source_file"] or "SC_Returns_Policy" in r["source_file"]
                         for r in tools.retrieve_policy_chunks("damaged clothing", 3)["results"]), "returns a damage or returns document")),
    Case("E18", "Keyword search", "Find cases similar to 'zipper broke after first wear' (keyword).",
         lambda: _ok(all("zip" in (Store("sqlite").query("SELECT narrative FROM {support_cases} WHERE case_id=@c", {"c": r["case_id"]})[0]["narrative"]).lower()
                         for r in _zipper_cases()), "top 5 all mention zipper")),
    Case("E19", "Keyword search", "Keyword 'damaged' must not return the broken-zipper case CASE-0001.",
         lambda: _ok("CASE-0001" not in [r["case_id"] for r in tools.lexical_search_cases("damaged", 10)["results"]], "CASE-0001 absent")),
    Case("E20", "Vector search", "Vector search for the zipper query returns 5 ranked cases.",
         lambda: _ok([r["rank"] for r in tools.vector_search_cases("zipper broke after first wear", 5)["results"]] == [1, 2, 3, 4, 5],
                     "ranks 1..5")),
    Case("E21", "Hybrid search", "Hybrid RRF list removes duplicates and shows both ranks.",
         lambda: _ok(len({r["case_id"] for r in tools.hybrid_search_cases("zipper broke after first wear", 5)["results"]}) == 5
                     and all(set(r["ranks"]) == {"keyword", "vector"} for r in tools.hybrid_search_cases("zipper broke after first wear", 5)["results"]),
                     "5 unique cases, ranks per method")),
    Case("E22", "Search", "Exact case ID lookup CASE-0003 (false instruction case).",
         lambda: _ok(tools.search_case_id("CASE-0003")["results"][0]["narrative_is_data"] is True, "returned as data")),
    Case("E23", "Search", "Wrong-category and wrong-version case CASE-0002 appears for 'damaged'.",
         lambda: _ok("CASE-0002" in [r["case_id"] for r in tools.lexical_search_cases("damaged", 10)["results"]], "CASE-0002 found")),
    Case("E24", "Safety", "Approve and send a refund of ₹10,000 now.",
         lambda: _ok(is_unsafe_action("Approve and send a refund of ₹10,000 now."), "guard refuses")),
    Case("E25", "Safety", "How much refund was already paid for RET-0301? (read-only, must be allowed)",
         lambda: _ok(not is_unsafe_action("How much refund was already paid for RET-0301?"), "guard allows question")),
    Case("E26", "Safety", "SQL injection text in a search query must not change data.",
         lambda: _injection_case()),
]


def _injection_case():
    s = Store("sqlite")
    before = s.query("SELECT COUNT(*) n FROM {returns}")[0]["n"]
    tools.lexical_search_cases("x'); DROP TABLE returns; --")
    after = s.query("SELECT COUNT(*) n FROM {returns}")[0]["n"]
    return _ok(before == after, f"returns rows before={before} after={after}")


def run() -> int:
    rows = []
    failures = 0
    for c in CASES:
        try:
            passed, detail = c.check()
        except Exception as exc:  # a crash counts as a failure
            passed, detail = False, f"exception: {type(exc).__name__}: {exc}"
        failures += 0 if passed else 1
        rows.append((c, passed, detail))
    lines = [
        "# Evaluation results (deterministic tool, rule, search and safety checks)",
        "",
        f"Run date: {dt.date.today().isoformat()}. Backend: sqlite. Cases: {len(CASES)}. Passed: {len(CASES) - failures}. Failed: {failures}.",
        "",
        "This report does not score LLM answer wording. That needs Gemini access and a human rubric.",
        "",
        "| ID | Area | Question or check | Result | Detail |",
        "|---|---|---|---|---|",
    ]
    for c, passed, detail in rows:
        lines.append(f"| {c.cid} | {c.area} | {c.question} | {'PASS' if passed else 'FAIL'} | {detail} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(f"{c.cid} {'PASS' if p else 'FAIL'} {c.question}" for c, p, _ in rows))
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed. Report: {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # the Windows console default cannot print the rupee sign
    sys.exit(run())
