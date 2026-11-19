from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from hashlib import sha256
import json
import pytest
from app.domain.matching.price_basis import AccountingEvidence, PriceLine, resolve_price_basis
from test_accounting_workflow import accounting_draft
from test_review_api import review, headers, decision
from test_invoice_list import login


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def test_spec_inv01_verbatim():
    lines = (PriceLine(D("40"), D("24.50"), D("980.00"), "unknown"),
             PriceLine(D("6"), D("89.00"), D("534.00"), "unknown"))
    before = repr(lines)
    result = resolve_price_basis(lines, D("1514.00"), D("317.94"), D("1831.94"), "USD", AccountingEvidence(True, ()))
    assert [(x.basis, x.source) for x in result.lines] == [("net", "arithmetic")] * 2
    assert str(result.expected_subtotal) == "1514.00"
    assert str(result.expected_total) == "1831.94"
    assert repr(lines) == before
    print("INV01: 1514.00 + 317.94 = 1831.94; net/arithmetic x2")


@pytest.mark.parametrize("variant,expected", [("cent", "Auto-Pay"), ("over_cent", None),
    ("gross", None), ("missing_adjustments", None), ("included_tax", None), ("ambiguous", None)])
def test_public_basis_and_rejected_correction_is_atomic(review, variant, expected):
    client, _, seed = review
    draft = accounting_draft(AccountingEvidence(True, ()))
    if variant == "cent": draft = replace(draft, total_amount=D("24.21"))
    if variant == "over_cent": draft = replace(draft, total_amount=D("24.2101"))
    if variant == "gross": draft = replace(draft, lines=(replace(draft.lines[0], unit_price_basis="gross"),))
    if variant == "missing_adjustments": draft = replace(draft, accounting_evidence=AccountingEvidence(True, None))
    if variant == "included_tax": draft = replace(draft, accounting_evidence=AccountingEvidence(False, ()))
    if variant == "ambiguous": draft = replace(draft, tax_amount=D("0.01"), total_amount=D("20.01"))
    case = seed(draft=draft)
    csrf = login(client, "analyst")
    detail = client.get(f"/review/{case}").json()
    assert detail["evaluation"]["result"]["final_status"] == expected
    row = detail["comparison_rows"][0]
    assert row["unit_price_basis_source"] == ("arithmetic" if expected else "declared" if variant == "gross" else "undetermined")
    assert detail["extraction"] == detail["original_extraction"]
    history = client.get(f"/review/{case}/history").json()
    before = digest([detail, history])
    invalid = deepcopy(detail["extraction"])
    invalid["accounting_evidence"] = {"taxes_separately_stated": True, "adjustments": [{"kind": "charge", "stage": "after_subtotal", "amount": 1.0, "line_index": None}]}
    response = client.patch(f"/review/{case}", headers=headers(csrf, 0), json={"invoice": invalid, "comment": "Reject inexact accounting amount"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_correction"
    assert response.json()["error"]["message"] == "Provide a valid complete extraction with exact decimal strings"
    assert digest([client.get(f"/review/{case}").json(), client.get(f"/review/{case}/history").json()]) == before
    if expected is None:
        denied = decision(client, csrf, case, 0)
        assert denied.status_code == 409
        assert denied.json()["error"]["code"] == "incomplete_evidence"
        assert denied.json()["error"]["message"] == "Complete, current evaluation evidence is required before approval"
        assert digest([client.get(f"/review/{case}").json(), client.get(f"/review/{case}/history").json()]) == before


def test_fresh_arithmetic_approval_cannot_reuse_receipts(review):
    client, _, seed = review
    draft = accounting_draft(AccountingEvidence(True, ()))
    draft = replace(draft, subtotal=D("30"), tax_amount=D("6.30"), total_amount=D("36.30"), lines=(replace(draft.lines[0], quantity=D("3"), line_total=D("30")),))
    first = seed("INDEPENDENT-1", draft=replace(draft, invoice_number="INDEPENDENT-1"))
    second = seed("INDEPENDENT-2", draft=replace(draft, invoice_number="INDEPENDENT-2"))
    csrf = login(client, "analyst")
    authorized = client.get(f"/review/{first}").json()
    assert authorized["operational_state"] == "APPROVED" and not authorized["payment_blocked"]
    assert authorized["current_decision"]["actor_id"] == "system:auto-pay"
    before = digest([client.get(f"/review/{second}").json(), client.get(f"/review/{second}/history").json()])
    denied = decision(client, csrf, second, 0)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "insufficient_available_quantity"
    assert denied.json()["error"]["message"] == "Received quantity is no longer available; refresh evidence before approving"
    assert digest([client.get(f"/review/{second}").json(), client.get(f"/review/{second}/history").json()]) == before
    refreshed = client.post(f"/review/{second}/reevaluations", headers=headers(csrf, 0), json={"comment": "Check remaining physical receipts"})
    assert refreshed.status_code == 200
    row = refreshed.json()["comparison_rows"][0]
    assert row["quantity_previously_approved"] == "3"
    assert row["quantity_available"] == "2"
    assert refreshed.json()["evaluation"]["result"]["final_status"] == "Held for Review"
