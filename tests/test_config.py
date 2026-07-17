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


if __name__ == "__main__":
    unittest.main()
