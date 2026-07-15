"""Error type and the exit-code contract, honored across all phases."""

# Exit codes.
EXIT_OK = 0                # success (incl. halted-at-gate)
EXIT_FAILED_NOMATCH = 1    # failed with a non-matching error — stopped
EXIT_RETRIES = 2           # retries exhausted
EXIT_CONFIG = 3            # config / auth / unknown-provider error
EXIT_TIMEOUT = 4           # timed out


class RepipeError(Exception):
    """A user-facing error that maps to an exit code."""

    def __init__(self, message: str, code: int = EXIT_CONFIG):
        super().__init__(message)
        self.code = code
