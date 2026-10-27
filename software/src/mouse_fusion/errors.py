"""Stable errors shared by the SDK, service and J4 actor."""


class FusionError(RuntimeError):
    def __init__(self, code, message, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(message)

    def as_dict(self):
        return {'code': self.code, 'message': str(self), 'details': self.details}

