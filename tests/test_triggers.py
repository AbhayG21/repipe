"""trigger_request body shapes for both adapters (pure — no network/auth),
plus the shared _finish_run gate/policy branches."""

import unittest
from types import SimpleNamespace
from unittest import mock

from repipe import cli
from repipe.model import Run, RunState, Target
from repipe.providers.bitbucket import BitbucketProvider
from repipe.providers.ghactions import GitHubActionsProvider


class BitbucketTrigger(unittest.TestCase):
    def test_pipeline_ref_target_body(self):
        p = BitbucketProvider("acme", "widget")
        t = Target(name="deploy-qa", env="qa")
        method, url, body = p.trigger_request(t, "qa-release-1", [("Env", "staging")])
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/acme/widget/pipelines/"))
        self.assertEqual(body["target"]["type"], "pipeline_ref_target")
        self.assertEqual(body["target"]["ref_name"], "qa-release-1")
        self.assertEqual(body["target"]["selector"]["pattern"], "deploy-qa")
        self.assertEqual(body["variables"], [{"key": "Env", "value": "staging", "secured": False}])


class GitHubTrigger(unittest.TestCase):
    def test_dispatch_body_uses_key_and_string_inputs(self):
        p = GitHubActionsProvider("acme", "widget")
        t = Target(name="Deploy Service", env="qa", key="deploy.yml")
        method, url, body = p.trigger_request(t, "main", [("environment", "canary"), ("dry_run", "false")])
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/repos/acme/widget/actions/workflows/deploy.yml/dispatches"))
        self.assertEqual(body["ref"], "main")
        self.assertEqual(body["inputs"], {"environment": "canary", "dry_run": "false"})

    def test_state_mapping(self):
        p = GitHubActionsProvider("acme", "widget")
        self.assertEqual(p._map_state({"status": "completed", "conclusion": "success"})[0], "SUCCESS")
        self.assertEqual(p._map_state({"status": "completed", "conclusion": "failure"})[0], "FAILED")
        self.assertEqual(p._map_state({"status": "in_progress", "conclusion": None})[0], "RUNNING")
        self.assertEqual(p._map_state({"status": "waiting", "conclusion": None})[0], "HALTED")


class ProdGate(unittest.TestCase):
    """_prod_gate is a Yes/No menu now — no retyped pipeline name."""

    PROD = Target(name="DEPLOY_PROD", env="prod")

    def _args(self, **over):
        base = dict(dry_run=False, yes=False)
        base.update(over)
        return SimpleNamespace(**base)

    def test_never_prompts_for_a_typed_name(self):
        with mock.patch.object(cli.interactive, "confirm_menu", return_value=True), \
                mock.patch("builtins.input",
                           side_effect=AssertionError("asked for typed input")), \
                mock.patch("builtins.print"):
            cli._prod_gate(self.PROD, self._args())

    def test_yes_proceeds(self):
        with mock.patch.object(cli.interactive, "confirm_menu",
                               return_value=True) as m, \
                mock.patch("builtins.print"):
            cli._prod_gate(self.PROD, self._args())
        m.assert_called_once()
        self.assertFalse(m.call_args.kwargs["default"])   # No is pre-selected

    def test_no_aborts(self):
        with mock.patch.object(cli.interactive, "confirm_menu", return_value=False), \
                mock.patch("builtins.print"):
            with self.assertRaises(cli.RepipeError) as e:
                cli._prod_gate(self.PROD, self._args())
        self.assertEqual(e.exception.code, cli.EXIT_CONFIG)

    def test_qa_and_dry_run_are_never_gated(self):
        with mock.patch.object(cli.interactive, "confirm_menu",
                               side_effect=AssertionError("gated")):
            cli._prod_gate(Target(name="DEPLOY_QA", env="qa"), self._args())
            cli._prod_gate(self.PROD, self._args(dry_run=True))

    def test_yes_flag_skips_the_menu(self):
        with mock.patch.object(cli.interactive, "confirm_menu",
                               side_effect=AssertionError("prompted under --yes")), \
                mock.patch("builtins.print"):
            cli._prod_gate(self.PROD, self._args(yes=True))

    def test_no_stdin_keeps_the_actionable_message(self):
        # Piped/CI: the picker can't read an answer, so re-raise with the --yes hint
        # rather than interactive's generic "interactive input required".
        boom = cli.RepipeError("interactive input required", cli.EXIT_CONFIG)
        with mock.patch.object(cli.interactive, "confirm_menu", side_effect=boom), \
                mock.patch("builtins.print"):
            with self.assertRaises(cli.RepipeError) as e:
                cli._prod_gate(self.PROD, self._args())
        self.assertIn("--yes", str(e.exception))


