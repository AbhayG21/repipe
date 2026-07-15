"""Small formatting helpers for terminal output."""

from .model import RunState, Variable


def fmt_var(v: Variable) -> str:
    """e.g. MULTI[=true]{true|false}"""
    s = v.name
    if v.default is not None:
        s += f"[={v.default}]"
    if v.allowed_values:
        s += "{" + "|".join(v.allowed_values) + "}"
    return s


def state_symbol(state: str) -> str:
    return {
        RunState.SUCCESS: "✓",
        RunState.FAILED: "✗",
        RunState.RUNNING: "…",
        RunState.HALTED: "‖",
        RunState.TIMED_OUT: "⌛",
    }.get(state, "?")
