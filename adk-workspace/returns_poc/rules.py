"""Deterministic return-rule engine for StyleCart.

The numbers here must match the fictional policy PDFs in policy_docs/
(checked by tests/test_policy.py). The engine only *assesses* a return. It never
approves, rejects or pays a refund. Staff make that decision.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PolicyVersion:
    doc_name: str          # file name, e.g. SC_Returns_Policy_v1.pdf
    version: str
    effective_from: dt.date
    effective_to: dt.date | None  # None = still in force
    window_days: int
    anchor: str            # "order_date" or "delivered_at"
    loyalty_bonus_days: dict[str, int] = field(default_factory=dict)


POLICY_VERSIONS = [
    PolicyVersion("SC_Returns_Policy_v1.pdf", "1.0", dt.date(2024, 1, 1), dt.date(2025, 6, 30),
                  window_days=30, anchor="order_date"),
    PolicyVersion("SC_Returns_Policy_v2.pdf", "2.0", dt.date(2025, 7, 1), None,
                  window_days=45, anchor="delivered_at",
                  loyalty_bonus_days={"Gold": 7, "Platinum": 14}),
]

# Refund and damage SOPs are version-stable in this POC.
DAMAGE_SOP = {"doc_name": "SC_Damaged_Item_SOP.pdf", "version": "1.2", "effective_from": dt.date(2024, 1, 1)}
REFUND_SOP = {"doc_name": "SC_Refund_Processing_SOP.pdf", "version": "1.0", "effective_from": dt.date(2024, 1, 1)}


def policy_for_date(as_of: dt.date) -> PolicyVersion | None:
    """Return the returns-policy version in force on a date, or None if no version covers it."""
    for pv in POLICY_VERSIONS:
        if pv.effective_from <= as_of and (pv.effective_to is None or as_of <= pv.effective_to):
            return pv
    return None


def assess_return(facts: dict) -> dict:
    """Assess one return from facts returned by the data layer.

    `facts` keys: request_date, order_date, delivered_at, loyalty_tier, final_sale,
    reason_code, evidence_attached, same_item_return_count, refund_statuses (list of str).
    Dates are ISO strings or None.
    """
    flags: list[str] = []
    reasons: list[str] = []
    citations: list[str] = []

    request_date = dt.date.fromisoformat(facts["request_date"])
    pv = policy_for_date(request_date)
    if pv is None:
        return _result("NEEDS_HUMAN_REVIEW", None, flags + ["NO_POLICY_VERSION_FOR_DATE"],
                       ["No returns-policy version covers the request date. Evidence is not sufficient."], [])

    citations.append(f"{pv.doc_name} v{pv.version}")
    tier = facts.get("loyalty_tier") or "Standard"
    final_sale = facts.get("final_sale") == "Y"
    damage_claim = facts.get("reason_code") == "DAMAGED" and facts.get("evidence_attached") == "Y"

    if facts.get("same_item_return_count", 1) > 1:
        flags.append("DUPLICATE_RETURN_REQUEST")
        reasons.append("More than one return request exists for this order item.")

    refund_statuses = facts.get("refund_statuses", [])
    if "COMPLETED" in refund_statuses and "PENDING" in refund_statuses:
        flags.append("REFUND_STATUS_CONFLICT")
        reasons.append("One refund record is COMPLETED and another is PENDING for this return.")
    elif refund_statuses.count("COMPLETED") > 1:
        flags.append("MULTIPLE_COMPLETED_REFUNDS")
        reasons.append("More than one COMPLETED refund exists for this return.")

    if final_sale:
        citations.append(f"{pv.doc_name} v{pv.version} section 4 (final-sale exclusion)")
        if damage_claim:
            citations.append(f"{DAMAGE_SOP['doc_name']} v{DAMAGE_SOP['version']} (manual review)")
            flags.append("FINAL_SALE_DAMAGE_EXCEPTION")
            reasons.append("Final-sale item with a damage claim and evidence. The damage SOP requires manual review.")
        else:
            reasons.append("Item is marked final sale and there is no damage exception.")
            return _result("NOT_ELIGIBLE", pv, flags, reasons, citations)

    anchor_raw = facts.get("order_date") if pv.anchor == "order_date" else facts.get("delivered_at")
    if anchor_raw is None:
        flags.append("MISSING_DELIVERY_DATE" if pv.anchor == "delivered_at" else "MISSING_ORDER_DATE")
        reasons.append(f"The return window in {pv.doc_name} is counted from the {pv.anchor.replace('_', ' ')}, "
                       "and that date is missing.")
        return _result("NEEDS_HUMAN_REVIEW", pv, flags, reasons, citations)

    anchor = dt.date.fromisoformat(anchor_raw)
    days = (request_date - anchor).days
    bonus = pv.loyalty_bonus_days.get(tier, 0)
    limit = pv.window_days + bonus
    citations.append(f"{pv.doc_name} v{pv.version} section 2 (return period)")
    anchor_label = {"order_date": "order date", "delivered_at": "delivery date"}[pv.anchor]
    reasons.append(f"Request is {days} days after the {anchor_label}. "
                   f"Window is {pv.window_days} days" + (f" plus {bonus} loyalty days ({tier})." if bonus else "."))

    if days < 0:
        reasons.append("Request date is before the anchor date. The data needs checking.")
        return _result("NEEDS_HUMAN_REVIEW", pv, flags + ["DATE_ORDER_INVALID"], reasons, citations)
    if days > limit:
        reasons.append("Request is outside the return window.")
        return _result("NOT_ELIGIBLE", pv, flags, reasons, citations)
    if bonus and days > pv.window_days:
        citations.append(f"{pv.doc_name} v{pv.version} section 3 (loyalty exception)")
        reasons.append(f"Request is outside the standard window but inside the {tier} loyalty exception.")

    if flags:
        return _result("NEEDS_HUMAN_REVIEW", pv, flags, reasons, citations)
    return _result("ELIGIBLE_FOR_REVIEW", pv, flags, reasons, citations)


def _result(outcome, pv, flags, reasons, citations) -> dict:
    return {
        "outcome": outcome,
        "policy_version": (f"{pv.doc_name} v{pv.version}" if pv else None),
        "flags": flags,
        "reasons": reasons,
        "citations": citations,
        "needs_human_review": outcome == "NEEDS_HUMAN_REVIEW" or bool(flags),
        "decision_note": "Assessment only. Staff must make the approval decision. The assistant does not approve, reject or pay refunds.",
    }
