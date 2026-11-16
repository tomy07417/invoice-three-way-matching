"""Application command evidence is explicit and isolated from caller mutation."""
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from backend.tests.test_matching_rules import invoice
from app.application.dtos.invoice_evaluation import EvaluateInvoiceCommand
from app.domain.matching.types import LineAssociation


def test_command_snapshots_mapping_evidence(invoice):
    links = {0: LineAssociation("ORDER-Z", 7)}
    prices = {0: Decimal("100")}
    command = EvaluateInvoiceCommand(invoice=invoice, actor_id="system:test", supplier_id="SUP-Z",
                                    associations=links, net_unit_prices=prices)
    links[0] = None
    prices[0] = Decimal("999")
    assert command.associations[0] == LineAssociation("ORDER-Z", 7)
    assert command.net_unit_prices[0] == Decimal("100")
    with pytest.raises(TypeError):
        command.net_unit_prices[0] = Decimal("1")
    with pytest.raises(FrozenInstanceError):
        command.actor_id = "changed"


@pytest.mark.parametrize("actor,origin", [("", "structured-input"), ("   ", "structured-input"),
                                         ("system:test", "")])
def test_command_requires_audit_identity_and_origin(invoice, actor, origin):
    with pytest.raises(ValueError):
        EvaluateInvoiceCommand(invoice=invoice, actor_id=actor, origin=origin)


@pytest.mark.parametrize("identity", [0, -1, True])
def test_existing_invoice_identity_is_explicit_positive_integer(invoice, identity):
    with pytest.raises(ValueError):
        EvaluateInvoiceCommand(invoice=invoice, actor_id="system:test", invoice_id=identity)
