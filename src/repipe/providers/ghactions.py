"""GitHub Actions provider adapter.

Triggers workflows via the `workflow_dispatch` API. The dispatch call returns
204 with no run id, so `trigger` snapshots the workflow's run ids, dispatches,
then polls until a new run id appears and returns that run.
"""

import os
import time
import urllib.parse

from ..errors import RepipeError, EXIT_CONFIG
from ..ghyml import parse_workflows
from ..http import api_get_json, api_get_text, api_post_json, probe
from ..model import Run, RunState, Step
from .base import Provider
from .registry import register_provider

GITHUB_API = "https://api.github.com"
GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@register_provider
class GitHubActionsProvider(Provider):
    NAME = "github"
    HOSTS = ["github.com"]
    TARGET_WORD = "workflow"

    def _base(self) -> str:
        return f"{GITHUB_API}/repos/{self.workspace}/{self.repo}"

    def parse_targets(self, worktree: str) -> list:
        wf_dir = os.path.join(worktree, ".github", "workflows")
        if not os.path.isdir(wf_dir):
            raise RepipeError(
                f"no .github/workflows directory in {os.path.abspath(worktree)}.",
                EXIT_CONFIG,
            )
        return parse_workflows(wf_dir)

    def trigger_request(self, target, ref_name: str, variables: list):
        wf = urllib.parse.quote(target.key or target.name)
        url = f"{self._base()}/actions/workflows/{wf}/dispatches"
        body = {"ref": ref_name, "inputs": {k: str(v) for k, v in variables}}
        return "POST", url, body

    def _list_runs(self, target, ref_name: str, auth) -> list:
        wf = urllib.parse.quote(target.key or target.name)
        url = (
            f"{self._base()}/actions/workflows/{wf}/runs"
            f"?branch={urllib.parse.quote(ref_name)}"
            f"&event=workflow_dispatch&per_page=15"
        )
        data = api_get_json(url, auth, headers=GH_HEADERS)
        return data.get("workflow_runs") or []  # newest first

    def trigger(self, target, ref_name: str, variables: list, auth) -> Run:
        before = {r.get("id") for r in self._list_runs(target, ref_name, auth)}
        _, url, body = self.trigger_request(target, ref_name, variables)
        api_post_json(url, body, auth, headers=GH_HEADERS)  # 204, empty body
        # Dispatch doesn't echo a run id — poll until a new run shows up.
        for _ in range(15):
            time.sleep(2)
            new = [r for r in self._list_runs(target, ref_name, auth)
                   if r.get("id") not in before]
            if new:
                return self._run_from_json(new[0])  # newest new run
        raise RepipeError(
            "dispatched the workflow but couldn't locate the created run "
            "(GitHub can be slow to register it, or the workflow filtered the "
            "branch out) — check the Actions tab.",
            EXIT_CONFIG,
        )

    def _map_state(self, rj: dict):
        status = rj.get("status")
        concl = rj.get("conclusion")
        native = concl or status
        if status == "completed":
            if concl == "success":
                return RunState.SUCCESS, native, concl
            if concl == "action_required":
                return RunState.HALTED, native, concl
            if concl in ("failure", "timed_out", "startup_failure",
                         "cancelled", "stale", "neutral"):
                return RunState.FAILED, native, concl
            return RunState.UNKNOWN, native, concl
        if status == "waiting":            # awaiting a deployment approval gate
            return RunState.HALTED, native, concl
        if status in ("queued", "in_progress", "requested", "pending"):
            return RunState.RUNNING, native, concl
        return RunState.UNKNOWN, native, concl

    def _run_from_json(self, rj: dict) -> Run:
        state, native_state, native_result = self._map_state(rj)
        return Run(
            id=str(rj.get("id")),
            number=rj.get("run_number"),
            state=state,
            native_state=native_state,
            native_result=native_result,
            ref=rj.get("head_branch"),
            pipeline=rj.get("name"),
            web_url=rj.get("html_url"),
        )

    def get_run(self, run_id: str, auth) -> Run:
        rj = api_get_json(
            f"{self._base()}/actions/runs/{run_id.strip()}", auth, headers=GH_HEADERS
        )
        return self._run_from_json(rj)

    def get_steps(self, run: Run, auth) -> list:
        data = api_get_json(
            f"{self._base()}/actions/runs/{run.id}/jobs", auth, headers=GH_HEADERS
        )
        steps = []
        for jv in data.get("jobs") or []:
            concl = jv.get("conclusion")
            norm = RunState.FAILED if concl in (
                "failure", "timed_out", "startup_failure", "cancelled"
            ) else (RunState.SUCCESS if concl == "success" else RunState.UNKNOWN)
            steps.append(
                Step(
                    name=jv.get("name") or "(unnamed job)",
                    state=norm,
                    native_result=concl,
                    uuid=str(jv.get("id")),
                )
            )
        return steps

    def get_step_log(self, run: Run, step: Step, auth) -> str:
        return api_get_text(
            f"{self._base()}/actions/jobs/{step.uuid}/logs", auth, headers=GH_HEADERS
        )

    def verify_auth(self, auth):
        return probe(self._base(), auth, headers=GH_HEADERS)
