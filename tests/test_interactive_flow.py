"""cmd_interactive wiring — driven offline with scripted pickers and a fake provider.

Covers the state repipe persists after a run: the recents that _finish_run writes
during the run must survive cmd_interactive's own post-run save.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from repipe import cli, config
from repipe.model import Run, RunState, Target

TARGETS = [
    Target(name="BUILD_QA", env="qa"),
    Target(name="DEPLOY_QA", env="qa"),
    Target(name="DEPLOY_PROD", env="prod"),
]


class FakeProvider:
    NAME = "bitbucket"
    TARGET_WORD = "pipeline"

    def __init__(self, workspace, repo):
        self.workspace = workspace
        self.repo = repo

    def parse_targets(self, worktree):
        return [Target(name=t.name, env=t.env) for t in TARGETS]

    def trigger_request(self, target, ref, variables):
        return "POST", "https://example.invalid/trigger", {}

    def trigger(self, target, ref, variables, auth):
        return Run(id="1", number=1, state=RunState.RUNNING)


class InteractiveFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _args(self, **over):
        base = dict(path=".", provider=None, dry_run=False, yes=False, detach=False)
        base.update(over)
        return SimpleNamespace(**base)

    def _run(self, picks, finish=None):
        """Drive cmd_interactive, answering each pick() in order from `picks`."""
        seq = list(picks)

        def fake_pick(label, items, **kw):
            want = seq.pop(0)
            for it in items:
                if getattr(it, "name", it) == want:
                    return it
            raise AssertionError(f"{want!r} not offered for {label!r}: {items}")

        with mock.patch.object(cli, "detect_repo",
                              return_value=("bitbucket.org", "acme", "widget", "main")), \
                mock.patch.object(cli, "choose_provider", return_value=FakeProvider), \
                mock.patch.object(cli, "branch_candidates", return_value=["main"]), \
                mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli.interactive, "banner"), \
                mock.patch.object(cli.interactive, "pick", side_effect=fake_pick), \
                mock.patch.object(cli, "_finish_run",
                                  side_effect=finish or (lambda *a, **k: cli.EXIT_OK)), \
                mock.patch("builtins.print"):
            code = cli.cmd_interactive(self._args())
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(seq, [], "not every scripted pick was consumed")
        return config.load()

    def test_recents_written_during_the_run_survive_the_post_run_save(self):
        # _finish_run records recents mid-run against its own freshly-loaded config.
        # cmd_interactive then saves last_run/remembered — from a copy it loaded
        # BEFORE the run. Without a re-read, that save drops the recents.
        def finish(provider, target, ref, variables, args, confirmed=False):
            cli._record_recent(f"{provider.workspace}/{provider.repo}",
                               target.env, target.name, ref)
            return cli.EXIT_OK

        cfg = self._run(["DEPLOY_QA", "qa", "main", "Trigger it"], finish=finish)
        r = config.get_repo(cfg, "acme/widget")
        self.assertEqual(config.get_recent(cfg, "acme/widget", "qa"),
                         {"branches": ["main"], "pipelines": ["DEPLOY_QA"]})
        self.assertEqual(r["last_run"]["pipeline"], "DEPLOY_QA")
        self.assertEqual(r["last_run"]["branch"], "main")

    def test_pipeline_cursor_starts_on_last_run(self):
        cfg = {}
        config.set_last_run(cfg, "acme/widget", "DEPLOY_QA", "main", "qa", {})
        config.save(cfg)

        seen = {}

        def fake_pick(label, items, **kw):
            if label == "Pipeline":
                seen["idx"] = kw.get("default_idx")
                seen["rows"] = [kw["to_str"](i) for i in items]
            return items[kw.get("default_idx", 0)] if label != "Confirm" else "Trigger it"

        with mock.patch.object(cli, "detect_repo",
                              return_value=("bitbucket.org", "acme", "widget", "main")), \
                mock.patch.object(cli, "choose_provider", return_value=FakeProvider), \
                mock.patch.object(cli, "branch_candidates", return_value=["main"]), \
                mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli.interactive, "banner"), \
                mock.patch.object(cli.interactive, "pick", side_effect=fake_pick), \
                mock.patch.object(cli, "_finish_run", return_value=cli.EXIT_OK), \
                mock.patch("builtins.print"):
            cli.cmd_interactive(self._args())

        self.assertEqual(seen["idx"], 1)                      # DEPLOY_QA's index
        self.assertNotIn("suggested", seen["rows"][0])         # BUILD_QA: never run
        self.assertNotIn("suggested", seen["rows"][1])         # last_run != recents

    def test_recent_pipelines_are_marked_suggested_per_env(self):
        cfg = {}
        config.record_recent(cfg, "acme/widget", "qa", pipeline="DEPLOY_QA")
        config.record_recent(cfg, "acme/widget", "prod", pipeline="DEPLOY_PROD")
        config.save(cfg)

        rows = {}

        def fake_pick(label, items, **kw):
            if label == "Pipeline":
                rows.update({i.name: kw["to_str"](i) for i in items})
            return items[0] if label != "Confirm" else "Trigger it"

        with mock.patch.object(cli, "detect_repo",
                              return_value=("bitbucket.org", "acme", "widget", "main")), \
                mock.patch.object(cli, "choose_provider", return_value=FakeProvider), \
                mock.patch.object(cli, "branch_candidates", return_value=["main"]), \
                mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli.interactive, "banner"), \
                mock.patch.object(cli.interactive, "pick", side_effect=fake_pick), \
                mock.patch.object(cli, "_finish_run", return_value=cli.EXIT_OK), \
                mock.patch("builtins.print"):
            cli.cmd_interactive(self._args())

        self.assertIn("suggested", rows["DEPLOY_QA"])
        self.assertIn("suggested", rows["DEPLOY_PROD"])
        self.assertNotIn("suggested", rows["BUILD_QA"])

    def test_branch_recents_come_first_and_are_marked(self):
        cfg = {}
        config.record_recent(cfg, "acme/widget", "qa", branch="qa-release-7")
        config.record_recent(cfg, "acme/widget", "prod", branch="prod-release-3")
        config.save(cfg)

        seen = {}

        def fake_pick(label, items, **kw):
            if label == "Branch":
                seen["items"] = list(items)
                seen["rows"] = [kw["to_str"](i) for i in items]
                seen["idx"] = kw.get("default_idx")
            return items[0] if label != "Confirm" else "Trigger it"

        with mock.patch.object(cli, "detect_repo",
                              return_value=("bitbucket.org", "acme", "widget", "main")), \
                mock.patch.object(cli, "choose_provider", return_value=FakeProvider), \
                mock.patch.object(cli, "branch_candidates",
                                  return_value=["qa-release-8", "main"]), \
                mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli.interactive, "banner"), \
                mock.patch.object(cli.interactive, "pick", side_effect=fake_pick), \
                mock.patch.object(cli, "_finish_run", return_value=cli.EXIT_OK), \
                mock.patch("builtins.print"):
            cli.cmd_interactive(self._args())

        # qa recents first (prod's must not leak in), then git candidates, then manual.
        self.assertEqual(seen["items"],
                         ["qa-release-7", "qa-release-8", "main", cli._MANUAL_BRANCH])
        self.assertEqual(seen["idx"], 0)
        self.assertIn("suggested", seen["rows"][0])
        self.assertNotIn("suggested", seen["rows"][1])
        self.assertNotIn("suggested", seen["rows"][-1])   # never tag the escape hatch


class ConfirmMenu(unittest.TestCase):
    """interactive.confirm_menu — Yes/No as a picker instead of a typed y/N."""

    def test_maps_selection_to_bool(self):
        from repipe import interactive
        with mock.patch.object(interactive, "pick",
                               side_effect=lambda l, items, **k: items[0]):
            self.assertIs(interactive.confirm_menu("go?"), True)
        with mock.patch.object(interactive, "pick",
                               side_effect=lambda l, items, **k: items[1]):
            self.assertIs(interactive.confirm_menu("go?"), False)

    def test_default_picks_the_matching_row(self):
        from repipe import interactive
        seen = {}

        def spy(label, items, **kw):
            seen["idx"] = kw["default_idx"]
            seen["rows"] = [kw["to_str"](i) for i in items]
            seen["back"] = kw["allow_back"]
            return items[kw["default_idx"]]

        with mock.patch.object(interactive, "pick", side_effect=spy):
            self.assertIs(interactive.confirm_menu("go?", default=True), True)
            self.assertEqual((seen["idx"], seen["rows"]), (0, ["Yes", "No"]))
            self.assertFalse(seen["back"])        # nothing to go back to
            self.assertIs(interactive.confirm_menu("go?", default=False), False)
            self.assertEqual(seen["idx"], 1)

    def test_numbered_fallback_reads_a_number(self):
        # No TTY in the test runner, so pick() degrades to the numbered prompt.
        from repipe import interactive
        with mock.patch.object(interactive, "_input", return_value="1"), \
                mock.patch("builtins.print"):
            self.assertIs(interactive.confirm_menu("go?"), True)
        with mock.patch.object(interactive, "_input", return_value="2"), \
                mock.patch("builtins.print"):
            self.assertIs(interactive.confirm_menu("go?", default=True), False)
        with mock.patch.object(interactive, "_input", return_value=""), \
                mock.patch("builtins.print"):
            self.assertIs(interactive.confirm_menu("go?", default=True), True)


class ProdPromptsAreMenus(unittest.TestCase):
    """The interactive prod tail asks via menus, never a typed y/N."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retry_and_detach_use_confirm_menu(self):
        picks = ["DEPLOY_PROD", "prod", "main", "Trigger it"]

        def fake_pick(label, items, **kw):
            want = picks.pop(0)
            for it in items:
                if getattr(it, "name", it) == want:
                    return it
            raise AssertionError(f"{want!r} not offered for {label!r}")

        with mock.patch.object(cli, "detect_repo",
                              return_value=("bitbucket.org", "acme", "widget", "main")), \
                mock.patch.object(cli, "choose_provider", return_value=FakeProvider), \
                mock.patch.object(cli, "branch_candidates", return_value=["main"]), \
                mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli.interactive, "banner"), \
                mock.patch.object(cli.interactive, "pick", side_effect=fake_pick), \
                mock.patch.object(cli.interactive, "confirm_menu",
                                  return_value=False) as menu, \
                mock.patch.object(cli.interactive, "confirm",
                                  side_effect=AssertionError("typed y/N used")), \
                mock.patch.object(cli, "_finish_run", return_value=cli.EXIT_OK), \
                mock.patch("builtins.print"):
            cli.cmd_interactive(SimpleNamespace(path=".", provider=None,
                                                dry_run=False, yes=False, detach=False))

        asked = [c.args[0] for c in menu.call_args_list]
        self.assertEqual(len(asked), 2, asked)
        self.assertIn("Auto-retry", asked[0])
        self.assertIn("background", asked[1])


if __name__ == "__main__":
    unittest.main()
