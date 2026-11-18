"""Atomic serialized config+audit writes; historical evaluations are never touched."""
from contextlib import contextmanager

from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.dtos.review import ReviewError, review_value
from app.domain.matching.types import MatchingConfig
from app.infrastructure.persistence.evaluation_records import RuleConfigurationVersion
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationTransaction
from app.infrastructure.persistence.parameter_models import ParameterChange


class SqlParameterRepository:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def _transaction(self, *, write=False):
        try:
            isolation = 'READ COMMITTED' if write else 'REPEATABLE READ'
            with self.engine.connect().execution_options(isolation_level=isolation) as connection:
                with Session(connection) as session, session.begin():
                    session.execute(text("SET LOCAL statement_timeout = '15s'"))
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    if write:
                        # Stable namespace shared by every parameter writer, including
                        # when there is no mutable singleton row to lock. The first
                        # configuration read and server clock happen AFTER waiting.
                        session.execute(text('SELECT pg_advisory_xact_lock(1346458177)'))
                    yield session
        except DataError:
            raise ReviewError('invalid_parameters', 'Parameter values cannot be represented exactly in PostgreSQL numeric storage', 422) from None
        except SQLAlchemyError:
            raise ReviewError('parameters_unavailable', 'Parameter service is temporarily unavailable', 503) from None

    def _current(self, session, at):
        # Reuse the exact effective-date ordering used by every evaluation.
        snapshot = SqlAlchemyInvoiceEvaluationTransaction(session).get_configuration(at)
        return session.get(RuleConfigurationVersion, snapshot.configuration_id)

    @staticmethod
    def _values(row):
        return dict(price_tolerance=str(row.price_tolerance),
                    analyst_approval_limit=str(row.analyst_approval_limit), currency='USD')

    def _version(self, session, row):
        event = session.get(ParameterChange, row.id)
        actor = (dict(actor_id=event.actor_id, username=event.actor_username, role=event.actor_role)
                 if event else None)
        return review_value(dict(version_id=row.id, **self._values(row),
                                 effective_from=row.effective_from, actor=actor,
                                 actor_reference=row.actor_id))

    def current(self):
        with self._transaction() as session:
            row = self._current(session, session.scalar(select(func.clock_timestamp())))
            result = self._version(session, row)
        return result

    def history(self, *, offset, limit):
        with self._transaction() as session:
            total = session.scalar(select(func.count()).select_from(RuleConfigurationVersion))
            rows = session.scalars(select(RuleConfigurationVersion).order_by(
                RuleConfigurationVersion.effective_from.desc(), RuleConfigurationVersion.id.desc()
            ).offset(offset).limit(limit)).all()
            items = []
            for row in rows:
                event = session.get(ParameterChange, row.id)
                previous = session.get(RuleConfigurationVersion, event.previous_version_id) if event else None
                items.append(dict(**self._version(session, row),
                    previous_version_id=event.previous_version_id if event else None,
                    before=self._values(previous) if previous else None, after=self._values(row),
                    comment=event.comment if event else None,
                    provenance='manager_change' if event else ('bootstrap' if row.actor_id == 'bootstrap' else 'historical_unknown')))
            result = dict(items=items, total=total, offset=offset, limit=limit)
        return result

    def change(self, expected_version, patch, actor):
        with self._transaction(write=True) as session:
            at = session.scalar(select(func.clock_timestamp()))
            old = self._current(session, at)
            if old.id != expected_version:
                raise ReviewError('version_conflict', 'Parameters changed; reload the current version', 409)
            previous = MatchingConfig(old.price_tolerance, old.analyst_approval_limit)
            config = patch.apply(previous)
            if config == previous:
                # A comment alone never manufactures a parameter change. The CAS
                # check above still applies to no-ops and stale replay requests.
                result = self._version(session, old)
            else:
                for value in (config.price_tolerance, config.analyst_approval_limit):
                    # PostgreSQL's documented physical numeric limits, not business
                    # caps. Reject rather than risk silent scale loss or a 503.
                    exponent = value.as_tuple().exponent
                    if value.adjusted() >= 131072 or exponent < -16383:
                        raise ReviewError('invalid_parameters', 'Parameter values exceed exact PostgreSQL numeric storage', 422)
                row = RuleConfigurationVersion(effective_from=at,
                    price_tolerance=config.price_tolerance,
                    analyst_approval_limit=config.analyst_approval_limit, actor_id=str(actor.actor_id))
                session.add(row)
                session.flush()
                session.add(ParameterChange(version_id=row.id, previous_version_id=old.id,
                    actor_id=actor.actor_id, actor_username=actor.username,
                    actor_role=actor.role.value, comment=patch.comment))
                session.flush()
                result = self._version(session, row)
        return result
