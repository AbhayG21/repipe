"""Bitbucket Cloud provider adapter."""

import os
import urllib.parse

from ..errors import RepipeError, EXIT_CONFIG
from ..http import api_get_json, api_get_text, api_post_json
from ..model import Run, RunState, Step
from ..ymlparse import parse_pipelines_yml
from .base import Provider
from .registry import register_provider

BITBUCKET_API = "https://api.bitbucket.org/2.0"


@register_provider
class BitbucketProvider(Provider):
    NAME = "bitbucket"
    HOSTS = ["bitbucket.org"]
    TARGET_WORD = "pipeline"

    def _base(self) -> str:
        return f"{BITBUCKET_API}/repositories/{self.workspace}/{self.repo}"

    def web_url(self, build_number) -> str:
        return (
            f"https://bitbucket.org/{self.workspace}/{self.repo}"
            f"/pipelines/results/{build_number}"
        )

    def parse_targets(self, worktree: str) -> list:
        path = os.path.join(worktree, "bitbucket-pipelines.yml")
        if not os.path.isfile(path):
            raise RepipeError(
                f"no bitbucket-pipelines.yml found in {os.path.abspath(worktree)}.",
                EXIT_CONFIG,
            )
        with open(path, "r", encoding="utf-8") as f:
            return parse_pipelines_yml(f.read())

    def trigger_request(self, target_name: str, ref_name: str, variables: list):
        body = {
            "target": {
                "type": "pipeline_ref_target",
                "ref_type": "branch",
                "ref_name": ref_name,
                "selector": {"type": "custom", "pattern": target_name},
            },
            "variables": [
                {"key": k, "value": v, "secured": False} for k, v in variables
            ],
        }
        return "POST", f"{self._base()}/pipelines/", body

    def trigger(self, target_name: str, ref_name: str, variables: list, auth) -> Run:
        _, url, body = self.trigger_request(target_name, ref_name, variables)
        pj = api_post_json(url, body, auth)
        uuid = pj.get("uuid") or ""
        return self._run_from_json(uuid, pj)

    def _map_state(self, pipeline_json: dict):
        st = pipeline_json.get("state", {}) or {}
        name = st.get("name")
        result = (st.get("result") or {}).get("name")
        if name == "COMPLETED":
            if result == "SUCCESSFUL":
                return RunState.SUCCESS, name, result
            if result in ("FAILED", "ERROR"):
                return RunState.FAILED, name, result
            if result == "STOPPED":
                return RunState.HALTED, name, result
            return RunState.UNKNOWN, name, result
        if name in ("PENDING", "IN_PROGRESS", "BUILDING", "RUNNING"):
            return RunState.RUNNING, name, result
        if name in ("PAUSED", "HALTED"):
            return RunState.HALTED, name, result
        return RunState.UNKNOWN, name, result

    def _resolve_uuid(self, run_id: str, auth) -> tuple:
        """Accept a uuid ({...}) or a build number. Return (uuid, pipeline_json)."""
        run_id = run_id.strip()
        if run_id.isdigit():
            q = urllib.parse.urlencode(
                {"q": f"build_number={run_id}", "sort": "-created_on", "pagelen": 1}
            )
            data = api_get_json(f"{self._base()}/pipelines/?{q}", auth)
            values = data.get("values") or []
            if not values:
                raise RepipeError(
                    f"no pipeline with build number {run_id} in "
                    f"{self.workspace}/{self.repo}.",
                    EXIT_CONFIG,
                )
            pj = values[0]
            return pj["uuid"], pj
        uuid = run_id if run_id.startswith("{") else "{" + run_id + "}"
        pj = api_get_json(f"{self._base()}/pipelines/{urllib.parse.quote(uuid)}", auth)
        return uuid, pj

    def _run_from_json(self, uuid: str, pj: dict) -> Run:
        state, native_state, native_result = self._map_state(pj)
        target = pj.get("target") or {}
        selector = target.get("selector") or {}
        number = pj.get("build_number")
        return Run(
            id=uuid,
            number=number,
            state=state,
            native_state=native_state,
            native_result=native_result,
            ref=target.get("ref_name"),
            pipeline=selector.get("pattern"),
            web_url=self.web_url(number) if number is not None else None,
        )

    def get_run(self, run_id: str, auth) -> Run:
        uuid, pj = self._resolve_uuid(run_id, auth)
        return self._run_from_json(uuid, pj)

    def get_steps(self, run: Run, auth) -> list:
        uuid = urllib.parse.quote(run.id)
        data = api_get_json(f"{self._base()}/pipelines/{uuid}/steps/", auth)
        steps = []
        for sv in data.get("values") or []:
            result = (sv.get("state", {}).get("result") or {}).get("name")
            norm = RunState.FAILED if result in ("FAILED", "ERROR") else (
                RunState.SUCCESS if result == "SUCCESSFUL" else RunState.UNKNOWN
            )
            steps.append(
                Step(
                    name=sv.get("name") or "(unnamed step)",
                    state=norm,
                    native_result=result,
                    uuid=sv.get("uuid"),
                )
            )
        return steps

    def get_step_log(self, run: Run, step: Step, auth) -> str:
        uuid = urllib.parse.quote(run.id)
        suuid = urllib.parse.quote(step.uuid or "")
        return api_get_text(
            f"{self._base()}/pipelines/{uuid}/steps/{suuid}/log", auth
        )
