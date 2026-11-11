"""Generate the fictional StyleCart policy PDFs used for RAG.

Run from adk-workspace/:  python -m returns_poc.policy.build_pdfs
All policy text is fictional. Numbers must match returns_poc/rules.py (checked by tests).
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from returns_poc.config import POLICY_DIR

DOCS = [
    {
        "file": "SC_Returns_Policy_v1.pdf",
        "title": "StyleCart Returns Policy",
        "version": "1.0",
        "effective_from": "2024-01-01",
        "effective_to": "2025-06-30",
        "status": "Superseded by SC_Returns_Policy_v2.pdf on 2025-07-01",
        "sections": [
            ("1. Purpose", ["This policy describes how StyleCart customers may return clothing, shoes and accessories."]),
            ("2. Return period", [
                "Customers may return an eligible item within 30 calendar days counted from the order date.",
                "The return period is the same for all loyalty tiers in this version.",
            ]),
            ("3. Allowed items", [
                "Clothing, shoes and accessories that are unworn, unwashed and carry original tags are allowed.",
            ]),
            ("4. Exclusions", [
                "Items marked final sale are not returnable, except as stated in SC_Damaged_Item_SOP.pdf.",
                "Personal-care items and innerwear are not returnable.",
            ]),
            ("5. Start date", [
                "This version takes effect on 2024-01-01 and is superseded on 2025-07-01.",
                "For returns requested on or before 2025-06-30, this version applies.",
            ]),
        ],
    },
    {
        "file": "SC_Returns_Policy_v2.pdf",
        "title": "StyleCart Returns Policy",
        "version": "2.0",
        "effective_from": "2025-07-01",
        "effective_to": None,
        "status": "Current",
        "sections": [
            ("1. Purpose", ["This policy replaces version 1.0 for all returns requested on or after 2025-07-01."]),
            ("2. Return period", [
                "Customers may return an eligible item within 45 calendar days counted from the delivery date.",
                "The delivery date is taken from the delivered_at field of the order.",
                "If the delivery date is missing, the return cannot be assessed automatically and needs human review.",
            ]),
            ("3. Loyalty exceptions", [
                "Gold tier customers receive 7 additional days, so the total return period is 52 calendar days.",
                "Platinum tier customers receive 14 additional days, so the total return period is 59 calendar days.",
                "Standard and Silver tier customers receive no additional days.",
            ]),
            ("4. Final-sale items", [
                "Items marked final sale are not returnable.",
                "The only exception is a damage claim that meets SC_Damaged_Item_SOP.pdf. Such claims are reviewed manually.",
            ]),
            ("5. Start date", [
                "This version takes effect on 2025-07-01.",
                "It applies to returns requested on or after 2025-07-01.",
            ]),
        ],
    },
    {
        "file": "SC_Damaged_Item_SOP.pdf",
        "title": "Damaged Item Standard Operating Procedure",
        "version": "1.2",
        "effective_from": "2024-01-01",
        "effective_to": None,
        "status": "Current",
        "sections": [
            ("1. Scope", ["This SOP applies to customer claims that an item arrived damaged or became damaged in normal use."]),
            ("2. Damage evidence", [
                "The customer must upload at least one photo that shows the damage, within 48 hours of delivery.",
                "The photo must show the damaged area and the order label.",
                "A claim without evidence is recorded as 'no evidence attached'.",
            ]),
            ("3. Checks", [
                "Check that the item SKU matches the order item.",
                "Check that the claim is not a duplicate of an open return for the same order item.",
                "Check whether the item is final sale.",
            ]),
            ("4. Conditions for manual review", [
                "Every final-sale item with a damage claim must be reviewed manually, even when evidence is attached.",
                "Any claim with a duplicate return request must be reviewed manually.",
                "The assistant may summarise the evidence. Staff make the decision.",
            ]),
        ],
    },
    {
        "file": "SC_Refund_Processing_SOP.pdf",
        "title": "Refund Processing Standard Operating Procedure",
        "version": "1.0",
        "effective_from": "2024-01-01",
        "effective_to": None,
        "status": "Current",
        "sections": [
            ("1. Scope", ["This SOP describes limits, payment status checks and approval controls for refunds."]),
            ("2. Refund limits", [
                "The approved amount must not exceed the item price multiplied by the quantity returned.",
                "Shipping charges are refunded only when the return reason is DEFECTIVE or WRONG_ITEM.",
            ]),
            ("3. Payment status", [
                "A refund can be disbursed only when the order payment_status is PAID or PARTIALLY_REFUNDED.",
                "An order with payment_status REFUNDED needs human review before any further refund.",
            ]),
            ("4. Duplicate checks", [
                "A return must not have more than one COMPLETED refund record.",
                "If a COMPLETED refund and a PENDING refund both exist for the same return, hold both and escalate for review.",
            ]),
            ("5. Approval controls", [
                "Only authorised staff approve, reject or release refunds in the payment system.",
                "Automated assistants may summarise refund facts and flag issues. They must not approve, reject or pay refunds.",
            ]),
        ],
    },
]


def _footer(doc_meta):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(40, 25, f"StyleCart (fictional) | {doc_meta['file']} | Version {doc_meta['version']} | Effective {doc_meta['effective_from']}")
        canvas.drawRightString(A4[0] - 40, 25, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def build(out_dir: Path = POLICY_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    paths = []
    for meta in DOCS:
        path = out_dir / meta["file"]
        story = [
            Paragraph(meta["title"], styles["Title"]),
            Paragraph(f"Document: {meta['file']}", styles["Normal"]),
            Paragraph(f"Version: {meta['version']}", styles["Normal"]),
            Paragraph(f"Effective from: {meta['effective_from']}", styles["Normal"]),
            Paragraph(f"Effective to: {meta['effective_to'] or 'No end date'}", styles["Normal"]),
            Paragraph(f"Status: {meta['status']}", styles["Normal"]),
            Spacer(1, 12),
        ]
        for i, (heading, paras) in enumerate(meta["sections"]):
            if i == 3:
                story.append(PageBreak())  # so the document spans more than one page
            story.append(Paragraph(heading, styles["Heading2"]))
            for p in paras:
                story.append(Paragraph(p, styles["Normal"]))
                story.append(Spacer(1, 6))
        doc = SimpleDocTemplate(str(path), pagesize=A4, title=meta["title"] + f" v{meta['version']}",
                                author="StyleCart (fictional)")
        footer = _footer(meta)
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for p in build():
        print(p)
