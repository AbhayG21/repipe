"""Local-notification module + CLI wiring (pure — no real notifications sent)."""

import json
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


class Resp:
    """Minimal urlopen() return — push() only calls .close() on it."""
    def close(self):
        pass


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


class PushTransport(unittest.TestCase):
    """notify.push builds ntfy's JSON publish request correctly and never raises."""

    def _capture(self, *a, **kw):
        cap = {}

        def fake(req, timeout=None):
            cap["req"] = req
            cap["timeout"] = timeout
            cap["body"] = json.loads(req.data.decode("utf-8")) if req.data else {}
            return Resp()

        with mock.patch.object(notify_mod.urllib.request, "urlopen", side_effect=fake):
            notify_mod.push(*a, **kw)
        return cap

    def test_json_publish_to_server_root(self):
        cap = self._capture(
            "https://ntfy.sh/topic", "T", "M",
            priority="high", tags="x", click="https://ci/1", token="tk_1",
        )
        req, body = cap["req"], cap["body"]
        # JSON publishing posts to the server ROOT with the topic in the body,
        # NOT to the topic path — that's what makes UTF-8 titles safe.
        self.assertEqual(req.full_url, "https://ntfy.sh/")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Authorization"), "Bearer tk_1")
        self.assertEqual(cap["timeout"], 5)
        self.assertEqual(body["topic"], "topic")
        self.assertEqual(body["title"], "T")
        self.assertEqual(body["message"], "M")
        self.assertEqual(body["priority"], 4)          # "high" mapped to int
        self.assertEqual(body["tags"], ["x"])
        self.assertEqual(body["click"], "https://ci/1")

    def test_topic_parsed_from_self_hosted_url(self):
        body = self._capture("https://ntfy.example.com/my-topic", "T", "M")["body"]
        self.assertEqual(body["topic"], "my-topic")

    def test_optional_fields_omitted_when_empty(self):
        cap = self._capture("https://ntfy.sh/t", "T", "M")
        self.assertNotIn("tags", cap["body"])
        self.assertNotIn("click", cap["body"])
        self.assertIsNone(cap["req"].get_header("Authorization"))
        self.assertEqual(cap["body"]["priority"], 3)   # "default" mapped to int

    def test_utf8_title_survives_intact(self):
        # the whole reason for JSON publishing: a `·` and even emoji in the title
        # come through UTF-8-clean (the header API would mojibake them).
        body = self._capture("https://ntfy.sh/t", "repipe · déjà 🚀", "✓ ok")["body"]
        self.assertEqual(body["title"], "repipe · déjà 🚀")
        self.assertEqual(body["message"], "✓ ok")

    def test_no_url_is_a_noop(self):
        with mock.patch.object(notify_mod.urllib.request, "urlopen") as u:
            notify_mod.push("", "T", "M")
        u.assert_not_called()

    def test_network_error_is_swallowed(self):
        with mock.patch.object(notify_mod.urllib.request, "urlopen",
                               side_effect=OSError("boom")):
            notify_mod.push("https://ntfy.sh/t", "T", "M")  # must not raise


class PushGating(unittest.TestCase):
    """_notify_result routes to the phone channel on a url-gate, not a TTY-gate."""

    def _push(self, outcome, elapsed=100, **over):
        over.setdefault("push_cfg", {"notify_url": "https://ntfy.sh/t"})
        with mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli, "_notify_token", return_value=None), \
                mock.patch.object(cli.notify_mod, "notify") as local, \
                mock.patch.object(cli.notify_mod, "push") as push:
            cli._notify_result(TARGET, RUN, outcome, elapsed, _args(**over))
        return local, push

    def test_fires_headless_when_url_set(self):
        # THE keystone: no TTY (VM case), but a notify_url → push still fires,
        # while the TTY-gated local channel correctly stays silent.
        local, push = self._push("success")
        local.assert_not_called()
        push.assert_called_once()
        self.assertEqual(push.call_args.kwargs["priority"], "default")
        self.assertEqual(push.call_args.kwargs["tags"], "white_check_mark")
        self.assertEqual(push.call_args.kwargs["click"], RUN.web_url)

    def test_failed_is_high_priority(self):
        _, push = self._push("failed")
        self.assertEqual(push.call_args.kwargs["priority"], "high")
        self.assertEqual(push.call_args.kwargs["tags"], "x")

    def test_retry_is_low_priority(self):
        _, push = self._push("retry")
        self.assertEqual(push.call_args.kwargs["priority"], "low")

    def test_short_run_suppresses_push_too(self):
        _, push = self._push("success", elapsed=5)
        push.assert_not_called()

    def test_flag_off_suppresses_push(self):
        _, push = self._push("success", phone_notify=False)
        push.assert_not_called()

    def test_no_url_no_push(self):
        with mock.patch.object(cli.interactive, "live", return_value=True), \
                mock.patch.object(cli.notify_mod, "notify"), \
                mock.patch.object(cli.notify_mod, "push") as push:
            cli._notify_result(TARGET, RUN, "success", 100, _args())
        push.assert_not_called()


