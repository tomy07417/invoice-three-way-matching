from datetime import datetime, timedelta, timezone

import pytest

from app.application.use_cases.retry_policy import RetryPolicy


def test_increasing_windows_floor_jitter_and_exhaustion():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    policy = RetryPolicy(base_seconds=2, cap_seconds=8, jitter_ratio=.5, max_attempts=4)
    assert [policy.delay(n, random_value=0) for n in (1, 2, 3, 4, 1000)] == [2, 4, 8, 8, 8]
    assert [policy.delay(n, random_value=1) for n in (1, 2, 3)] == [3, 6, 12]
    assert policy.next_at(now, 1, random_value=1, floor=now + timedelta(seconds=90)) == now + timedelta(seconds=91)
    assert policy.next_at(now, 4, random_value=0) is None
    assert policy.next_at(now, 1, retryable=False, random_value=0) is None


@pytest.mark.parametrize('kwargs', [dict(base_seconds=0), dict(cap_seconds=1, base_seconds=2), dict(jitter_ratio=-1), dict(max_attempts=0), dict(base_seconds=float('nan')), dict(jitter_ratio=float('inf'))])
def test_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_invalid_random_and_attempt():
    policy = RetryPolicy()
    for value in (-.1, 1.1, float('nan')):
        with pytest.raises(ValueError):
            policy.delay(1, random_value=value)
    with pytest.raises(ValueError):
        policy.delay(0, random_value=0)
