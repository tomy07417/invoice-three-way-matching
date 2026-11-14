"""Composition root for the reference-data import command."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.application.dtos.reference_data import ReferenceDataError
from app.application.use_cases.import_reference_data import import_reference_data
from app.infrastructure.persistence.connection import create_database_engine
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository
from app.infrastructure.reference_data.json_reader import read_reference_data


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atomically import suppliers.json, purchase_orders.json and goods_received_notes.json.",
        epilog="Identical identities/content are unchanged (line order and decimal scale ignored); "
               "changed content conflicts. No updates/deletions. Success prints compact JSON header "
               "inserted/unchanged counts. Failures print 'Import failed: <safe detail>' to stderr "
               "and exit 1. Database configuration uses DATABASE_URL or POSTGRES_*; no .env loading.",
    )
    parser.add_argument("--directory", required=True, type=Path, help="directory containing the three JSON arrays")
    args = parser.parse_args()
    engine = None
    try:
        data = read_reference_data(args.directory)
        try:
            engine = create_database_engine()
        except (ValueError, TypeError, OverflowError, SQLAlchemyError):
            # Configuration library messages can contain credentials. Do not echo them.
            raise ReferenceDataError("invalid or missing database configuration") from None
        counts = import_reference_data(data, SqlReferenceDataRepository(engine))
    except ReferenceDataError as error:
        print(f"Import failed: {error}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps({name: asdict(count) for name, count in counts.items()}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
