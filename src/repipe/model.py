"""Normalized, provider-neutral domain model.

Every Provider maps its native concepts onto these types, so the CLI and (later)
the retry engine never depend on a specific CI host.
"""

from dataclasses import dataclass, field
from typing import Optional


class RunState:
    """Normalized run states. Providers map native statuses to these."""
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    HALTED = "HALTED"        # paused at a manual gate (terminal, non-failure)
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN = "UNKNOWN"


@dataclass
class Variable:
    """A pipeline input variable (a declared parameter)."""
    name: str
    default: Optional[str] = None
    allowed_values: list = field(default_factory=list)


@dataclass
class Target:
    """A runnable pipeline/workflow discovered from the repo."""
    name: str
    env: str                       # "qa" | "prod"
    variables: list = field(default_factory=list)


@dataclass
class Step:
    name: str
    state: str                     # normalized RunState (best-effort)
    native_result: Optional[str] = None
    uuid: Optional[str] = None


@dataclass
class Run:
    id: str                        # provider run id (Bitbucket pipeline uuid)
    number: Optional[int]          # human build number
    state: str                     # normalized RunState
    native_state: Optional[str] = None
    native_result: Optional[str] = None
    ref: Optional[str] = None
    pipeline: Optional[str] = None
    web_url: Optional[str] = None
