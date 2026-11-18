"""Manager-only parameter administration, also enforced outside HTTP."""
from app.application.dtos.auth import Role
from app.application.dtos.parameters import parse_parameters
from app.application.dtos.review import ReviewError
from app.application.ports.auth import AuthError
from app.application.ports.parameters import ParameterRepository
from app.application.use_cases.sessions import SessionService


class ParameterService:
    def __init__(self, sessions: SessionService, repository: ParameterRepository):
        self.sessions, self.repository = sessions, repository

    def authorize(self, token, csrf=None, *, write=False):
        if write:
            return self.sessions.require(token, csrf, roles=(Role.AP_MANAGER,))
        actor = self.sessions.resolve(token).actor
        if actor.role != Role.AP_MANAGER:
            raise AuthError('Forbidden', 403)
        return actor

    def current(self, token):
        self.authorize(token)
        return self.repository.current()

    def history(self, token, *, offset=0, limit=20):
        self.authorize(token)
        if type(offset) is not int or not 0 <= offset <= 2_147_483_647 or type(limit) is not int or not 1 <= limit <= 100:
            raise ReviewError('invalid_parameters', 'Invalid history pagination', 422)
        return self.repository.history(offset=offset, limit=limit)

    def change(self, token, csrf, expected_version, value):
        actor = self.authorize(token, csrf, write=True)
        if type(expected_version) is not int or not 1 <= expected_version <= 2_147_483_647:
            raise ReviewError('invalid_version', 'Provide a valid current configuration version', 422)
        return self.repository.change(expected_version, parse_parameters(value), actor)
