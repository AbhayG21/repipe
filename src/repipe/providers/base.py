"""The Provider interface every CI host implements."""

from ..model import Run, RunState, Step


class Provider:
    """Interface every CI host implements. Bitbucket ships in v1.

    Subclasses set NAME, HOSTS, TARGET_WORD and implement the methods below.
    Instances are constructed with (workspace, repo) from git detection.
    """
    NAME = "base"
    HOSTS = []
    TARGET_WORD = "pipeline"     # host-native vocabulary for prompts/output

    def __init__(self, workspace: str, repo: str):
        self.workspace = workspace
        self.repo = repo

    def parse_targets(self, worktree: str) -> list:
        """Discover runnable Targets from the repo working tree."""
        raise NotImplementedError

    def trigger_request(self, target, ref_name: str, variables: list):
        """Return (method, url, body) for a trigger — without sending it.

        `target` is a Target (providers read `target.name`/`target.key`).
        `variables` is a list of (key, value) tuples. Kept separate from
        trigger() so --dry-run can show the exact request per provider.
        """
        raise NotImplementedError

    def trigger(self, target, ref_name: str, variables: list, auth) -> Run:
        """Trigger a run and return the created Run."""
        raise NotImplementedError

    def get_run(self, run_id: str, auth) -> Run:
        """Fetch a Run by provider id or human build number."""
        raise NotImplementedError

    def get_steps(self, run: Run, auth) -> list:
        """List a run's Steps."""
        raise NotImplementedError

    def get_step_log(self, run: Run, step: Step, auth) -> str:
        """Fetch a step's raw log (may be empty)."""
        raise NotImplementedError

    # --- generic, provider-neutral (built on the methods above) ---

    def failed_step_logs(self, run: Run, auth):
        """Return (failed_steps, [(step_name, log_text), …]) for a failed run.

        Tolerates empty logs — the caller falls back to step names when a body
        is unavailable.
        """
        failed = [s for s in self.get_steps(run, auth) if s.state == RunState.FAILED]
        logs = [(s.name, self.get_step_log(run, s, auth)) for s in failed]
        return failed, logs
