"""bitbucket-pipelines.yml subset parser + env classification."""

import unittest

from repipe.ymlparse import parse_pipelines_yml, classify_env

YML = """\
image: python:3
pipelines:
  custom:
    deploy-qa:
      - variables:
          - name: Environment
            default: staging
            allowed-values:
              - staging
              - production
          - name: Owner
      - step:
          script:
            - echo hi
    DEPLOY_PROD:
      - step:
          script:
            - echo prod
"""


class YmlParse(unittest.TestCase):
    def setUp(self):
        self.targets = parse_pipelines_yml(YML)
        self.by_name = {t.name: t for t in self.targets}

    def test_finds_custom_pipelines(self):
        self.assertEqual(set(self.by_name), {"deploy-qa", "DEPLOY_PROD"})

    def test_variables_and_allowed_values(self):
        v = {x.name: x for x in self.by_name["deploy-qa"].variables}
        self.assertEqual(v["Environment"].default, "staging")
        self.assertEqual(v["Environment"].allowed_values, ["staging", "production"])
        self.assertEqual(v["Owner"].default, None)

    def test_env_classification(self):
        self.assertEqual(self.by_name["deploy-qa"].env, "qa")
        self.assertEqual(self.by_name["DEPLOY_PROD"].env, "prod")

    def test_classify_env_direct(self):
        self.assertEqual(classify_env("build_CANARY"), "prod")
        self.assertEqual(classify_env("nightly"), "qa")

    def test_no_custom_block_is_empty(self):
        self.assertEqual(parse_pipelines_yml("image: x\n"), [])


if __name__ == "__main__":
    unittest.main()
