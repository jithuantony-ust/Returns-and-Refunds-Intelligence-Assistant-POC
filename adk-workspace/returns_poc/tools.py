"""Python tools called by the ADK agents.

Rules for every tool:
  * Validate inputs first. Return {"error": ...} for bad input. Never raise into the model.
  * Use fixed SQL templates with bound parameters only.
  * Read-only. No tool approves, rejects, pays or sends a refund.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from contextvars import ContextVar
from functools import lru_cache

from returns_poc.config import MAX_TOP_K
from returns_poc.retrieval import build_case_index, policy_index
from returns_poc.rules import DAMAGE_SOP, REFUND_SOP, assess_return, policy_for_date
from returns_poc.store import Store

log = logging.getLogger("returns_poc.tools")

ORDER_RE = re.compile(r"^ORD-\d{4}$")
RETURN_RE = re.compile(r"^RET-\d{4}$")
CASE_RE = re.compile(r"^CASE-\d{4}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DOC_RE = re.compile(r"^SC_[A-Za-z_]+\.pdf$")
MAX_QUERY_LEN = 300


@lru_cache(maxsize=1)
def store() -> Store:
    return Store()


@lru_cache(maxsize=1)
def case_index():
    return build_case_index(store())


# Per-request evidence sink. The API sets it before running the agents, so record IDs from tools that run
# inside subagents still reach the response. Subagent results reach the root agent only as text.
EVIDENCE: ContextVar[list | None] = ContextVar("evidence_sink", default=None)


def _timed(name: str, fn, *args, **kwargs):
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # return a safe error to the model; details go to the log only
        log.exception("tool %s failed", name)
        result = {"error": f"{name} failed. Check the service logs. Error type: {type(exc).__name__}."}
    log.info("tool=%s ms=%.1f", name, (time.perf_counter() - start) * 1000)
    sink = EVIDENCE.get()
    if sink is not None and isinstance(result, dict):
        sink.append({"tool": name, "record_ids": list(result.get("record_ids", [])), "error": "error" in result})
    return result


def _id(value, rx, label):
    if not isinstance(value, str) or not rx.match(value.strip()):
        return None, {"error": f"Invalid {label}. Expected a format like {'ORD-0142' if label == 'order_id' else 'RET-0104' if label == 'return_id' else 'CASE-0001'}."}
    return value.strip(), None


def _date(value: str):
    try:
        return dt.date.fromisoformat(value), None
    except (TypeError, ValueError):
        return None, {"error": "Invalid date. Use YYYY-MM-DD."}


def _top_k(value) -> int:
    try:
        return max(1, min(int(value), MAX_TOP_K))
    except (TypeError, ValueError):
        return 5


def _query_text(value) -> tuple[str | None, dict | None]:
    if not isinstance(value, str) or not value.strip():
        return None, {"error": "Query is empty."}
    if len(value) > MAX_QUERY_LEN:
        return None, {"error": f"Query is longer than {MAX_QUERY_LEN} characters."}
    return value.strip(), None


# ---------------------------------------------------------------- Order and returns data

def get_order_details(order_id: str) -> dict:
    """Return an order with its customer tier, items, return requests and refund records.

    Args:
        order_id: Order ID such as ORD-0142.
    """
    def run():
        oid, err = _id(order_id, ORDER_RE, "order_id")
        if err:
            return err
        s = store()
        orders = s.query(
            "SELECT o.order_id, o.customer_id, o.order_date, o.delivered_at, o.payment_status, o.order_total, "
            "c.loyalty_tier FROM {orders} o LEFT JOIN {customers} c ON c.customer_id = o.customer_id "
            "WHERE o.order_id = @order_id", {"order_id": oid})
        if not orders:
            return {"error": f"Order {oid} was not found."}
        items = s.query("SELECT order_item_id, sku, item_category, item_price, qty, final_sale FROM {order_items} "
                        "WHERE order_id = @order_id ORDER BY order_item_id", {"order_id": oid})
        returns = s.query(
            "SELECT r.return_id, r.order_item_id, r.request_date, r.reason_code, r.status, r.requested_refund, "
            "r.evidence_attached FROM {returns} r JOIN {order_items} oi ON oi.order_item_id = r.order_item_id "
            "WHERE oi.order_id = @order_id ORDER BY r.return_id", {"order_id": oid})
        refunds = s.query(
            "SELECT f.refund_id, f.return_id, f.approved_amount, f.disbursed_amount, f.refund_status, f.updated_at "
            "FROM {refunds} f JOIN {returns} r ON r.return_id = f.return_id "
            "JOIN {order_items} oi ON oi.order_item_id = r.order_item_id "
            "WHERE oi.order_id = @order_id ORDER BY f.refund_id", {"order_id": oid})
        return {"order": orders[0], "items": items, "returns": returns, "refunds": refunds,
                "record_ids": [oid] + [r["return_id"] for r in returns] + [f["refund_id"] for f in refunds]}
    return _timed("get_order_details", run)


def _return_facts(return_id: str) -> dict:
    s = store()
    rows = s.query(
        "SELECT r.return_id, r.order_item_id, r.request_date, r.reason_code, r.status, r.requested_refund, "
        "r.evidence_attached, oi.order_id, oi.sku, oi.item_category, oi.item_price, oi.qty, oi.final_sale, "
        "o.order_date, o.delivered_at, o.payment_status, c.loyalty_tier "
        "FROM {returns} r JOIN {order_items} oi ON oi.order_item_id = r.order_item_id "
        "JOIN {orders} o ON o.order_id = oi.order_id LEFT JOIN {customers} c ON c.customer_id = o.customer_id "
        "WHERE r.return_id = @return_id", {"return_id": return_id})
    if not rows:
        return {}
    facts = rows[0]
    facts["refunds"] = s.query(
        "SELECT refund_id, approved_amount, disbursed_amount, refund_status, updated_at FROM {refunds} "
        "WHERE return_id = @return_id ORDER BY refund_id", {"return_id": return_id})
    same_item = s.query("SELECT COUNT(*) AS n FROM {returns} WHERE order_item_id = @item",
                        {"item": facts["order_item_id"]})
    facts["same_item_return_count"] = same_item[0]["n"]
    paid = s.query("SELECT COALESCE(SUM(disbursed_amount), 0) AS paid FROM {refunds} "
                   "WHERE return_id = @return_id AND refund_status = 'COMPLETED'", {"return_id": return_id})
    facts["disbursed_completed_total"] = round(float(paid[0]["paid"]), 2)
    return facts


def get_return_case(return_id: str) -> dict:
    """Return one return request with its order item, order dates, customer tier, refund records and paid total.

    Args:
        return_id: Return ID such as RET-0104.
    """
    def run():
        rid, err = _id(return_id, RETURN_RE, "return_id")
        if err:
            return err
        f = _return_facts(rid)
        if not f:
            return {"error": f"Return {rid} was not found."}
        return {"return": f, "record_ids": [rid, f["order_id"]] + [r["refund_id"] for r in f["refunds"]]}
    return _timed("get_return_case", run)


def assess_return_review(return_id: str) -> dict:
    """Assess whether a return is eligible for staff review, using the policy version for its request date.

    This is an assessment only. It does not approve, reject or pay the refund. Staff make that decision.

    Args:
        return_id: Return ID such as RET-0104.
    """
    def run():
        rid, err = _id(return_id, RETURN_RE, "return_id")
        if err:
            return err
        f = _return_facts(rid)
        if not f:
            return {"error": f"Return {rid} was not found."}
        facts = {
            "request_date": f["request_date"], "order_date": f["order_date"], "delivered_at": f["delivered_at"],
            "loyalty_tier": f["loyalty_tier"], "final_sale": f["final_sale"], "reason_code": f["reason_code"],
            "evidence_attached": f["evidence_attached"], "same_item_return_count": f["same_item_return_count"],
            "refund_statuses": [r["refund_status"] for r in f["refunds"]],
        }
        result = assess_return(facts)
        result.update({
            "return_id": rid, "order_id": f["order_id"], "item_category": f["item_category"],
            "final_sale": f["final_sale"], "loyalty_tier": f["loyalty_tier"],
            "request_date": f["request_date"], "order_date": f["order_date"], "delivered_at": f["delivered_at"],
            "sop_references": [f"{DAMAGE_SOP['doc_name']} v{DAMAGE_SOP['version']}",
                               f"{REFUND_SOP['doc_name']} v{REFUND_SOP['version']}"],
            "record_ids": [rid, f["order_id"]] + [r["refund_id"] for r in f["refunds"]],
        })
        return result
    return _timed("assess_return_review", run)


def summarize_returns(month: str) -> dict:
    """Count return requests for a calendar month, with reason-code and status breakdowns.

    Args:
        month: Month in YYYY-MM format, for example 2026-10.
    """
    def run():
        if not isinstance(month, str) or not MONTH_RE.match(month):
            return {"error": "Invalid month. Use YYYY-MM."}
        s = store()
        total = s.query("SELECT COUNT(*) AS n FROM {returns} r WHERE {ym(r.request_date)} = @month", {"month": month})[0]["n"]
        reasons = s.query("SELECT r.reason_code, COUNT(*) AS n FROM {returns} r WHERE {ym(r.request_date)} = @month "
                          "GROUP BY r.reason_code ORDER BY n DESC, r.reason_code", {"month": month})
        statuses = s.query("SELECT r.status, COUNT(*) AS n FROM {returns} r WHERE {ym(r.request_date)} = @month "
                           "GROUP BY r.status ORDER BY n DESC, r.status", {"month": month})
        return {"month": month, "total_returns": total, "by_reason": reasons, "by_status": statuses,
                "note": "Counts come from SQL on the returns table. Totals are not estimated."}
    return _timed("summarize_returns", run)


def check_data_quality() -> dict:
    """Run data-quality checks: missing delivery dates, duplicate return requests, refund status conflicts,
    orphan records, and final-sale returns that are not damage claims."""
    def run():
        s = store()
        checks = {
            "orders_missing_delivery_date": s.query(
                "SELECT order_id FROM {orders} WHERE delivered_at IS NULL ORDER BY order_id LIMIT 50", {}),
            "duplicate_return_requests_per_item": s.query(
                "SELECT order_item_id, COUNT(*) AS n FROM {returns} GROUP BY order_item_id HAVING COUNT(*) > 1 "
                "ORDER BY order_item_id LIMIT 50", {}),
            "returns_with_completed_and_pending_refund": s.query(
                "SELECT return_id FROM {refunds} GROUP BY return_id "
                "HAVING SUM(CASE WHEN refund_status = 'COMPLETED' THEN 1 ELSE 0 END) > 0 "
                "AND SUM(CASE WHEN refund_status = 'PENDING' THEN 1 ELSE 0 END) > 0 ORDER BY return_id LIMIT 50", {}),
            "returns_with_orphan_order_item": s.query(
                "SELECT r.return_id FROM {returns} r LEFT JOIN {order_items} oi ON oi.order_item_id = r.order_item_id "
                "WHERE oi.order_item_id IS NULL LIMIT 50", {}),
            "refunds_with_orphan_return": s.query(
                "SELECT f.refund_id FROM {refunds} f LEFT JOIN {returns} r ON r.return_id = f.return_id "
                "WHERE r.return_id IS NULL LIMIT 50", {}),
            "final_sale_returns_not_damage": s.query(
                "SELECT r.return_id FROM {returns} r JOIN {order_items} oi ON oi.order_item_id = r.order_item_id "
                "WHERE oi.final_sale = 'Y' AND r.reason_code <> 'DAMAGED' ORDER BY r.return_id LIMIT 50", {}),
            "support_cases_with_orphan_return": s.query(
                "SELECT c.case_id FROM {support_cases} c LEFT JOIN {returns} r ON r.return_id = c.return_id "
                "WHERE r.return_id IS NULL LIMIT 50", {}),
        }
        summary = {k: len(v) for k, v in checks.items()}
        samples = {k: [list(r.values())[0] for r in v[:10]] for k, v in checks.items()}
        return {"summary_counts": summary, "sample_ids": samples,
                "note": "Counts are capped at 50 per check. Review the samples before acting."}
    return _timed("check_data_quality", run)


# ---------------------------------------------------------------- Policy

def get_policy_version(as_of_date: str) -> dict:
    """Return the returns-policy version that applies to a date, with its effective dates and source file.

    Args:
        as_of_date: Date in YYYY-MM-DD format. Use the return request date for return questions.
    """
    def run():
        d, err = _date(as_of_date)
        if err:
            return err
        pv = policy_for_date(d)
        if pv is None:
            return {"as_of_date": as_of_date, "policy_version": None,
                    "message": "No returns-policy version covers this date. The evidence is not sufficient. Do not invent a rule."}
        idx = policy_index()
        chunk_count = len(idx.chunks_for_doc(pv.doc_name))
        return {"as_of_date": as_of_date, "source_file": pv.doc_name, "policy_version": pv.version,
                "effective_from": pv.effective_from.isoformat(),
                "effective_to": pv.effective_to.isoformat() if pv.effective_to else None,
                "indexed_chunks": chunk_count,
                "supporting_sops": [f"{DAMAGE_SOP['doc_name']} v{DAMAGE_SOP['version']}",
                                    f"{REFUND_SOP['doc_name']} v{REFUND_SOP['version']}"]}
    return _timed("get_policy_version", run)


def retrieve_policy_chunks(query: str, top_k: int = 5, policy_doc: str = "") -> dict:
    """Search policy chunks with hybrid retrieval (keyword BM25 + vector, fused with RRF).

    Args:
        query: Search text, for example "damaged clothing final sale".
        top_k: Number of chunks to return (1 to 10).
        policy_doc: Optional file name filter, such as SC_Returns_Policy_v2.pdf. Leave empty for all documents.
    """
    def run():
        q, err = _query_text(query)
        if err:
            return err
        doc = policy_doc or None
        if doc and not DOC_RE.match(doc):
            return {"error": "Invalid policy_doc. Use a file name like SC_Returns_Policy_v2.pdf."}
        results = policy_index().retrieve(q, _top_k(top_k), policy_doc=doc)
        return {"query": q, "results": results,
                "record_ids": [r["chunk_id"] for r in results],
                "note": "Each result has source_file, policy_version, section and page for citation."}
    return _timed("retrieve_policy_chunks", run)


def explain_return_rule(topic: str, as_of_date: str) -> dict:
    """Find the policy text for a return rule, limited to the policy version that applies on a date.

    Args:
        topic: Short topic, for example "return period", "loyalty exception", "final sale".
        as_of_date: Date in YYYY-MM-DD format. Use the return request date.
    """
    def run():
        d, err = _date(as_of_date)
        if err:
            return err
        q, err = _query_text(topic)
        if err:
            return err
        pv = policy_for_date(d)
        if pv is None:
            return {"message": "No returns-policy version covers this date. The evidence is not sufficient. Do not invent a rule."}
        hits = policy_index().retrieve(q, 3, policy_doc=pv.doc_name)
        if not hits:
            return {"message": f"No passage about '{q}' was found in {pv.doc_name}. The evidence is not sufficient."}
        return {"source_file": pv.doc_name, "policy_version": pv.version, "results": hits,
                "record_ids": [h["chunk_id"] for h in hits]}
    return _timed("explain_return_rule", run)


# ---------------------------------------------------------------- Support-case search

def search_case_id(case_id: str) -> dict:
    """Find one support case by its exact case ID.

    Args:
        case_id: Case ID such as CASE-0001.
    """
    def run():
        cid, err = _id(case_id, CASE_RE, "case_id")
        if err:
            return err
        hit = case_index().by_id(cid)
        if hit is None:
            return {"error": f"Case {cid} was not found."}
        return {"results": [hit], "record_ids": [cid]}
    return _timed("search_case_id", run)


def lexical_search_cases(query: str, top_k: int = 5) -> dict:
    """Keyword search over support cases using BM25 ranking.

    Args:
        query: Words to search for, for example "zipper broke".
        top_k: Number of cases to return (1 to 10).
    """
    def run():
        q, err = _query_text(query)
        if err:
            return err
        results = case_index().lexical(q, _top_k(top_k))
        return {"method": "keyword_bm25", "query": q, "results": results,
                "record_ids": [r["case_id"] for r in results]}
    return _timed("lexical_search_cases", run)


def vector_search_cases(query: str, top_k: int = 5) -> dict:
    """Meaning-based search over support cases using embedding cosine similarity.

    Args:
        query: Description of the problem, for example "zipper stopped working after a few wears".
        top_k: Number of cases to return (1 to 10).
    """
    def run():
        q, err = _query_text(query)
        if err:
            return err
        results = case_index().vector(q, _top_k(top_k))
        return {"method": "vector_cosine", "query": q, "results": results,
                "record_ids": [r["case_id"] for r in results]}
    return _timed("vector_search_cases", run)


def hybrid_search_cases(query: str, top_k: int = 5) -> dict:
    """Combine keyword and vector rankings with Reciprocal Rank Fusion (RRF), with duplicates removed.

    Args:
        query: Search text describing the problem.
        top_k: Number of fused cases to return (1 to 10).
    """
    def run():
        q, err = _query_text(query)
        if err:
            return err
        out = case_index().hybrid(q, _top_k(top_k))
        results = out["fused"]
        return {"method": "hybrid_rrf_k60", "query": q, "results": results,
                "record_ids": [r["case_id"] for r in results], "dedup_note": out["dedup_note"]}
    return _timed("hybrid_search_cases", run)
