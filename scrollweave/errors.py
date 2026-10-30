"""Public exception hierarchy for ScrollWeave.

Exceptions deliberately inherit from the closest built-in category so existing
``except ValueError`` / ``except OSError`` / ``except RuntimeError`` handlers keep
working, while callers can opt into the more specific classes.
"""

from __future__ import annotations


class ScrollWeaveError(Exception):
    """Base class for all ScrollWeave-specific failures."""


class InvalidArgumentError(ScrollWeaveError, ValueError):
    """A public API argument is invalid or inconsistent."""


class LimitError(ScrollWeaveError, ValueError):
    """A documented size, count or allocation limit was exceeded."""


class UnsupportedError(ScrollWeaveError, ValueError):
    """The requested operation or format is not supported."""


class UnsupportedFormatError(UnsupportedError):
    """An image codec or output format is not supported."""


class ImageError(ScrollWeaveError):
    """Base class for image read/write failures."""


class ImageIOError(ImageError, OSError):
    """An image could not be opened, created or written."""


class ImageFormatError(ImageError, ValueError):
    """Image bytes are corrupt or do not match the claimed format."""


class NativeError(ScrollWeaveError, RuntimeError):
    """The native Rust engine reported a non-input failure."""


class NativeUnavailable(NativeError):
    """The native Rust shared library is missing or incompatible."""


class InternalError(NativeError):
    """An unexpected native-engine invariant or panic occurred."""
