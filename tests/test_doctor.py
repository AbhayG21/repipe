"""cmd_doctor: exit codes + the one side effect (a test phone push)."""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

from repipe import cli
from repipe.errors import EXIT_OK, EXIT_CONFIG


class FakeProvider:
    NAME = "github"

    def __init__(self, code=200):
        self._code = code

    def verify_auth(self, auth):
        return self._code


def _args():
    return SimpleNamespace(path=".", provider=None)


def _run(*, repo=True, auth=("basic", "e@x.com", "t"), verify=200,
         cfg=None, live=True, isfile=False):
    """Drive cmd_doctor with everything external mocked. Returns (exit_code, push_mock, output)."""
    cfg = {} if cfg is None else cfg
    detect = (mock.patch.object(cli, "detect_repo",
                                return_value=("github.com", "AbhayG21", "repipe", "main"))
              if repo else
              mock.patch.object(cli, "detect_repo",
                                side_effect=cli.RepipeError("not a repo")))
    with detect, \
            mock.patch.object(cli, "choose_provider",
                              return_value=lambda w, r: FakeProvider(verify)), \
            mock.patch.object(cli, "get_auth", return_value=auth), \
            mock.patch.object(cli.config, "load", return_value=cfg), \
            mock.patch.object(cli.config, "config_path",
                              return_value="/nonexistent/repipe/config.toml"), \
            mock.patch("os.path.isfile", return_value=isfile), \
            mock.patch.object(cli.interactive, "live", return_value=live), \
            mock.patch.object(cli.notify_mod, "push") as push:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.cmd_doctor(_args())
    return code, push, buf.getvalue()


class Doctor(unittest.TestCase):
    def test_all_good_exits_zero(self):
        code, _, out = _run(cfg={"retry_on": ["x"], "notify": True})
        self.assertEqual(code, EXIT_OK)
        self.assertIn("all good", out)

    def test_no_credentials_is_a_hard_fail(self):
        code, _, out = _run(auth=None)
        self.assertEqual(code, EXIT_CONFIG)
        self.assertIn("none found", out)

    def test_rejected_auth_is_a_hard_fail(self):
        code, _, out = _run(verify=401)
        self.assertEqual(code, EXIT_CONFIG)
        self.assertIn("401", out)

    def test_missing_scope_warns_but_passes(self):
        code, _, _ = _run(verify=403, cfg={"retry_on": ["x"]})
        self.assertEqual(code, EXIT_OK)

    def test_empty_retry_patterns_warns_but_passes(self):
        code, _, out = _run(cfg={})
        self.assertEqual(code, EXIT_OK)
        self.assertIn("auto-retry is off", out)

    def test_test_push_sent_when_url_set_and_tty(self):
        code, push, out = _run(cfg={"notify_url": "https://ntfy.sh/t"}, live=True)
        self.assertEqual(code, EXIT_OK)
        push.assert_called_once()
        self.assertIn("test sent", out)

    def test_no_push_when_not_a_tty(self):
        code, push, out = _run(cfg={"notify_url": "https://ntfy.sh/t"}, live=False)
        push.assert_not_called()
        self.assertIn("configured", out)

    def test_runs_outside_a_repo(self):
        # not in a repo: repo + auth-verify skipped, but doctor still runs
        code, _, out = _run(repo=False, cfg={"retry_on": ["x"]})
        self.assertEqual(code, EXIT_OK)
        self.assertIn("not a recognized CI repo", out)


if __name__ == "__main__":
    unittest.main()
