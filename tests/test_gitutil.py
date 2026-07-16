"""git remote URL parsing → (host, workspace, repo)."""

import unittest

from repipe.errors import RepipeError
from repipe.gitutil import parse_remote


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


if __name__ == "__main__":
    unittest.main()
