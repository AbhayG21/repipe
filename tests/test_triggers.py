"""trigger_request body shapes for both adapters (pure — no network/auth)."""

import unittest

from repipe.model import Target
from repipe.providers.bitbucket import BitbucketProvider
from repipe.providers.ghactions import GitHubActionsProvider


class BitbucketTrigger(unittest.TestCase):
    def test_pipeline_ref_target_body(self):
        p = BitbucketProvider("acme", "widget")
        t = Target(name="deploy-qa", env="qa")
        method, url, body = p.trigger_request(t, "qa-release-1", [("Env", "staging")])
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/acme/widget/pipelines/"))
        self.assertEqual(body["target"]["type"], "pipeline_ref_target")
        self.assertEqual(body["target"]["ref_name"], "qa-release-1")
        self.assertEqual(body["target"]["selector"]["pattern"], "deploy-qa")
        self.assertEqual(body["variables"], [{"key": "Env", "value": "staging", "secured": False}])


class GitHubTrigger(unittest.TestCase):
    def test_dispatch_body_uses_key_and_string_inputs(self):
        p = GitHubActionsProvider("acme", "widget")
        t = Target(name="Deploy Service", env="qa", key="deploy.yml")
        method, url, body = p.trigger_request(t, "main", [("environment", "canary"), ("dry_run", "false")])
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/repos/acme/widget/actions/workflows/deploy.yml/dispatches"))
        self.assertEqual(body["ref"], "main")
        self.assertEqual(body["inputs"], {"environment": "canary", "dry_run": "false"})

    def test_state_mapping(self):
        p = GitHubActionsProvider("acme", "widget")
        self.assertEqual(p._map_state({"status": "completed", "conclusion": "success"})[0], "SUCCESS")
        self.assertEqual(p._map_state({"status": "completed", "conclusion": "failure"})[0], "FAILED")
        self.assertEqual(p._map_state({"status": "in_progress", "conclusion": None})[0], "RUNNING")
        self.assertEqual(p._map_state({"status": "waiting", "conclusion": None})[0], "HALTED")


if __name__ == "__main__":
    unittest.main()
