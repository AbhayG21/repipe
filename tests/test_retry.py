"""retry pattern matching — opt-in only, no built-in defaults."""

import unittest

from repipe import cli
from repipe.retry import SUGGESTED_RETRY_PATTERNS, build_patterns, first_match


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


class ResolveProdRetry(unittest.TestCase):
    """_resolve_prod_retry precedence: --force > per-repo prod_retry > global > off."""

    def test_explicit_force_wins_over_repo_off(self):
        cfg = {"prod_retry": False, "repos": {"w/r": {"prod_retry": False}}}
        self.assertTrue(cli._resolve_prod_retry(cfg, "w/r", True))

    def test_per_repo_on_overrides_global_off(self):
        cfg = {"prod_retry": False, "repos": {"w/r": {"prod_retry": True}}}
        self.assertTrue(cli._resolve_prod_retry(cfg, "w/r"))

    def test_per_repo_off_overrides_global_on(self):
        # The reason the resolver membership-tests instead of using .get().
        cfg = {"prod_retry": True, "repos": {"w/r": {"prod_retry": False}}}
        self.assertFalse(cli._resolve_prod_retry(cfg, "w/r"))

    def test_repo_without_override_falls_back_to_global(self):
        cfg = {"prod_retry": True, "repos": {"w/r": {}}}
        self.assertTrue(cli._resolve_prod_retry(cfg, "w/r"))

    def test_unconfigured_is_off(self):
        self.assertFalse(cli._resolve_prod_retry({}, "w/r"))


class SuggestedPatterns(unittest.TestCase):
    def test_timeout_spellings_present(self):
        for p in ("connection timed out", "connect timed out", "operation timed out"):
            self.assertIn(p, SUGGESTED_RETRY_PATTERNS)

    def test_no_bare_timeout_pattern(self):
        # A bare "timeout"/"timed out" would match repipe's own watch-deadline
        # output and most build-tool prose under substring matching.
        self.assertNotIn("timeout", SUGGESTED_RETRY_PATTERNS)
        self.assertNotIn("timed out", SUGGESTED_RETRY_PATTERNS)

    def test_list_invariants(self):
        self.assertEqual(SUGGESTED_RETRY_PATTERNS,
                         [p.lower() for p in SUGGESTED_RETRY_PATTERNS])
        self.assertEqual(len(SUGGESTED_RETRY_PATTERNS),
                         len(set(SUGGESTED_RETRY_PATTERNS)))

    def test_connect_timed_out_matches_java_socket_error(self):
        log = "Caused by: java.net.SocketTimeoutException: connect timed out"
        self.assertEqual(first_match(log, ["connect timed out"]), "connect timed out")
        # …and the pre-existing pattern alone would NOT have caught it.
        self.assertIsNone(first_match(log, ["connection timed out"]))

    def test_operation_timed_out_matches_curl_error(self):
        log = "curl: (28) Operation timed out after 30001 milliseconds"
        self.assertEqual(first_match(log, ["operation timed out"]),
                         "operation timed out")


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
