"""Exact partial parameter changes; no scheduling or client-supplied actor."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from app.application.dtos.review import ReviewError
from app.domain.matching.types import MatchingConfig


@dataclass(frozen=True)
class ParameterPatch:
    price_tolerance: Decimal | None = None
    analyst_approval_limit: Decimal | None = None
    comment: str | None = None

    def apply(self, current: MatchingConfig) -> MatchingConfig:
        return MatchingConfig(
            current.price_tolerance if self.price_tolerance is None else self.price_tolerance,
            current.analyst_approval_limit if self.analyst_approval_limit is None else self.analyst_approval_limit)


def parse_parameters(value) -> ParameterPatch:
    """Require ordinary decimal/scientific strings, finite and nonnegative.

    No business maximum, quantization, float conversion or fixed scale. The
    bounded HTTP body limits resource usage, not the permitted numeric range.
    """
    def invalid():
        raise ReviewError('invalid_parameters',
                          'Provide finite nonnegative exact decimal strings and an optional comment', 422)

    names = {'price_tolerance', 'analyst_approval_limit'}
    if not isinstance(value, dict) or set(value) - (names | {'comment'}) or not names.intersection(value):
        invalid()
    parsed = {}
    for name in names.intersection(value):
        raw = value[name]
        if not isinstance(raw, str) or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', raw):
            invalid()
        try:
            parsed[name] = Decimal(raw)
            # Keep the established domain validation as the single range policy.
            MatchingConfig(**{name: parsed[name]})
        except (InvalidOperation, ValueError):
            invalid()
    comment = value.get('comment')
    if comment is not None and (not isinstance(comment, str) or len(comment) > 4000):
        invalid()
    return ParameterPatch(**parsed, comment=comment)
