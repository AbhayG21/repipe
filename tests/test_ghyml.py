"""GitHub Actions workflow parser (.github/workflows/*.yml)."""

import unittest

from repipe.ghyml import parse_workflow

DISPATCH = """\
name: Deploy Service
on:
  workflow_dispatch:
    inputs:
      environment:
        description: "target"
        type: choice
        default: staging
        options:
          - staging
          - production
      dry_run:
        type: boolean
        default: false
      version:
        type: string
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: echo hi
"""

NO_DISPATCH = """\
name: CI
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo test
"""

INLINE_LIST = """\
name: Promote PROD
on: [push, workflow_dispatch]
jobs:
  go:
    runs-on: ubuntu-latest
    steps:
      - run: echo go
"""


class GhYmlParse(unittest.TestCase):
    def test_dispatch_workflow(self):
        t = parse_workflow(DISPATCH, "deploy.yml")
        self.assertIsNotNone(t)
        self.assertEqual(t.name, "Deploy Service")   # display name, not filename
        self.assertEqual(t.key, "deploy.yml")         # trigger handle
        self.assertEqual(t.env, "qa")
        v = {x.name: x for x in t.variables}
        self.assertEqual(v["environment"].allowed_values, ["staging", "production"])
        self.assertEqual(v["environment"].default, "staging")
        self.assertEqual(v["dry_run"].allowed_values, ["true", "false"])  # boolean
        self.assertEqual(v["version"].allowed_values, [])                 # string

    def test_no_dispatch_excluded(self):
        self.assertIsNone(parse_workflow(NO_DISPATCH, "ci.yml"))

    def test_inline_list_dispatch_no_inputs(self):
        t = parse_workflow(INLINE_LIST, "promote.yml")
        self.assertIsNotNone(t)
        self.assertEqual(t.env, "prod")           # name signals PROD
        self.assertEqual(t.variables, [])

    def test_name_falls_back_to_filename(self):
        text = "on: [workflow_dispatch]\njobs:\n  x:\n    runs-on: ubuntu-latest\n"
        t = parse_workflow(text, "release-thing.yaml")
        self.assertEqual(t.name, "release-thing")


if __name__ == "__main__":
    unittest.main()
