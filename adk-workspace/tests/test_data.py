"""Data-model and scenario tests for the synthetic StyleCart data (SQLite backend)."""
from datetime import date

import pytest

from returns_poc import tools
from returns_poc.store import Store


@pytest.fixture(scope="module")
def s():
    return Store("sqlite")


def scalar(s, sql, params=None):
    return s.query(sql, params)[0]["n"]


def test_row_counts_match_brief(s):
    assert scalar(s, "SELECT COUNT(*) n FROM {customers}") == 500
    assert scalar(s, "SELECT COUNT(*) n FROM {orders}") == 2000
    assert scalar(s, "SELECT COUNT(*) n FROM {order_items}") == 4000
    assert scalar(s, "SELECT COUNT(*) n FROM {returns}") == 600
    assert 350 <= scalar(s, "SELECT COUNT(*) n FROM {refunds}") <= 500
    assert scalar(s, "SELECT COUNT(*) n FROM {support_cases}") == 800


@pytest.mark.parametrize("name,sql", [
    ("order->customer", "SELECT COUNT(*) n FROM {orders} o LEFT JOIN {customers} c ON c.customer_id=o.customer_id WHERE c.customer_id IS NULL"),
    ("item->order", "SELECT COUNT(*) n FROM {order_items} i LEFT JOIN {orders} o ON o.order_id=i.order_id WHERE o.order_id IS NULL"),
    ("return->item", "SELECT COUNT(*) n FROM {returns} r LEFT JOIN {order_items} i ON i.order_item_id=r.order_item_id WHERE i.order_item_id IS NULL"),
    ("refund->return", "SELECT COUNT(*) n FROM {refunds} f LEFT JOIN {returns} r ON r.return_id=f.return_id WHERE r.return_id IS NULL"),
    ("case->return", "SELECT COUNT(*) n FROM {support_cases} c LEFT JOIN {returns} r ON r.return_id=c.return_id WHERE r.return_id IS NULL"),
])
def test_foreign_keys(s, name, sql):
    assert scalar(s, sql) == 0, name


def test_no_return_before_delivery_and_none_on_missing_delivery(s):
    assert scalar(s, "SELECT COUNT(*) n FROM {returns} r JOIN {order_items} i ON i.order_item_id=r.order_item_id "
                     "JOIN {orders} o ON o.order_id=i.order_id WHERE r.request_date < o.delivered_at") == 0
    assert scalar(s, "SELECT COUNT(*) n FROM {returns} r JOIN {order_items} i ON i.order_item_id=r.order_item_id "
                     "JOIN {orders} o ON o.order_id=i.order_id WHERE o.delivered_at IS NULL") == 0


def test_required_test_data_present(s):
    # Order with no delivery date.
    assert s.query("SELECT delivered_at FROM {orders} WHERE order_id='ORD-0999'")[0]["delivered_at"] is None
    # Two return requests for one order item.
    assert scalar(s, "SELECT COUNT(*) n FROM (SELECT order_item_id FROM {returns} GROUP BY order_item_id HAVING COUNT(*)>1)") == 1
    # Complete and pending refund on one return.
    assert tools.check_data_quality()["summary_counts"]["returns_with_completed_and_pending_refund"] == 1
    # Loyalty exception: Gold customer, request 46 days after delivery.
    r = tools.get_return_case("RET-0104")["return"]
    assert r["loyalty_tier"] == "Gold" and r["final_sale"] == "N"
    days = (date.fromisoformat(r["request_date"]) - date.fromisoformat(r["delivered_at"])).days
    assert days == 46
    # Final-sale item with a damage exception.
    r = tools.get_return_case("RET-0206")["return"]
    assert r["final_sale"] == "Y" and r["reason_code"] == "DAMAGED" and r["evidence_attached"] == "Y"
    # Broken-zipper complaint without the word "damaged".
    narrative = s.query("SELECT narrative FROM {support_cases} WHERE case_id='CASE-0001'")[0]["narrative"]
    assert "zipper" in narrative.lower() and "damaged" not in narrative.lower()
    # Case with a false instruction to ignore rules.
    narrative = s.query("SELECT narrative FROM {support_cases} WHERE case_id='CASE-0003'")[0]["narrative"]
    assert "ignore all policy rules" in narrative
    # Similar case with the wrong category and an old policy version.
    narrative = s.query("SELECT narrative FROM {support_cases} WHERE case_id='CASE-0002'")[0]["narrative"]
    assert "sneakers" in narrative and "policy v1" in narrative


def test_order_in_scope_for_demo(s):
    # ORD-0142 has two return requests (demo question 1).
    detail = tools.get_order_details("ORD-0142")
    assert len(detail["returns"]) == 2
    assert all(r["return_id"] in {"RET-0010", "RET-0011"} for r in detail["returns"])
