"""Local-notification module + CLI wiring (pure — no real notifications sent)."""

import unittest
from types import SimpleNamespace
from unittest import mock

from repipe import notify as notify_mod
from repipe import cli
from repipe.model import Run, RunState, Step, Target


def _args(**over):
    base = dict(notify=True, notify_steps=False, max_retries=2)
    base.update(over)
    return SimpleNamespace(**base)


TARGET = Target(name="deploy", env="qa")
RUN = Run(id="1", number=158, state=RunState.SUCCESS, pipeline="deploy",
          web_url="https://ci/158")


class NotifyModule(unittest.TestCase):
    def test_applescript_escaping(self):
        # backslash escaped first, then quote — no un-escaped delimiters survive
        self.assertEqual(notify_mod._applescript_str('a"b\\c'), 'a\\"b\\\\c')

    def test_macos_no_sound_omits_sound_clause(self):
        with mock.patch.object(notify_mod.sys, "platform", "darwin"), \
                mock.patch.object(notify_mod.subprocess, "run") as run:
            notify_mod.notify("t", "m", sound=False)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertIn('with title "t"', argv[2])
        self.assertNotIn("sound name", argv[2])

    def test_macos_sound_adds_sound_clause(self):
        with mock.patch.object(notify_mod.sys, "platform", "darwin"), \
                mock.patch.object(notify_mod.subprocess, "run") as run:
            notify_mod.notify("t", "m", sound=True)
        self.assertIn('sound name "default"', run.call_args.args[0][2])

    def test_linux_uses_notify_send_when_present(self):
        with mock.patch.object(notify_mod.sys, "platform", "linux"), \
                mock.patch.object(notify_mod.shutil, "which", return_value="/n"), \
                mock.patch.object(notify_mod.subprocess, "run") as run:
            notify_mod.notify("title", "msg")
        self.assertEqual(run.call_args.args[0], ["notify-send", "title", "msg"])

    def test_linux_without_notify_send_rings_bell(self):
        with mock.patch.object(notify_mod.sys, "platform", "linux"), \
                mock.patch.object(notify_mod.shutil, "which", return_value=None), \
                mock.patch.object(notify_mod, "_bell") as bell:
            notify_mod.notify("t", "m")
        bell.assert_called_once()

    def test_subprocess_failure_is_swallowed_and_falls_to_bell(self):
        with mock.patch.object(notify_mod.sys, "platform", "darwin"), \
                mock.patch.object(notify_mod.subprocess, "run",
                                  side_effect=OSError("boom")), \
                mock.patch.object(notify_mod, "_bell") as bell:
            notify_mod.notify("t", "m")  # must not raise
        bell.assert_called_once()


class NotifyRouting(unittest.TestCase):
    """cli._notify_result sound routing + gating."""

    def _capture(self, outcome, elapsed=100, **over):
        with mock.patch.object(cli.interactive, "live", return_value=True), \
                mock.patch.object(cli.notify_mod, "notify") as n:
            cli._notify_result(TARGET, RUN, outcome, elapsed, _args(**over))
        return n

    def test_final_result_plays_sound(self):
        n = self._capture("success")
        self.assertTrue(n.call_args.kwargs["sound"])

    def test_retry_is_silent(self):
        n = self._capture("retry")
        self.assertFalse(n.call_args.kwargs["sound"])

    def test_short_run_is_suppressed(self):
        n = self._capture("success", elapsed=5)
        n.assert_not_called()

    def test_disabled_by_flag(self):
        n = self._capture("success", notify=False)
        n.assert_not_called()

    def test_non_tty_suppressed(self):
        with mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli.notify_mod, "notify") as n:
            cli._notify_result(TARGET, RUN, "success", 100, _args())
        n.assert_not_called()


class StepDiff(unittest.TestCase):
    """cli._notify_new_steps: seed-silently-then-ping-on-transition."""

    class FakeProvider:
        def __init__(self, steps):
            self.steps = steps

        def get_steps(self, run, auth):
            return self.steps

    def test_running_then_success_pings_once(self):
        step = Step(name="build", state=RunState.UNKNOWN, uuid="s1")
        prov = self.FakeProvider([step])
        with mock.patch.object(cli.interactive, "live", return_value=True), \
                mock.patch.object(cli.notify_mod, "notify") as n:
            snap = cli._notify_new_steps(prov, TARGET, RUN, None, {}, _args())
            n.assert_not_called()                 # first sight seeds only
            prov.steps = [Step(name="build", state=RunState.SUCCESS, uuid="s1")]
            cli._notify_new_steps(prov, TARGET, RUN, None, snap, _args())
            self.assertEqual(n.call_count, 1)     # transition pings
            self.assertFalse(n.call_args.kwargs.get("sound", False))

    def test_already_done_on_first_sight_is_silent(self):
        prov = self.FakeProvider([Step(name="build", state=RunState.SUCCESS, uuid="s1")])
        with mock.patch.object(cli.interactive, "live", return_value=True), \
                mock.patch.object(cli.notify_mod, "notify") as n:
            cli._notify_new_steps(prov, TARGET, RUN, None, {}, _args())
        n.assert_not_called()


class WatchLoopStepPolling(unittest.TestCase):
    """The inner poll loop only calls get_steps when --notify-steps is on."""

    class LoopProvider:
        def __init__(self):
            self.get_steps_calls = 0
            self._done = Run(id="1", number=1, state=RunState.SUCCESS,
                             native_state="COMPLETED", web_url="u", pipeline="deploy")

        def get_run(self, rid, auth):
            return self._done

        def get_steps(self, run, auth):
            self.get_steps_calls += 1
            return [Step(name="build", state=RunState.SUCCESS, uuid="s1")]

    def _run_watch(self, notify_steps):
        prov = self.LoopProvider()
        running = Run(id="1", number=1, state=RunState.RUNNING,
                      native_state="IN_PROGRESS", pipeline="deploy")
        args = cli._run_args_namespace(poll_interval=0, timeout=100,
                                       notify=True, notify_steps=notify_steps)
        with mock.patch.object(cli.interactive, "live", return_value=True), \
                mock.patch.object(cli.notify_mod, "notify"):
            code = cli._watch_and_retry(prov, TARGET, "main", [], None, running, args)
        return prov, code

    def test_polls_steps_when_on(self):
        prov, code = self._run_watch(notify_steps=True)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(prov.get_steps_calls, 1)

    def test_no_step_polling_when_off(self):
        prov, code = self._run_watch(notify_steps=False)
        self.assertEqual(code, 0)
        self.assertEqual(prov.get_steps_calls, 0)


if __name__ == "__main__":
    unittest.main()
