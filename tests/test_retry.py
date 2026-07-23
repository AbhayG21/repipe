"""retry pattern matching — opt-in only, no built-in defaults."""

import unittest

from repipe import cli
from repipe.retry import build_patterns, first_match


class ResolveRetry(unittest.TestCase):
    """_resolve_retry precedence: explicit --retry-on > per-repo override > default."""

    CFG = {"retry_on": ["default"], "repos": {"w/r": {"retry_on": ["repo-only"]}}}

    def test_explicit_cli_wins(self):
        self.assertEqual(cli._resolve_retry(self.CFG, "w/r", ["cli"]), ["cli"])

    def test_per_repo_overrides_default(self):
        self.assertEqual(cli._resolve_retry(self.CFG, "w/r"), ["repo-only"])

    def test_repo_without_override_falls_back_to_default(self):
        cfg = {"retry_on": ["default"], "repos": {"w/r": {}}}
        self.assertEqual(cli._resolve_retry(cfg, "w/r"), ["default"])

    def test_no_config_is_none(self):
        self.assertIsNone(cli._resolve_retry({}, "w/r"))


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
