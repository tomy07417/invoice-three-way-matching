"""Reserved workflow identity, never an authenticated user or login role."""
from dataclasses import dataclass
from enum import Enum

AUTO_APPROVER_ID = 'system:auto-pay'


class _AutomationRole(str, Enum):
    SYSTEM = 'system'


@dataclass(frozen=True)
class AutomaticApprover:
    actor_id: str = AUTO_APPROVER_ID
    username: str = 'Automatic matching'
    role: _AutomationRole = _AutomationRole.SYSTEM


AUTOMATIC_APPROVER = AutomaticApprover()
