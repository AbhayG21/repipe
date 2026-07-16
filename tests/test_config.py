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
