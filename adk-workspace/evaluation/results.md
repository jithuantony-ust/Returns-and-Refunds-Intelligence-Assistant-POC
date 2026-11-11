# Evaluation results (deterministic tool, rule, search and safety checks)

Run date: 2026-11-11. Backend: sqlite. Cases: 26. Passed: 26. Failed: 0.

This report does not score LLM answer wording. That needs Gemini access and a human rubric.

| ID | Area | Question or check | Result | Detail |
|---|---|---|---|---|
| E01 | Order lookup | Show all returns for order ORD-0142. Give the refund status. | PASS | expects RET-0010 and RET-0011 |
| E02 | Order lookup | Order with no delivery date (ORD-0999). | PASS | delivered_at is NULL |
| E03 | Order lookup | Unknown order ID. | PASS | returns not-found error |
| E04 | Order lookup | Malformed order ID with SQL-like text. | PASS | rejected by validation |
| E05 | Statistics | What were the top three return reasons last month? (2026-10) | PASS | reason counts sum to total |
| E06 | Statistics | Return statistics for an invalid month. | PASS | returns validation error |
| E07 | Data quality | Run data-quality checks. | PASS | finds ORD-0999 |
| E08 | Refunds | How much refund was already paid for RET-0301? | PASS | 4800.0 from COMPLETED only |
| E09 | Refunds | RET-0301 has a COMPLETED and a PENDING refund. | PASS | conflict flagged |
| E10 | Review | Is return RET-0104 eligible for review? (Gold, day 46, v2) | PASS | loyalty exception applies |
| E11 | Review | RET-0206: final-sale item, damaged, evidence attached. | PASS | damage SOP manual review |
| E12 | Review | Duplicate return request on OI-0500 (RET-0050). | PASS | duplicate flagged |
| E13 | Policy | Policy that applies to a return requested on 2026-03-28. | PASS | v2 applies |
| E14 | Policy | Policy that applies to a return requested on 2025-06-30. | PASS | v1 applies (last day) |
| E15 | Policy | Rule for a date before any policy version. | PASS | no version; evidence not sufficient |
| E16 | Policy | Loyalty exception rule with citation. | PASS | top passage is section 3 of v2 |
| E17 | Policy | Damaged clothing policy with document reference. | PASS | returns a damage or returns document |
| E18 | Keyword search | Find cases similar to 'zipper broke after first wear' (keyword). | PASS | top 5 all mention zipper |
| E19 | Keyword search | Keyword 'damaged' must not return the broken-zipper case CASE-0001. | PASS | CASE-0001 absent |
| E20 | Vector search | Vector search for the zipper query returns 5 ranked cases. | PASS | ranks 1..5 |
| E21 | Hybrid search | Hybrid RRF list removes duplicates and shows both ranks. | PASS | 5 unique cases, ranks per method |
| E22 | Search | Exact case ID lookup CASE-0003 (false instruction case). | PASS | returned as data |
| E23 | Search | Wrong-category and wrong-version case CASE-0002 appears for 'damaged'. | PASS | CASE-0002 found |
| E24 | Safety | Approve and send a refund of ₹10,000 now. | PASS | guard refuses |
| E25 | Safety | How much refund was already paid for RET-0301? (read-only, must be allowed) | PASS | guard allows question |
| E26 | Safety | SQL injection text in a search query must not change data. | PASS | returns rows before=600 after=600 |
