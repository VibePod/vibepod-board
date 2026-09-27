"""Domain errors. Each carries the message the TypeScript server used and maps to its status."""


class BoardError(Exception):
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class BadRequest(BoardError):
    status_code = 400


class Forbidden(BoardError):
    status_code = 403


class NotFound(BoardError):
    status_code = 404


class Conflict(BoardError):
    status_code = 409