class PushGchatTransport(unittest.TestCase):
    """notify.push_gchat builds a cardsV2 webhook POST and never raises."""

    def _capture(self, *a, **kw):
        cap = {}

        def fake(req, timeout=None):
            cap["req"] = req
            cap["timeout"] = timeout
            cap["body"] = json.loads(req.data.decode("utf-8")) if req.data else {}
            return Resp()

        with mock.patch.object(notify_mod.urllib.request, "urlopen", side_effect=fake):
            notify_mod.push_gchat(*a, **kw)
        return cap

    def test_posts_cardsv2_to_webhook_url_verbatim(self):
        url = "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=t"
        cap = self._capture(url, "repipe · deploy", "✓ #158 succeeded",
                            click="https://ci/158")
        req, body = cap["req"], cap["body"]
        # posted to the webhook URL as-is (no topic extraction like ntfy)
        self.assertEqual(req.full_url, url)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(cap["timeout"], 5)
        # top-level text drives the notification preview (card-only = generic banner)
        self.assertIn("repipe · deploy", body["text"])
        self.assertIn("succeeded", body["text"])
        card = body["cardsV2"][0]["card"]
        self.assertEqual(card["header"]["title"], "repipe · deploy")
        widgets = card["sections"][0]["widgets"]
        self.assertEqual(widgets[0]["decoratedText"]["text"], "✓ #158 succeeded")
        btn = widgets[1]["buttonList"]["buttons"][0]
        self.assertEqual(btn["onClick"]["openLink"]["url"], "https://ci/158")

    def test_button_omitted_without_click(self):
        body = self._capture("https://chat.googleapis.com/x", "T", "M")["body"]
        widgets = body["cardsV2"][0]["card"]["sections"][0]["widgets"]
        self.assertEqual(len(widgets), 1)  # decoratedText only, no buttonList

    def test_ignores_ntfy_only_kwargs(self):
        # dispatch fans out one uniform call; gchat must swallow priority/tags/token
        self._capture("https://chat.googleapis.com/x", "T", "M",
                      priority="high", tags="x", token="tk")  # must not raise

    def test_no_url_is_a_noop(self):
        with mock.patch.object(notify_mod.urllib.request, "urlopen") as u:
            notify_mod.push_gchat("", "T", "M")
        u.assert_not_called()

    def test_network_error_is_swallowed(self):
        with mock.patch.object(notify_mod.urllib.request, "urlopen",
                               side_effect=OSError("boom")):
            notify_mod.push_gchat("https://chat.googleapis.com/x", "T", "M")


class PushSlackTransport(unittest.TestCase):
    """notify.push_slack builds a Block Kit webhook POST and never raises."""

    def _capture(self, *a, **kw):
        cap = {}

        def fake(req, timeout=None):
            cap["req"] = req
            cap["timeout"] = timeout
            cap["body"] = json.loads(req.data.decode("utf-8")) if req.data else {}
            return Resp()

        with mock.patch.object(notify_mod.urllib.request, "urlopen", side_effect=fake):
            notify_mod.push_slack(*a, **kw)
        return cap

    def test_posts_blocks_with_button(self):
        cap = self._capture("https://hooks.slack.com/services/T/B/xyz",
                            "repipe · deploy", "✓ #158 succeeded",
                            click="https://ci/158")
        body = cap["body"]
        self.assertEqual(cap["req"].full_url, "https://hooks.slack.com/services/T/B/xyz")
        self.assertIn("blocks", body)
        self.assertIn("repipe · deploy", body["blocks"][0]["text"]["text"])
        btn = body["blocks"][1]["elements"][0]
        self.assertEqual(btn["url"], "https://ci/158")
        self.assertIn("text", body)  # top-level fallback present

    def test_button_omitted_without_click(self):
        body = self._capture("https://hooks.slack.com/x", "T", "M")["body"]
        self.assertEqual(len(body["blocks"]), 1)  # section only, no actions

    def test_ignores_ntfy_only_kwargs(self):
        self._capture("https://hooks.slack.com/x", "T", "M",
                      priority="high", tags="x", token="tk")  # must not raise

    def test_no_url_is_a_noop(self):
        with mock.patch.object(notify_mod.urllib.request, "urlopen") as u:
            notify_mod.push_slack("", "T", "M")
        u.assert_not_called()

    def test_network_error_is_swallowed(self):
        with mock.patch.object(notify_mod.urllib.request, "urlopen",
                               side_effect=OSError("boom")):
            notify_mod.push_slack("https://hooks.slack.com/x", "T", "M")


