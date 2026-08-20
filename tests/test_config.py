"""config TOML emitter round-trip + accessors."""

import unittest

from repipe import config

try:
    import tomllib
except ImportError:                 # pragma: no cover
    tomllib = None


def sample_cfg():
    cfg = {"user_email": "dev@example.com", "repos": {}}
    r = config.ensure_repo(cfg, "ws/repo")
    r["provider"] = "bitbucket"
    r["variables"] = {
        "Env": {"enum": ["a", "b"], "default": "a", "required": True},
        "Svcs": {"remember": True, "no_spaces_unless": "Multi"},
    }
    return cfg


class ConfigRoundTrip(unittest.TestCase):
    def test_remember_value_dedups(self):
        cfg = {}
        config.remember_value(cfg, "ws/repo", "Svcs", "core")
        config.remember_value(cfg, "ws/repo", "Svcs", "core")
        config.remember_value(cfg, "ws/repo", "Svcs", "lite")
        self.assertEqual(config.get_remembered(cfg, "ws/repo")["Svcs"], ["core", "lite"])

    def test_repo_variables_accessor(self):
        cfg = sample_cfg()
        self.assertIn("Env", config.repo_variables(cfg, "ws/repo"))
        self.assertEqual(config.repo_variables(cfg, "missing/repo"), {})

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_emitter_roundtrips_notify_globals(self):
        cfg = {"notify": False, "notify_steps": True, "max_retries": 3}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertEqual(reparsed["notify"], False)
        self.assertEqual(reparsed["notify_steps"], True)
        self.assertEqual(reparsed["max_retries"], 3)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_notify_events_round_trips(self):
        cfg = {"notify_events": ["failed", "timeout"]}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertEqual(reparsed["notify_events"], ["failed", "timeout"])

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_per_repo_retry_on_round_trips(self):
        cfg = {"repos": {"ws/repo": {"provider": "github",
                                     "retry_on": ["flaky test", "oom"]}}}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertEqual(reparsed["repos"]["ws/repo"]["retry_on"],
                         ["flaky test", "oom"])

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_emitter_roundtrips_poll_and_timeout(self):
        cfg = {"poll_interval": 45, "timeout": 900}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertEqual(reparsed["poll_interval"], 45)
        self.assertEqual(reparsed["timeout"], 900)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_globals_edit_preserves_repo_schema(self):
        # Editing a global then re-emitting must not drop a hand-written
        # per-repo variables/remembered/last_run section (the `repipe config`
        # menu's core safety property).
        cfg = sample_cfg()
        config.remember_value(cfg, "ws/repo", "Svcs", "core")
        config.set_last_run(cfg, "ws/repo", "deploy", "main", "qa", {"Env": "a"})
        cfg["max_retries"] = 9  # a globals edit, as the menu would make

        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertEqual(reparsed["max_retries"], 9)
        r = reparsed["repos"]["ws/repo"]
        self.assertEqual(r["variables"]["Env"]["enum"], ["a", "b"])
        self.assertEqual(r["remembered"]["Svcs"], ["core"])
        self.assertEqual(r["last_run"]["vars"]["Env"], "a")

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_repo_field_edit_preserves_variable_schema(self):
        # What the `repipe config` repo submenu does: set a flat per-repo field,
        # then save. The hand-written variables schema must survive.
        cfg = sample_cfg()
        config.ensure_repo(cfg, "ws/repo")["provider"] = "github"
        config.ensure_repo(cfg, "ws/repo")["qa_branch_prefix"] = "qa-release-"

        reparsed = tomllib.loads(config.dumps(cfg))
        r = reparsed["repos"]["ws/repo"]
        self.assertEqual(r["provider"], "github")
        self.assertEqual(r["qa_branch_prefix"], "qa-release-")
        self.assertEqual(r["variables"]["Env"]["enum"], ["a", "b"])

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_full_variable_entry_roundtrips(self):
        # Every field the `repipe config` variable editor can set must survive a
        # dumps()->load() round-trip with its type intact.
        entry = {
            "enum": ["x", "y"], "default": "x", "required": False,
            "pattern": "^[a-z]+$", "autofill": "git_email", "remember": True,
            "no_spaces_unless": "MULTI", "hint": "pick one",
        }
        cfg = {"repos": {"ws/repo": {"variables": {"V": dict(entry)}}}}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertEqual(reparsed["repos"]["ws/repo"]["variables"]["V"], entry)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_emitter_preserves_variables_remembered_last_run(self):
        cfg = sample_cfg()
        config.remember_value(cfg, "ws/repo", "Svcs", "core")
        config.set_last_run(cfg, "ws/repo", "deploy", "main", "qa", {"Env": "a"})

        reparsed = tomllib.loads(config.dumps(cfg))
        r = reparsed["repos"]["ws/repo"]
        self.assertEqual(r["variables"]["Env"]["enum"], ["a", "b"])
        self.assertEqual(r["variables"]["Env"]["required"], True)
        self.assertEqual(r["variables"]["Svcs"]["no_spaces_unless"], "Multi")
        self.assertEqual(r["remembered"]["Svcs"], ["core"])
        self.assertEqual(r["last_run"]["pipeline"], "deploy")
        self.assertEqual(r["last_run"]["vars"]["Env"], "a")


