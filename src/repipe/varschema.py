"""Variable-schema layer: validate/resolve pipeline variables before trigger.

The pipeline yml self-describes only some constraints (e.g. a variable's
allowed-values list). Each org enriches the rest — enums, required flags,
regex patterns, cross-field rules — via a per-repo `[variables]` table in
config. Validation runs locally so a bad combo errors in ~1s instead of
burning a real pipeline run that just `exit 1`s.

Nothing here is org- or convention-specific: every constraint is read from the
`schema` argument, a `{varname: {enum, default, required, pattern, autofill,
remember, no_spaces_unless, hint}}` dict supplied by the caller.
"""

import re

from .errors import RepipeError, EXIT_CONFIG


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1", "on")


def allowed_values_for(var, entry) -> list:
    """Effective allowed-values: config `enum` if present, else yml-declared."""
    return list(entry.get("enum") or var.allowed_values or [])


def _validate_one(var, entry, value, provided_by_default):
    where = "default" if provided_by_default else "value"
    allowed = allowed_values_for(var, entry)
    if allowed and value not in allowed:
        raise RepipeError(
            f"variable '{var.name}' {where} '{value}' is not one of the "
            f"allowed values {allowed}.",
            EXIT_CONFIG,
        )
    pattern = entry.get("pattern")
    if pattern:
        try:
            ok = re.search(pattern, str(value)) is not None
        except re.error:
            ok = True  # a bad pattern in config shouldn't block the user
        if not ok:
            raise RepipeError(
                f"variable '{var.name}' {where} '{value}' does not match the "
                f"required pattern /{pattern}/.",
                EXIT_CONFIG,
            )


def _validate_cross(schema: dict, values: dict):
    """Generic cross-field rule: a variable may contain spaces only when the
    sibling var named by its `no_spaces_unless` is truthy."""
    for name, entry in schema.items():
        sibling = entry.get("no_spaces_unless")
        if not sibling or name not in values:
            continue
        val = (values.get(name) or "").strip()
        if val and " " in val and not _truthy(values.get(sibling, "")):
            raise RepipeError(
                f"variable '{name}'='{val}' may not contain spaces unless "
                f"'{sibling}' is set true.",
                EXIT_CONFIG,
            )


def resolve_variables(target, provided: dict, schema: dict = None):
    """Merge provided (--var) with declared/config defaults, validate against
    the per-repo `schema`, and return an ordered list of (key, value) ready for
    the trigger body.

    `schema` is `{varname: {enum, default, required, pattern, ...}}` (from
    config). Raises RepipeError (exit 3) on any missing-required or invalid
    combo.
    """
    schema = schema or {}
    final = []
    seen = set()

    for var in target.variables:
        entry = schema.get(var.name, {})
        if var.name in provided:
            value = provided[var.name]
            by_default = False
        elif entry.get("default") is not None:
            value = entry["default"]
            by_default = True
        elif var.default is not None:
            value = var.default
            by_default = True
        else:
            # A declared variable with no value and no default is required
            # unless config explicitly marks it optional (`required = false`).
            required = entry.get("required")
            if required is None:
                required = True
            if required:
                raise RepipeError(
                    f"missing required variable '{var.name}' for pipeline "
                    f"'{target.name}'. Pass --var {var.name}=<value>.",
                    EXIT_CONFIG,
                )
            continue
        _validate_one(var, entry, value, by_default)
        final.append((var.name, value))
        seen.add(var.name)

    # Pass through any extra --var values the yml didn't declare (validate
    # against any config entry that names them).
    for key, value in provided.items():
        if key not in seen:
            _validate_one_extra(key, schema.get(key, {}), value)
            final.append((key, value))

    _validate_cross(schema, dict(final))
    return final


def _validate_one_extra(name, entry, value):
    """Validate an extra --var (no yml Variable to attach to)."""
    allowed = list(entry.get("enum") or [])
    if allowed and value not in allowed:
        raise RepipeError(
            f"variable '{name}' value '{value}' is not one of the allowed "
            f"values {allowed}.",
            EXIT_CONFIG,
        )
    pattern = entry.get("pattern")
    if pattern:
        try:
            ok = re.search(pattern, str(value)) is not None
        except re.error:
            ok = True
        if not ok:
            raise RepipeError(
                f"variable '{name}' value '{value}' does not match the "
                f"required pattern /{pattern}/.",
                EXIT_CONFIG,
            )
