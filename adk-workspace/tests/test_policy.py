"""Policy-version selection, rule engine, and consistency between the PDFs and the code."""
import datetime as dt

import pytest

from returns_poc.config import POLICY_DIR
from returns_poc.indexing import parse_policy_pdf
from returns_poc.rules import assess_return, policy_for_date


@pytest.mark.parametrize("day,expected", [
    ("2023-12-31", None),
    ("2024-01-01", "SC_Returns_Policy_v1.pdf"),
    ("2025-06-30", "SC_Returns_Policy_v1.pdf"),
    ("2025-07-01", "SC_Returns_Policy_v2.pdf"),
    ("2026-11-07", "SC_Returns_Policy_v2.pdf"),
])
def test_policy_version_by_request_date(day, expected):
    pv = policy_for_date(dt.date.fromisoformat(day))
    assert (pv.doc_name if pv else None) == expected


def _facts(**kw):
    base = dict(request_date="2026-03-28", order_date="2026-02-06", delivered_at="2026-02-10", loyalty_tier="Standard",
                final_sale="N", reason_code="SIZE_FIT", evidence_attached="N", same_item_return_count=1,
                refund_statuses=[])
    base.update(kw)
    return base


def test_loyalty_exception_makes_day_46_eligible_for_gold_only():
    assert assess_return(_facts(loyalty_tier="Gold"))["outcome"] == "ELIGIBLE_FOR_REVIEW"
    assert assess_return(_facts(loyalty_tier="Standard"))["outcome"] == "NOT_ELIGIBLE"


def test_one_day_outside_window_is_not_eligible_without_exception():
    # v2 window is 45 days from delivery. Day 46 is outside it for Standard tier.
    out = assess_return(_facts(request_date="2026-03-28", delivered_at="2026-02-10", loyalty_tier="Standard"))
    assert out["outcome"] == "NOT_ELIGIBLE"
    assert any("outside the return window" in r for r in out["reasons"])


def test_final_sale_without_damage_exception_is_not_eligible():
    out = assess_return(_facts(final_sale="Y", reason_code="SIZE_FIT"))
    assert out["outcome"] == "NOT_ELIGIBLE"


def test_final_sale_damage_with_evidence_needs_human_review():
    out = assess_return(_facts(final_sale="Y", reason_code="DAMAGED", evidence_attached="Y", request_date="2026-05-06",
                               delivered_at="2026-05-02", order_date="2026-04-28"))
    assert out["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "FINAL_SALE_DAMAGE_EXCEPTION" in out["flags"]
    assert out["needs_human_review"] is True


def test_missing_delivery_date_under_v2_needs_review_and_v1_uses_order_date():
    out = assess_return(_facts(delivered_at=None))
    assert out["outcome"] == "NEEDS_HUMAN_REVIEW" and "MISSING_DELIVERY_DATE" in out["flags"]
    # v1 counts from the order date, so a missing delivery date does not block it.
    v1 = assess_return(_facts(request_date="2025-03-01", order_date="2025-02-20", delivered_at=None))
    assert v1["policy_version"].startswith("SC_Returns_Policy_v1.pdf")
    assert v1["outcome"] == "ELIGIBLE_FOR_REVIEW"


def test_duplicate_and_refund_conflicts_are_flagged():
    dup = assess_return(_facts(same_item_return_count=2))
    assert "DUPLICATE_RETURN_REQUEST" in dup["flags"] and dup["needs_human_review"]
    conflict = assess_return(_facts(refund_statuses=["COMPLETED", "PENDING"]))
    assert "REFUND_STATUS_CONFLICT" in conflict["flags"]


def test_no_policy_version_means_insufficient_evidence():
    out = assess_return(_facts(request_date="2023-06-01"))
    assert out["outcome"] == "NEEDS_HUMAN_REVIEW"
    assert "NO_POLICY_VERSION_FOR_DATE" in out["flags"]
    assert any("not sufficient" in r for r in out["reasons"])


def test_policy_pdfs_contain_the_numbers_the_rules_use():
    text = {}
    for name in ["SC_Returns_Policy_v1.pdf", "SC_Returns_Policy_v2.pdf"]:
        text[name] = " ".join(c["content"] for c in parse_policy_pdf(POLICY_DIR / name))
    assert "30 calendar days counted from the order date" in text["SC_Returns_Policy_v1.pdf"]
    assert "45 calendar days counted from the delivery date" in text["SC_Returns_Policy_v2.pdf"]
    assert "Gold tier customers receive 7 additional days" in text["SC_Returns_Policy_v2.pdf"]
    assert "Platinum tier customers receive 14 additional days" in text["SC_Returns_Policy_v2.pdf"]
    assert "2025-07-01" in text["SC_Returns_Policy_v2.pdf"]


def test_every_chunk_has_source_version_section_page_and_dates():
    for pdf in POLICY_DIR.glob("*.pdf"):
        chunks = parse_policy_pdf(pdf)
        assert chunks, pdf.name
        for c in chunks:
            assert c["source_file"] == pdf.name
            assert c["policy_version"] and c["section"] and c["page"] >= 1
            assert c["effective_from"]
