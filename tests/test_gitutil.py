"""git remote URL parsing → (host, workspace, repo)."""

import unittest
from unittest import mock

from repipe.errors import RepipeError
from repipe.gitutil import parse_remote, remote_has_branch


class ParseRemote(unittest.TestCase):
    def test_scp_form(self):
        self.assertEqual(
            parse_remote("git@bitbucket.org:acme/widget.git"),
            ("bitbucket.org", "acme", "widget"),
        )

    def test_ssh_form(self):
        self.assertEqual(
            parse_remote("ssh://git@github.com/acme/widget.git"),
            ("github.com", "acme", "widget"),
        )

    def test_https_form(self):
        self.assertEqual(
            parse_remote("https://github.com/acme/widget.git"),
            ("github.com", "acme", "widget"),
        )

    def test_https_with_user(self):
        self.assertEqual(
            parse_remote("https://user@bitbucket.org/acme/widget"),
            ("bitbucket.org", "acme", "widget"),
        )

    def test_bad_url_raises(self):
        with self.assertRaises(RepipeError):
            parse_remote("not-a-url")


class RemoteHasBranch(unittest.TestCase):
    def _with(self, git_out):
        return mock.patch("repipe.gitutil.run_git", return_value=git_out)

    def test_present(self):
        out = "deadbeef\trefs/heads/release/July28"
        with self._with(out):
            self.assertTrue(remote_has_branch("release/July28"))

    def test_absent_empty_output(self):
        # ls-remote exits 0 with no lines when the ref simply isn't there.
        with self._with(""):
            self.assertFalse(remote_has_branch("release/July28"))

    def test_unknown_when_git_fails(self):
        # run_git returns None on any real failure (no network/remote/auth) —
        # callers must not block on this.
        with self._with(None):
            self.assertIsNone(remote_has_branch("release/July28"))

    def test_suffix_is_not_a_match(self):
        # A ref that only matches as a path suffix must not count as present.
        out = "deadbeef\trefs/heads/team/release/July28"
        with self._with(out):
            self.assertFalse(remote_has_branch("release/July28"))


if __name__ == "__main__":
    unittest.main()
