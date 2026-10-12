class DomainError(Exception):
    status_code = 400

    def __init__(self, message: str, **details) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409


class Invalid(DomainError):
    status_code = 422


class Unauthorized(DomainError):
    status_code = 401


class Unavailable(DomainError):
    status_code = 503