class FinishRunProdPolicy(unittest.TestCase):
    """The prod branches of cli._finish_run, driven offline with a fake provider."""

    class FakeProvider:
        NAME = "fake"
        TARGET_WORD = "pipeline"
        workspace = "ws"
        repo = "repo"

        def trigger_request(self, target, ref, variables):
            return "POST", "https://example.invalid/trigger", {}

        def trigger(self, target, ref, variables, auth):
            return Run(id="1", number=1, state=RunState.RUNNING)

    def _args(self, **over):
        base = dict(dry_run=False, yes=False, no_wait=True, force=False,
                    detach=False, path=".", retry_on=["oom"], max_retries=2)
        base.update(over)
        return SimpleNamespace(**base)

    def _run(self, target, args, confirmed=False):
        # Stub every side effect: remote probe, auth, announce, config write.
        with mock.patch.object(cli, "remote_has_branch", return_value=None), \
                mock.patch.object(cli, "get_auth", return_value=None), \
                mock.patch.object(cli, "_announce"), \
                mock.patch.object(cli, "_record_recent") as rec, \
                mock.patch("builtins.print"):
            code = cli._finish_run(self.FakeProvider(), target, "ref", [], args,
                                   confirmed=confirmed)
        return code, rec

    def test_confirmed_skips_the_typed_name_gate(self):
        # What the interactive flow now does: its Confirm step is the confirmation.
        target = Target(name="DEPLOY_PROD", env="prod")
        with mock.patch.object(cli, "_prod_gate",
                               side_effect=AssertionError("gate ran")) as gate:
            code, _ = self._run(target, self._args(), confirmed=True)
        self.assertEqual(code, cli.EXIT_OK)
        gate.assert_not_called()

    def test_unconfirmed_still_gates(self):
        # `run` / `rerun` keep the typed-name gate.
        target = Target(name="DEPLOY_PROD", env="prod")
        with mock.patch.object(cli, "_prod_gate") as gate:
            self._run(target, self._args(), confirmed=False)
        gate.assert_called_once()

    def test_prod_without_force_disables_retry(self):
        target = Target(name="DEPLOY_PROD", env="prod")
        args = self._args(force=False)
        self._run(target, args, confirmed=True)
        self.assertIsNone(args.retry_on)
        self.assertEqual(args.max_retries, 0)

    def test_prod_with_force_keeps_retry(self):
        # force=True is what `prod_retry` config resolves to via _resolve_prod_retry.
        target = Target(name="DEPLOY_PROD", env="prod")
        args = self._args(force=True)
        self._run(target, args, confirmed=True)
        self.assertEqual(args.retry_on, ["oom"])
        self.assertEqual(args.max_retries, 2)

    def test_qa_retry_untouched(self):
        target = Target(name="DEPLOY_QA", env="qa")
        args = self._args(force=False)
        self._run(target, args, confirmed=True)
        self.assertEqual(args.retry_on, ["oom"])
        self.assertEqual(args.max_retries, 2)

    def test_records_recents_after_a_real_trigger(self):
        target = Target(name="DEPLOY_QA", env="qa")
        _, rec = self._run(target, self._args(), confirmed=True)
        rec.assert_called_once_with("ws/repo", "qa", "DEPLOY_QA", "ref")

    def test_dry_run_records_nothing(self):
        target = Target(name="DEPLOY_QA", env="qa")
        _, rec = self._run(target, self._args(dry_run=True), confirmed=True)
        rec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
