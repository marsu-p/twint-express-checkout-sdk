class TwintError(Exception):
    """Base class for all errors raised by this SDK."""


class ApiFailure(TwintError):
    """The TWINT API returned a fault or an unexpected response."""


class CancellationFailed(ApiFailure):
    """A check-in or order could not be cancelled (e.g. it is already finished)."""


class InvalidValue(TwintError, ValueError):
    """A value passed to or received from the API is malformed."""
