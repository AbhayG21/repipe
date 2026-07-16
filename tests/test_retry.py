"""retry pattern matching — opt-in only, no built-in defaults."""

import unittest

from repipe.retry import build_patterns, first_match


class Retry(unittest.TestCase):
    def test_no_builtin_defaults(self):
        self.assertEqual(build_patterns(None), [])
        self.assertEqual(build_patterns([]), [])
        self.assertEqual(build_patterns(["x"]), ["x"])

    def test_substring_case_insensitive(self):
        log = "ERROR: Could Not Resolve Host bitbucket.org"
        self.assertEqual(first_match(log, ["could not resolve host"]), "could not resolve host")

    def test_no_match_returns_none(self):
        self.assertIsNone(first_match("all good", ["oom"]))
        self.assertIsNone(first_match("", ["oom"]))
        self.assertIsNone(first_match("x", []))

    def test_regex_mode(self):
        self.assertEqual(first_match("exit code 137", [r"exit code \d+"], "regex"), r"exit code \d+")
        self.assertIsNone(first_match("clean", [r"\d\d\d"], "regex"))

    def test_invalid_regex_skipped_not_fatal(self):
        # first pattern is invalid regex; second matches
        self.assertEqual(first_match("oom killed", ["[unterminated", "oom"], "regex"), "oom")


if __name__ == "__main__":
    unittest.main()
