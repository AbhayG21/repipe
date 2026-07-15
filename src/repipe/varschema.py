"""Variable-schema layer: validate/resolve pipeline variables before trigger.

The yml self-describes only some constraints (e.g. MULTI's allowed-values);
this enriches what it omits (Project is an enum; FLAVOURS has a cross-field
rule with MULTI) and fails fast locally, so a bad combo errors in ~1s instead
of burning a real pipeline run that just `exit 1`s.
"""

from .errors import RepipeError, EXIT_CONFIG

# Enrichment the yml doesn't declare.
PROJECT_VALUES = ["PCI", "NON-PCI"]


def _validate_one(target_name, var, value, provided_by_default):
    where = "default" if provided_by_default else "value"
    # 1. yml-declared allowed-values (e.g. MULTI: [true, false]).
    if var.allowed_values and value not in var.allowed_values:
        raise RepipeError(
            f"variable '{var.name}' {where} '{value}' is not one of the "
            f"allowed values {var.allowed_values}.",
            EXIT_CONFIG,
        )
    # 2. Known-name enrichment.
    if var.name == "Project" and value not in PROJECT_VALUES:
        raise RepipeError(
            f"variable 'Project'='{value}' must be one of {PROJECT_VALUES}.",
            EXIT_CONFIG,
        )
    if var.name == "USEREMAIL" and "@" not in value:
        raise RepipeError(
            f"variable 'USEREMAIL'='{value}' does not look like an email address.",
            EXIT_CONFIG,
        )


def _validate_cross(target_name, values: dict):
    """Cross-field rule: MULTI governs whether FLAVOURS may be space-separated."""
    if "FLAVOURS" not in values:
        return
    flav = (values["FLAVOURS"] or "").strip()
    if not flav:
        raise RepipeError(
            f"variable 'FLAVOURS' must not be empty for '{target_name}'.",
            EXIT_CONFIG,
        )
    multi = str(values.get("MULTI", "false")).strip().lower() == "true"
    if not multi and " " in flav:
        raise RepipeError(
            "MULTI=false requires a single FLAVOURS value (no spaces). "
            "Set MULTI=true for multiple, space-separated flavours.",
            EXIT_CONFIG,
        )


def resolve_variables(target, provided: dict):
    """Merge provided (--var) with declared defaults, validate, and return
    an ordered list of (key, value) ready for the trigger body.

    Raises RepipeError (exit 3) on any missing-required or invalid combo.
    """
    final = []
    seen = set()

    for var in target.variables:
        if var.name in provided:
            value = provided[var.name]
            by_default = False
        elif var.default is not None:
            value = var.default
            by_default = True
        else:
            raise RepipeError(
                f"missing required variable '{var.name}' for pipeline "
                f"'{target.name}'. Pass --var {var.name}=<value>.",
                EXIT_CONFIG,
            )
        _validate_one(target.name, var, value, by_default)
        final.append((var.name, value))
        seen.add(var.name)

    # Pass through any extra --var values the yml didn't declare.
    for key, value in provided.items():
        if key not in seen:
            final.append((key, value))

    _validate_cross(target.name, dict(final))
    return final
