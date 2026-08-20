"""Config + persisted state at ~/.config/repipe/config.toml (non-secret only).

Read with stdlib tomllib (3.11+). stdlib has no TOML *writer*, so we emit our
own for the small, known schema below — repos keyed by "<ws>/<repo>", plus a
hand-edited per-repo `[…variables]` schema and tool-persisted state
(`[…remembered]` values, `last_run` that `rerun` reads back, and
`[…recent.<env>]` MRU lists that the interactive pickers suggest from).

NOTE: dumps() is a *whitelist* emitter — a key it doesn't know about is dropped
on the next save(). Any new setting must be added there too.
"""

import os
import re

# How many recently-executed branches/pipelines to keep per env.
_RECENT_LIMIT = 5

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - older interpreters
    tomllib = None


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "repipe")


def config_path() -> str:
    return os.path.join(config_dir(), "config.toml")


def load() -> dict:
    path = config_path()
    if not os.path.isfile(path) or tomllib is None:
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def save(cfg: dict) -> str:
    os.makedirs(config_dir(), exist_ok=True)
    path = config_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(cfg))
    return path


# --- minimal TOML emitter for our schema ------------------------------------

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _string(s: str) -> str:
    s = (
        str(s)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return '"' + s + '"'


def _key(k: str) -> str:
    return k if _BARE.match(str(k)) else _string(k)


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    return _string(v)


def dumps(cfg: dict) -> str:
    from . import notify  # provider config keys, so they round-trip losslessly
    push_keys = tuple(p["config_key"] for p in notify.PUSH_PROVIDERS)
    lines = []
    for k in ("user_email", "match", "max_retries",
              "poll_interval", "timeout", "prod_retry", "notify", "notify_steps",
              "notify_events") + push_keys:
        if k in cfg:
            lines.append(f"{k} = {_val(cfg[k])}")
    if "retry_on" in cfg:
        lines.append(f"retry_on = {_val(cfg['retry_on'])}")

    _VAR_FIELDS = (
        "enum", "default", "required", "pattern",
        "autofill", "remember", "no_spaces_unless", "hint",
    )
    for key, r in (cfg.get("repos") or {}).items():
        header = f"repos.{_key(key)}"
        lines.append("")
        lines.append(f"[{header}]")
        for k in ("provider", "qa_branch_prefix", "prod_branch_prefix"):
            if k in r:
                lines.append(f"{k} = {_val(r[k])}")
        # Tri-state: absent ⇒ inherit the global. An explicit false must survive
        # the round-trip so a repo can opt OUT of a global `prod_retry = true`.
        if "prod_retry" in r:
            lines.append(f"prod_retry = {_val(bool(r['prod_retry']))}")
        if r.get("retry_on"):  # per-repo override of the default retry patterns
            lines.append(f"retry_on = {_val(r['retry_on'])}")
        # Per-variable schema (hand-edited). Re-emitted so a tool-triggered
        # save() never drops the user's constraints.
        for vname, entry in (r.get("variables") or {}).items():
            lines.append("")
            lines.append(f"[{header}.variables.{_key(vname)}]")
            for fk in _VAR_FIELDS:
                if fk in entry:
                    lines.append(f"{fk} = {_val(entry[fk])}")
        remembered = r.get("remembered") or {}
        if remembered:
            lines.append("")
            lines.append(f"[{header}.remembered]")
            for rk, rv in remembered.items():
                lines.append(f"{_key(rk)} = {_val(rv)}")
        # MRU history per env, so the interactive pickers can suggest.
        for env in sorted(r.get("recent") or {}):
            entry = (r["recent"] or {}).get(env) or {}
            rows = [(k, entry[k]) for k in ("branches", "pipelines") if entry.get(k)]
            if not rows:
                continue
            lines.append("")
            lines.append(f"[{header}.recent.{_key(env)}]")
            for k, v in rows:
                lines.append(f"{k} = {_val(v)}")
        lr = r.get("last_run") or {}
        if lr:
            lines.append("")
            lines.append(f"[{header}.last_run]")
            for k in ("pipeline", "branch", "env"):
                if k in lr:
                    lines.append(f"{k} = {_val(lr[k])}")
            if lr.get("vars"):
                lines.append(f"[{header}.last_run.vars]")
                for vk, vv in lr["vars"].items():
                    lines.append(f"{_key(vk)} = {_val(vv)}")
    return "\n".join(lines) + "\n"


# --- accessors / mutators ----------------------------------------------------

def get_repo(cfg: dict, key: str) -> dict:
    return (cfg.get("repos") or {}).get(key, {})


def ensure_repo(cfg: dict, key: str) -> dict:
    return cfg.setdefault("repos", {}).setdefault(key, {})


def repo_variables(cfg: dict, key: str) -> dict:
    """The per-repo `[variables]` schema table ({} if none)."""
    return get_repo(cfg, key).get("variables") or {}


def get_remembered(cfg: dict, key: str) -> dict:
    """Tool-persisted remembered values, {varname: [values]}."""
    return get_repo(cfg, key).get("remembered") or {}


def remember_value(cfg: dict, key: str, varname: str, value: str):
    """Persist an entered value for a `remember = true` variable."""
    if not value:
        return
    remembered = ensure_repo(cfg, key).setdefault("remembered", {})
    values = remembered.setdefault(varname, [])
    if value not in values:
        values.append(value)


def _push_mru(values: list, value: str, limit: int = _RECENT_LIMIT) -> list:
    """Move `value` to the front of a most-recently-used list, capped at `limit`.
    Unlike remember_value's append-only ordering, a re-used value climbs back to
    the top — that's what makes it worth pre-selecting."""
    out = [v for v in values if v != value]
    out.insert(0, value)
    return out[:limit]


def get_recent(cfg: dict, key: str, env: str) -> dict:
    """Recently executed branches/pipelines for one env, most recent first.
    Always returns both keys (empty lists when nothing is recorded)."""
    entry = ((get_repo(cfg, key).get("recent") or {}).get(env)) or {}
    return {
        "branches": list(entry.get("branches") or []),
        "pipelines": list(entry.get("pipelines") or []),
    }


def record_recent(cfg: dict, key: str, env: str, pipeline=None, branch=None):
    """Push an executed pipeline/branch onto that env's MRU lists."""
    if not env or not (pipeline or branch):
        return
    entry = ensure_repo(cfg, key).setdefault("recent", {}).setdefault(env, {})
    for field, value in (("pipelines", pipeline), ("branches", branch)):
        if value:
            entry[field] = _push_mru(list(entry.get(field) or []), value)


def get_last_run(cfg: dict, key: str) -> dict:
    """The single-slot last run for a repo ({} if none) — what `rerun` repeats
    and what the interactive pipeline picker puts its cursor on."""
    return get_repo(cfg, key).get("last_run") or {}


def set_last_run(cfg: dict, key: str, pipeline, branch, env, variables: dict):
    ensure_repo(cfg, key)["last_run"] = {
        "pipeline": pipeline,
        "branch": branch,
        "env": env,
        "vars": dict(variables),
    }
