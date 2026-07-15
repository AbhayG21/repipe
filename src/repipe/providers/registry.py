"""Host-keyed provider registry + resolution.

Adding a CI host is: implement Provider (base.py), then @register_provider it.
Nothing else in repipe needs to change.
"""

from typing import Optional

from ..errors import RepipeError, EXIT_CONFIG

PROVIDERS_BY_HOST = {}   # git host   -> Provider subclass
PROVIDERS_BY_NAME = {}   # short name -> Provider subclass


def register_provider(cls):
    for h in cls.HOSTS:
        PROVIDERS_BY_HOST[h] = cls
    PROVIDERS_BY_NAME[cls.NAME] = cls
    return cls


def choose_provider(host: str, override: Optional[str] = None):
    """Resolve the Provider class by --provider override or git host."""
    if override:
        cls = PROVIDERS_BY_NAME.get(override.lower())
        if not cls:
            supported = ", ".join(sorted(PROVIDERS_BY_NAME))
            raise RepipeError(
                f"unknown --provider '{override}' — supported: {supported}.",
                EXIT_CONFIG,
            )
        return cls
    cls = PROVIDERS_BY_HOST.get(host)
    if not cls:
        supported = ", ".join(sorted(PROVIDERS_BY_NAME))
        raise RepipeError(
            f"no provider for git host '{host}' — supported: {supported}. "
            "Use --provider to override.",
            EXIT_CONFIG,
        )
    return cls
