"""The Provider interface every CI host implements."""

from ..model import Run, Step


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

    def get_run(self, run_id: str, auth) -> Run:
        """Fetch a Run by provider id or human build number."""
        raise NotImplementedError

    def get_steps(self, run: Run, auth) -> list:
        """List a run's Steps."""
        raise NotImplementedError

    def get_step_log(self, run: Run, step: Step, auth) -> str:
        """Fetch a step's raw log (may be empty)."""
        raise NotImplementedError
