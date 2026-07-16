"""resolve_variables: config-driven validation, no hardcoded variable names."""

import unittest

from repipe.errors import RepipeError
from repipe.model import Target, Variable
from repipe.varschema import resolve_variables


def target(*variables):
    return Target(name="deploy", env="qa", variables=list(variables))


class ResolveVariables(unittest.TestCase):
    def test_enum_from_config_rejects_bad_value(self):
        t = target(Variable("Env"))
        schema = {"Env": {"enum": ["a", "b"]}}
        with self.assertRaises(RepipeError):
            resolve_variables(t, {"Env": "c"}, schema)
        # a good value passes and is returned in order
        self.assertEqual(resolve_variables(t, {"Env": "b"}, schema), [("Env", "b")])

    def test_yml_allowed_values_used_when_no_config_enum(self):
        t = target(Variable("Flag", allowed_values=["true", "false"]))
        with self.assertRaises(RepipeError):
            resolve_variables(t, {"Flag": "maybe"}, {})
        self.assertEqual(resolve_variables(t, {"Flag": "true"}, {}), [("Flag", "true")])

    def test_required_missing_raises(self):
        t = target(Variable("Svc"))            # no yml default
        with self.assertRaises(RepipeError):
            resolve_variables(t, {}, {})       # required-by-default

    def test_required_false_makes_it_optional(self):
        t = target(Variable("Svc"))
        self.assertEqual(resolve_variables(t, {}, {"Svc": {"required": False}}), [])

    def test_pattern(self):
        t = target(Variable("Owner"))
        schema = {"Owner": {"pattern": ".+@.+"}}
        with self.assertRaises(RepipeError):
            resolve_variables(t, {"Owner": "nope"}, schema)
        self.assertEqual(
            resolve_variables(t, {"Owner": "a@b"}, schema), [("Owner", "a@b")]
        )

    def test_config_default_precedence_over_yml_default(self):
        t = target(Variable("Env", default="ymldef"))
        out = resolve_variables(t, {}, {"Env": {"default": "cfgdef"}})
        self.assertEqual(out, [("Env", "cfgdef")])

    def test_no_spaces_unless(self):
        t = target(Variable("Svcs"), Variable("Multi", default="false"))
        schema = {"Svcs": {"no_spaces_unless": "Multi"}}
        # spaces + sibling falsy → reject
        with self.assertRaises(RepipeError):
            resolve_variables(t, {"Svcs": "a b", "Multi": "false"}, schema)
        # spaces + sibling truthy → ok
        self.assertEqual(
            resolve_variables(t, {"Svcs": "a b", "Multi": "true"}, schema)[0],
            ("Svcs", "a b"),
        )
        # single value always ok
        self.assertEqual(
            resolve_variables(t, {"Svcs": "a", "Multi": "false"}, schema)[0],
            ("Svcs", "a"),
        )

    def test_extra_var_passthrough_and_validation(self):
        t = target(Variable("A", default="x"))
        # undeclared extra var is passed through
        out = resolve_variables(t, {"B": "y"}, {})
        self.assertIn(("B", "y"), out)
        # but still validated against a config entry that names it
        with self.assertRaises(RepipeError):
            resolve_variables(t, {"B": "bad"}, {"B": {"enum": ["ok"]}})


if __name__ == "__main__":
    unittest.main()
