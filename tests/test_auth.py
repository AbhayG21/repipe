"""get_auth: env vars first, ~/.config/repipe/credentials file as fallback."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from repipe import http
from repipe.errors import RepipeError


class GetAuth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "repipe"), exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _env(self, **overrides):
        """A clean environment with XDG pointed at the temp dir + only what we pass."""
        env = {"XDG_CONFIG_HOME": self.tmp}
        env.update(overrides)
        return mock.patch.dict(os.environ, env, clear=True)

    def _write_creds(self, text, mode=0o600):
        path = os.path.join(self.tmp, "repipe", "credentials")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(path, mode)
        return path

    def test_env_only(self):
        with self._env(REPIPE_TOKEN="envtok"):
            self.assertEqual(http.get_auth(), ("bearer", "envtok"))

    def test_github_token_env(self):
        with self._env(GITHUB_TOKEN="ghp_x"):
            self.assertEqual(http.get_auth(), ("bearer", "ghp_x"))

    def test_file_only_bearer(self):
        self._write_creds("REPIPE_TOKEN=filetok\n")
        with self._env():
            self.assertEqual(http.get_auth(), ("bearer", "filetok"))

    def test_file_only_basic(self):
        self._write_creds('REPIPE_EMAIL="you@co.com"\nREPIPE_API_TOKEN=abc123\n')
        with self._env():
            self.assertEqual(http.get_auth(), ("basic", "you@co.com", "abc123"))

    def test_env_overrides_file(self):
        self._write_creds("REPIPE_TOKEN=fromfile\n")
        with self._env(REPIPE_TOKEN="fromenv"):
            self.assertEqual(http.get_auth(), ("bearer", "fromenv"))

    def test_missing_raises_when_required(self):
        with self._env():
            with self.assertRaises(RepipeError):
                http.get_auth()

    def test_missing_returns_none_when_optional(self):
        with self._env():
            self.assertIsNone(http.get_auth(required=False))

    def test_comments_blanks_and_quotes(self):
        self._write_creds(
            "# my creds\n\n"
            "REPIPE_EMAIL = 'you@co.com'  \n"
            "REPIPE_API_TOKEN=\"tok\"\n"
            "IGNORED_KEY=whatever\n"
        )
        with self._env():
            self.assertEqual(http.get_auth(), ("basic", "you@co.com", "tok"))

    def test_perms_warning_when_group_readable(self):
        self._write_creds("REPIPE_TOKEN=x\n", mode=0o644)
        buf = io.StringIO()
        with self._env(), redirect_stderr(buf):
            http.get_auth()
        self.assertIn("chmod 600", buf.getvalue())

    def test_no_warning_when_locked_down(self):
        self._write_creds("REPIPE_TOKEN=x\n", mode=0o600)
        buf = io.StringIO()
        with self._env(), redirect_stderr(buf):
            http.get_auth()
        self.assertEqual(buf.getvalue(), "")

    def test_save_credentials_roundtrip_bearer(self):
        with self._env():
            path = http.save_credentials({"REPIPE_TOKEN": "tok"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(http.get_auth(), ("bearer", "tok"))

    def test_save_credentials_roundtrip_basic(self):
        with self._env():
            http.save_credentials({"REPIPE_EMAIL": "e@x.com", "REPIPE_API_TOKEN": "a"})
            self.assertEqual(http.get_auth(), ("basic", "e@x.com", "a"))


if __name__ == "__main__":
    unittest.main()
