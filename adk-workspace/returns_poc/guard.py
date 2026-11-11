"""Deterministic guard for requests to approve, reject, send or pay a refund.

The model is instructed not to do this, but the guard does not rely on the model. Matching requests get a
fixed refusal before any LLM call. The guard is not a complete classifier. The agents also have no write tools.
"""
from __future__ import annotations

import re

_ACTION = r"(approve|approving|reject|rejecting|send|sending|pay|paying|issue|issuing|release|releasing|process|processing|disburse|disbursing|transfer|transferring)"
_UNSAFE_RE = re.compile(rf"\b{_ACTION}\b[^.?!]{{0,60}}\b(refund|refunds|payout|money back)\b", re.IGNORECASE)
_UNSAFE_RE_REVERSED = re.compile(rf"\b(refund|refunds|payout)\b[^.?!]{{0,40}}\b(now|immediately)\b[^.?!]{{0,20}}\b{_ACTION}\b", re.IGNORECASE)
_QUESTION_START = re.compile(r"^\s*(how|what|which|when|where|why|who|is|are|was|were|does|do|show|find|give|list|explain|tell)\b", re.IGNORECASE)

REFUSAL = (
    "I cannot approve, reject, send, pay or release a refund. Only authorised staff can make that decision in the "
    "payment system (SC_Refund_Processing_SOP.pdf section 5). I can summarise the refund facts, show the policy "
    "that applies, list similar cases, and flag the case for human review. Please tell me which return or order you "
    "want to review."
)


def is_unsafe_action(message: str) -> bool:
    if not message or not message.strip():
        return False
    if _QUESTION_START.match(message) and "?" in message:
        # A question about refunds ("How much refund was already paid?") is allowed.
        return False
    return bool(_UNSAFE_RE.search(message) or _UNSAFE_RE_REVERSED.search(message))
