"""gdls package."""

from .cli import main
from .exceptions import (
	AuthenticationError,
	ConfigurationError,
	CredentialFileNotFoundError,
	GdlsError,
	GdlsFileNotFoundError,
	GdlsValueError,
)
from .gdls import gdls

__all__ = [
	"gdls",
	"main",
	"GdlsError",
	"GdlsValueError",
	"GdlsFileNotFoundError",
	"ConfigurationError",
	"AuthenticationError",
	"CredentialFileNotFoundError",
]
__version__ = "0.5.0"
