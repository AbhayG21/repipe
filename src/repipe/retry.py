"""Retry-pattern matching.

repipe applies NO retry patterns by default: it only re-triggers on patterns
the user configures (config `retry_on` and/or --retry-on). No configured
pattern matches a failed run's log ⇒ repipe does NOT retry — it surfaces the
failure instead of looping.

The list below is a *suggestion* set (common transient/infra errors) that we
surface via `repipe suggestions` for users to copy from. It is never applied
automatically — each org decides its own patterns.
"""

import re

# Suggested patterns only — surfaced to users, NOT applied unless configured.
SUGGESTED_RETRY_PATTERNS = [
    # DNS / network
    "could not resolve host",
    "temporary failure in name resolution",
    "connection timed out",
    "connection reset by peer",
    "network is unreachable",
    "no route to host",
    "tls handshake timeout",
    "i/o timeout",
    "unexpected eof",
    # memory
    "outofmemoryerror",
    "out of memory",
    "oomkilled",
    "cannot allocate memory",
    # docker registry / rate limits
    "toomanyrequests",
    "too many requests",
    "rate limit",
    "error pulling image",
    "failed to pull",
    "net/http: request canceled",
    # transient server responses
    "500 internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    # build tooling
    "gradle daemon",
    "daemon disappeared",
]


def build_patterns(retry_on):
    """Retry patterns come only from the user (config `retry_on` + --retry-on).
    There are no built-in defaults; no config ⇒ no retries."""
    return list(retry_on or [])


def first_match(log, patterns, mode="substring"):
    """Return the first pattern that matches the log, or None.

    substring: case-insensitive containment. regex: Python re.search
    (invalid patterns are skipped, not fatal).
    """
    if not log or not patterns:
        return None
    if mode == "regex":
        for p in patterns:
            try:
                if re.search(p, log):
                    return p
            except re.error:
                continue
        return None
    low = log.lower()
    for p in patterns:
        if p.lower() in low:
            return p
    return None