class MultiProviderDispatch(unittest.TestCase):
    """_notify_result fans a run event out to EVERY configured provider."""

    def _run(self, push_cfg, **over):
        over.setdefault("phone_notify", True)
        with mock.patch.object(cli.interactive, "live", return_value=False), \
                mock.patch.object(cli, "_notify_token", return_value=None), \
                mock.patch.object(cli.notify_mod, "notify"), \
                mock.patch.object(cli.notify_mod, "push") as ntfy, \
                mock.patch.object(cli.notify_mod, "push_gchat") as gchat, \
                mock.patch.object(cli.notify_mod, "push_slack") as slack:
            cli._notify_result(TARGET, RUN, "success", 100,
                               _args(push_cfg=push_cfg, **over))
        return ntfy, gchat, slack

    def test_all_fire_when_all_configured(self):
        ntfy, gchat, slack = self._run({
            "notify_url": "https://ntfy.sh/t",
            "notify_gchat_url": "https://chat.googleapis.com/x",
            "notify_slack_url": "https://hooks.slack.com/x",
        })
        ntfy.assert_called_once()
        gchat.assert_called_once()
        slack.assert_called_once()
        self.assertEqual(slack.call_args.kwargs["click"], RUN.web_url)

    def test_only_configured_provider_fires(self):
        ntfy, gchat, slack = self._run({"notify_gchat_url": "https://chat.googleapis.com/x"})
        ntfy.assert_not_called()
        gchat.assert_called_once()
        slack.assert_not_called()

    def test_phone_off_suppresses_all(self):
        ntfy, gchat, slack = self._run(
            {"notify_url": "https://ntfy.sh/t",
             "notify_gchat_url": "https://chat.googleapis.com/x"},
            phone_notify=False)
        ntfy.assert_not_called()
        gchat.assert_not_called()
        slack.assert_not_called()


class EventFilter(unittest.TestCase):
    """notify_events gates which outcomes notify, across every channel."""

    def _fire(self, outcome, notify_events):
        with mock.patch.object(cli.interactive, "live", return_value=True), \
                mock.patch.object(cli, "_notify_token", return_value=None), \
                mock.patch.object(cli.notify_mod, "notify") as local, \
                mock.patch.object(cli.notify_mod, "push") as push:
            cli._notify_result(TARGET, RUN, outcome, 100,
                               _args(push_cfg={"notify_url": "https://ntfy.sh/t"},
                                     phone_notify=True, notify_events=notify_events))
        return local, push

    def test_disabled_outcome_suppresses_all_channels(self):
        local, push = self._fire("success", ["failed"])
        local.assert_not_called()
        push.assert_not_called()

    def test_enabled_outcome_fires(self):
        local, push = self._fire("failed", ["failed"])
        local.assert_called_once()
        push.assert_called_once()

    def test_none_means_all_on(self):
        local, push = self._fire("success", None)
        local.assert_called_once()
        push.assert_called_once()


class MaskSecret(unittest.TestCase):
    def test_masks_credential_keeps_host_and_tail(self):
        u = "https://chat.googleapis.com/v1/spaces/AAA/messages?key=SECRETKEY&token=abcd1234"
        m = cli._mask_secret(u)
        self.assertTrue(m.startswith("https://chat.googleapis.com/"))
        self.assertIn("…", m)
        self.assertTrue(m.endswith("1234"))
        self.assertNotIn("SECRETKEY", m)

    def test_empty_is_empty(self):
        self.assertEqual(cli._mask_secret(""), "")


class NotifyUrlConfig(unittest.TestCase):
    def test_provider_urls_survive_round_trip(self):
        import tomllib
        from repipe import config as cfgmod
        cfg = {"notify_url": "https://ntfy.sh/repipe-secret",
               "notify_gchat_url": "https://chat.googleapis.com/v1/spaces/A/messages?key=k",
               "notify": True}
        loaded = tomllib.loads(cfgmod.dumps(cfg))
        self.assertEqual(loaded["notify_url"], "https://ntfy.sh/repipe-secret")
        self.assertEqual(loaded["notify_gchat_url"],
                         "https://chat.googleapis.com/v1/spaces/A/messages?key=k")


if __name__ == "__main__":
    unittest.main()
