"""Retry-pattern matching.

Built-in patterns cover transient/infra failures that are safe to re-trigger.
Users append their own with --retry-on (substring by default, or regex). If
nothing matches a failed run's log, repipe does NOT retry — it surfaces the
(probably real) failure instead of looping.
"""

import re

# Substring patterns (matched case-insensitively) for transient failures.
DEFAULT_RETRY_PATTERNS = [
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


def build_patterns(retry_on, use_defaults=True):
    """Combine built-ins (unless opted out) with user --retry-on patterns."""
    patterns = list(DEFAULT_RETRY_PATTERNS) if use_defaults else []
    patterns += list(retry_on or [])
    return patterns


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
