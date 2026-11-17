"""Configuration contracts and real PostgreSQL migration/constraint checks."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Date, Numeric, inspect, insert, select, text
from sqlalchemy.exc import IntegrityError

from app.infrastructure.persistence.connection import build_database_url, create_database_engine
from app.infrastructure.persistence import auth_models, processing_models  # Register new persistence metadata.
from app.infrastructure.persistence.models import (
    Base, GoodsReceivedNoteLine, Invoice, InvoiceLine, InvoicePOReference,
    PurchaseOrderLine, Supplier,
)


@pytest.fixture
def clean_environment(monkeypatch):
    for key in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_component_url_preserves_special_password_and_defaults_port(clean_environment):
    for key, value in {"HOST": "database", "DB": "matching", "USER": "app", "PASSWORD": "p@ss:%/word"}.items():
        clean_environment.setenv(f"POSTGRES_{key}", value)
    url = build_database_url()
    assert (url.drivername, url.host, url.port, url.database, url.username, url.password) == (
        "postgresql+psycopg", "database", 5432, "matching", "app", "p@ss:%/word",
    )
    clean_environment.setenv("POSTGRES_PORT", "5433")
    assert build_database_url().port == 5433


def test_database_url_takes_precedence_and_engine_is_lazy(clean_environment):
    clean_environment.setenv("DATABASE_URL", "postgresql://u:p%25%40@127.0.0.1:1/test_config")
    url = build_database_url()
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p%@"
    engine = create_database_engine()
    assert engine.url == url
    engine.dispose()  # No connection attempted to the deliberately unreachable port.


def test_configuration_failure_does_not_include_secrets(clean_environment):
    clean_environment.setenv("POSTGRES_PASSWORD", "do-not-display")
    with pytest.raises(ValueError, match="^Missing database configuration: POSTGRES_HOST, POSTGRES_DB, POSTGRES_USER$") as error:
        build_database_url()
    assert "do-not-display" not in str(error.value)
    clean_environment.setenv("DATABASE_URL", "mysql://u:do-not-display@localhost/db")
    with pytest.raises(ValueError, match="^DATABASE_URL must use PostgreSQL$"):
        build_database_url()


def test_model_metadata_preserves_domain_shapes():
    assert set(Base.metadata.tables) == {
        "suppliers", "purchase_orders", "purchase_order_lines", "goods_received_notes",
        "goods_received_note_lines", "invoices", "invoice_lines", "invoice_po_references",
        "rule_configuration_versions", "approved_payment_quantities", "invoice_evaluations",
        "evaluation_rule_findings", "users", "user_sessions", "upload_cases",
        "processing_control", "processing_attempts", "processing_events",
    }
    assert list(PurchaseOrderLine.__table__.primary_key.columns.keys()) == ["po_number", "line_number"]
    assert list(GoodsReceivedNoteLine.__table__.primary_key.columns.keys()) == ["grn_number", "po_line_number"]
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, Numeric):
                assert column.type.scale is None and column.type.precision is None
    assert isinstance(Invoice.__table__.c.invoice_date.type, Date)
    assert not Invoice.__table__.c.invoice_number.unique
    assert not PurchaseOrderLine.__table__.c.item_code.unique
    assert not InvoicePOReference.__table__.c.po_number.foreign_keys
    assert not InvoiceLine.__table__.c.po_line_reference.foreign_keys


def test_migration_roundtrip_and_repeat(database_url, alembic_cli):
    from sqlalchemy import create_engine

    alembic_cli("upgrade", "head")
    alembic_cli("upgrade", "head")
    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        for table in Base.metadata.tables.values():
            columns = {column["name"]: column for column in inspect(engine).get_columns(table.name)}
            assert set(columns) == set(table.columns.keys())
        alembic_cli("downgrade", "base")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        alembic_cli("upgrade", "head")
        assert set(inspect(engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_existing_invoice(database_url, alembic_cli):
    from sqlalchemy import create_engine
    from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository

    alembic_cli("upgrade", "0001")
    engine = create_engine(database_url)
    try:
        with engine.begin() as c:
            invoice_id = c.scalar(text("INSERT INTO invoices (supplier_name, supplier_tax_id, invoice_number, invoice_date, currency, subtotal, tax_amount, total_amount) VALUES ('old', 'raw', 'original', '2020-01-01', 'USD', 1, 0, 1) RETURNING id"))
            c.execute(text("INSERT INTO invoice_po_references VALUES (:id, 0, 'unknown')"), {"id": invoice_id})
            c.execute(text("INSERT INTO invoice_lines VALUES (:id, 7, 'legacy', 1, 1, 1, 'raw')"), {"id": invoice_id})
        alembic_cli("upgrade", "head")
        with SqlAlchemyInvoiceEvaluationRepository(engine).transaction() as tx:
            raw = tx.get_invoice(invoice_id)
            assert raw.invoice_number == "original"
            assert raw.po_references == ("unknown",)
            assert raw.lines[0].description == "legacy"
            assert tx.get_invoice_state(invoice_id) == "received"
            assert tx.get_approved_quantities(("unknown",)) == {}
            assert tx.list_evaluations(invoice_id) == ()
    finally:
        engine.dispose()


def test_evaluation_constraints(migrated_engine):
    for statement in (
        "INSERT INTO invoices (supplier_name, supplier_tax_id, invoice_number, invoice_date, currency, subtotal, tax_amount, total_amount, operational_state) VALUES ('a', 'b', 'c', '2020-01-01', 'USD', 1, 0, 1, 'paid')",
        "INSERT INTO rule_configuration_versions (effective_from, price_tolerance, analyst_approval_limit, actor_id) VALUES (now(), -1, 5000, 'actor')",
    ):
        with pytest.raises(IntegrityError), migrated_engine.begin() as c:
            c.execute(text(statement))
    with migrated_engine.begin() as c:
        invoice_id = c.scalar(text("INSERT INTO invoices (supplier_name, supplier_tax_id, invoice_number, invoice_date, currency, subtotal, tax_amount, total_amount) VALUES ('a', 'b', 'c', '2020-01-01', 'USD', 1, 0, 1) RETURNING id"))
    for complete, final, provisional in ((False, 'Auto-Pay', 'Auto-Pay'), (True, None, 'Auto-Pay'), (True, 'invalid', 'invalid')):
        with pytest.raises(IntegrityError), migrated_engine.begin() as c:
            c.execute(text("INSERT INTO invoice_evaluations (invoice_id, configuration_id, price_tolerance, analyst_approval_limit, evaluated_at, actor_id, origin, provisional_status, final_status, complete, input_snapshot, context_snapshot) VALUES (:id, 1, 0.02, 5000, now(), 'actor', 'test', :provisional, :final, :complete, '{}', '{}')"), {"id": invoice_id, "provisional": provisional, "final": final, "complete": complete})


def test_postgresql_constraints_and_exact_values(migrated_engine):
    engine = migrated_engine
    with engine.begin() as connection:
        connection.execute(insert(Supplier), dict(supplier_id="S", legal_name="Supplier", tax_id="tax", default_currency="USD", status="inactive", address="address"))
        for po in ("PO1", "PO2"):
            connection.execute(text("INSERT INTO purchase_orders VALUES (:po, 'S', '2026-01-01', 'USD', 'closed')"), {"po": po})
            connection.execute(insert(PurchaseOrderLine), dict(po_number=po, line_number=1, item_code="shared", description="item", quantity_ordered=Decimal("1.1234567890123456789"), unit_price=Decimal("2.1234567890123456789")))
        connection.execute(text("INSERT INTO goods_received_notes VALUES ('GRN1', 'PO1', '2026-01-02')"))
    # No receipt lines exist yet: rejection must come from the foreign keys,
    # not an accidental duplicate primary-key violation.
    for invalid in (
        dict(grn_number="GRN1", po_number="PO2", po_line_number=1, quantity_received=1),
        dict(grn_number="GRN1", po_number="PO1", po_line_number=99, quantity_received=1),
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert(GoodsReceivedNoteLine), invalid)
    with engine.begin() as connection:
        connection.execute(insert(GoodsReceivedNoteLine), dict(grn_number="GRN1", po_number="PO1", po_line_number=1, quantity_received=Decimal("0.1234567890123456789")))
        assert connection.scalar(select(PurchaseOrderLine.quantity_ordered).where(PurchaseOrderLine.po_number == "PO1")) == Decimal("1.1234567890123456789")
        assert connection.scalar(select(GoodsReceivedNoteLine.quantity_received)) == Decimal("0.1234567890123456789")
        assert connection.scalar(text("SELECT received_date FROM goods_received_notes")) == date(2026, 1, 2)
        assert connection.scalar(text("SELECT count(*) FROM goods_received_note_lines")) == 1
        for _ in range(2):
            invoice_id = connection.scalar(insert(Invoice).values(supplier_name="Unknown", supplier_tax_id="missing", invoice_number="duplicate", invoice_date=date(2026, 1, 1), currency="USD", subtotal=Decimal("1.00000000001"), tax_amount=0, total_amount=Decimal("1.00000000001")).returning(Invoice.id))
            connection.execute(insert(InvoicePOReference), dict(invoice_id=invoice_id, position=0, po_number="nonexistent"))
            connection.execute(insert(InvoiceLine), dict(invoice_id=invoice_id, line_number=1, description="raw", quantity=1, unit_price=1, line_total=1, po_line_reference="unknown:99"))
        assert connection.scalar(text("SELECT count(*) FROM invoices")) == 2
        assert connection.scalar(select(Invoice.subtotal).limit(1)) == Decimal("1.00000000001")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("DELETE FROM invoices"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM invoices")) == 2
        assert connection.scalar(text("SELECT count(*) FROM invoice_po_references")) == 2
        assert connection.scalar(text("SELECT count(*) FROM invoice_lines")) == 2
