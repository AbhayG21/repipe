"""Provider package: interface, registry, and the shipped adapters.

Importing this package registers every adapter (via each module's
@register_provider), so `choose_provider` sees them.
"""

from .base import Provider
from .registry import (
    register_provider,
    choose_provider,
    PROVIDERS_BY_HOST,
    PROVIDERS_BY_NAME,
)
from . import bitbucket  # noqa: F401  — import registers BitbucketProvider
from . import ghactions  # noqa: F401  — import registers GitHubActionsProvider

__all__ = [
    "Provider",
    "register_provider",
    "choose_provider",
    "PROVIDERS_BY_HOST",
    "PROVIDERS_BY_NAME",
]