class Recents(unittest.TestCase):
    """Per-env MRU history driving the interactive picker suggestions."""

    def test_records_per_env_most_recent_first(self):
        cfg = {}
        config.record_recent(cfg, "ws/repo", "qa", pipeline="DEPLOY", branch="qa-1")
        config.record_recent(cfg, "ws/repo", "qa", pipeline="MIGRATE", branch="qa-2")
        got = config.get_recent(cfg, "ws/repo", "qa")
        self.assertEqual(got["pipelines"], ["MIGRATE", "DEPLOY"])
        self.assertEqual(got["branches"], ["qa-2", "qa-1"])

    def test_reused_value_moves_to_front_without_duplicating(self):
        # MRU, unlike remember_value's append-only ordering.
        cfg = {}
        for b in ("qa-1", "qa-2", "qa-1"):
            config.record_recent(cfg, "ws/repo", "qa", branch=b)
        self.assertEqual(config.get_recent(cfg, "ws/repo", "qa")["branches"],
                         ["qa-1", "qa-2"])

    def test_caps_at_limit_dropping_oldest(self):
        cfg = {}
        for i in range(config._RECENT_LIMIT + 3):
            config.record_recent(cfg, "ws/repo", "qa", branch=f"qa-{i}")
        branches = config.get_recent(cfg, "ws/repo", "qa")["branches"]
        self.assertEqual(len(branches), config._RECENT_LIMIT)
        self.assertEqual(branches[0], f"qa-{config._RECENT_LIMIT + 2}")
        self.assertNotIn("qa-0", branches)

    def test_envs_are_independent(self):
        cfg = {}
        config.record_recent(cfg, "ws/repo", "qa", pipeline="DEPLOY_QA")
        config.record_recent(cfg, "ws/repo", "prod", pipeline="DEPLOY_PROD")
        self.assertEqual(config.get_recent(cfg, "ws/repo", "qa")["pipelines"],
                         ["DEPLOY_QA"])
        self.assertEqual(config.get_recent(cfg, "ws/repo", "prod")["pipelines"],
                         ["DEPLOY_PROD"])

    def test_unknown_repo_or_env_returns_empty_lists(self):
        self.assertEqual(config.get_recent({}, "no/repo", "qa"),
                         {"branches": [], "pipelines": []})
        cfg = {}
        config.record_recent(cfg, "ws/repo", "qa", branch="qa-1")
        self.assertEqual(config.get_recent(cfg, "ws/repo", "prod"),
                         {"branches": [], "pipelines": []})

    def test_nothing_to_record_is_a_noop(self):
        cfg = {}
        config.record_recent(cfg, "ws/repo", "qa")
        config.record_recent(cfg, "ws/repo", "", branch="qa-1")
        self.assertEqual(cfg, {})

    def test_get_last_run_accessor(self):
        cfg = {}
        self.assertEqual(config.get_last_run(cfg, "ws/repo"), {})
        config.set_last_run(cfg, "ws/repo", "deploy", "main", "qa", {})
        self.assertEqual(config.get_last_run(cfg, "ws/repo")["pipeline"], "deploy")

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_recents_survive_alongside_variables_remembered_last_run(self):
        # dumps() is a whitelist emitter — this is the "silently dropped on save"
        # regression guard.
        cfg = sample_cfg()
        config.remember_value(cfg, "ws/repo", "Svcs", "core")
        config.set_last_run(cfg, "ws/repo", "deploy", "main", "qa", {"Env": "a"})
        config.record_recent(cfg, "ws/repo", "qa", pipeline="DEPLOY", branch="qa-7")
        config.record_recent(cfg, "ws/repo", "prod", pipeline="SHIP", branch="prod-3")

        r = tomllib.loads(config.dumps(cfg))["repos"]["ws/repo"]
        self.assertEqual(r["recent"]["qa"]["pipelines"], ["DEPLOY"])
        self.assertEqual(r["recent"]["qa"]["branches"], ["qa-7"])
        self.assertEqual(r["recent"]["prod"]["branches"], ["prod-3"])
        # …and the pre-existing state is untouched.
        self.assertEqual(r["variables"]["Env"]["enum"], ["a", "b"])
        self.assertEqual(r["remembered"]["Svcs"], ["core"])
        self.assertEqual(r["last_run"]["pipeline"], "deploy")


class ProdRetryConfig(unittest.TestCase):
    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_global_prod_retry_round_trips(self):
        self.assertIs(tomllib.loads(config.dumps({"prod_retry": True}))["prod_retry"],
                      True)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_per_repo_explicit_false_round_trips(self):
        # An explicit per-repo false must survive — it's how a repo opts OUT of a
        # global prod_retry = true, so it can't be treated as "absent".
        cfg = {"prod_retry": True, "repos": {"ws/repo": {"prod_retry": False}}}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertIs(reparsed["repos"]["ws/repo"]["prod_retry"], False)

    @unittest.skipIf(tomllib is None, "tomllib requires Python 3.11+")
    def test_absent_per_repo_key_is_not_emitted(self):
        cfg = {"repos": {"ws/repo": {"provider": "bitbucket"}}}
        reparsed = tomllib.loads(config.dumps(cfg))
        self.assertNotIn("prod_retry", reparsed["repos"]["ws/repo"])


if __name__ == "__main__":
    unittest.main()
