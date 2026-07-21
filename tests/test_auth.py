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

    def test_ambiguous_token_over_basic_warns(self):
        # env token + file basic pair → bearer wins, but say so (the silent
        # override that caused a real 401 debugging session).
        self._write_creds("REPIPE_EMAIL=e@x.com\nREPIPE_API_TOKEN=a\n")
        buf = io.StringIO()
        with self._env(REPIPE_TOKEN="envtok"), redirect_stderr(buf):
            auth = http.get_auth()
        self.assertEqual(auth, ("bearer", "envtok"))
        self.assertIn("ignoring", buf.getvalue())
        self.assertIn("REPIPE_TOKEN", buf.getvalue())


def _http_error(url, code):
    return __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
        url, code, "msg", {}, None
    )


class RaiseHttp(unittest.TestCase):
    def test_401_names_host_and_credentials_file(self):
        with self.assertRaises(RepipeError) as cm:
            http._raise_http(_http_error("https://api.bitbucket.org/2.0/x", 401))
        msg = str(cm.exception)
        self.assertIn("401", msg)
        self.assertIn("bitbucket.org", msg)
        self.assertIn("credentials", msg)

    def test_403_github_scope_hint(self):
        with self.assertRaises(RepipeError) as cm:
            http._raise_http(_http_error("https://api.github.com/repos/x", 403))
        self.assertIn("actions:write", str(cm.exception))

    def test_403_bitbucket_scope_hint(self):
        with self.assertRaises(RepipeError) as cm:
            http._raise_http(_http_error("https://api.bitbucket.org/2.0/x", 403))
        self.assertIn("pipeline:bitbucket", str(cm.exception))


class LoginEmailBridge(unittest.TestCase):
    """`repipe login` (API-token method) must mirror the collected email into
    config's user_email, so `repipe config` doesn't show it as unset."""

    def test_mirrors_email_when_config_blank(self):
        from repipe import cli
        with mock.patch.object(cli.config, "load", return_value={}), \
                mock.patch.object(cli.config, "save") as save:
            got = cli._persist_login_email(
                {"REPIPE_EMAIL": "e@x.com", "REPIPE_API_TOKEN": "a"})
        self.assertEqual(got, "e@x.com")
        self.assertEqual(save.call_args.args[0]["user_email"], "e@x.com")

    def test_does_not_clobber_existing_user_email(self):
        from repipe import cli
        with mock.patch.object(cli.config, "load",
                               return_value={"user_email": "mine@x.com"}), \
                mock.patch.object(cli.config, "save") as save:
            got = cli._persist_login_email({"REPIPE_EMAIL": "e@x.com"})
        self.assertIsNone(got)
        save.assert_not_called()

    def test_noop_for_emailless_mapping(self):
        from repipe import cli
        with mock.patch.object(cli.config, "save") as save:
            got = cli._persist_login_email({"REPIPE_TOKEN": "t"})
        self.assertIsNone(got)
        save.assert_not_called()


class LoginEmailLookup(unittest.TestCase):
    """_login_email resolves the auth email: env wins over the credentials file."""

    def test_env_wins(self):
        from repipe import cli
        with mock.patch.dict(os.environ, {"REPIPE_EMAIL": "env@x.com"}), \
                mock.patch.object(cli.http, "_load_credentials_file",
                                  return_value={"REPIPE_EMAIL": "file@x.com"}):
            self.assertEqual(cli._login_email(), "env@x.com")

    def test_falls_back_to_credentials_file(self):
        from repipe import cli
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(cli.http, "_load_credentials_file",
                                  return_value={"REPIPE_EMAIL": "file@x.com"}):
            self.assertEqual(cli._login_email(), "file@x.com")


class AccessTokenUrl(unittest.TestCase):
    def test_deep_links_to_repo_when_known(self):
        from repipe import cli
        self.assertEqual(
            cli._access_token_url("me-cleartrip", "supply-core-new"),
            "https://bitbucket.org/me-cleartrip/supply-core-new/admin/access-tokens")

    def test_generic_path_when_repo_unknown(self):
        from repipe import cli
        url = cli._access_token_url(None, None)
        self.assertIn("bitbucket.org", url)
        self.assertIn("Access tokens", url)


if __name__ == "__main__":
    unittest.main()
