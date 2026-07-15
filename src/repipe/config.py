"""Config + persisted state at ~/.config/repipe/config.toml (non-secret only).

Read with stdlib tomllib (3.11+). stdlib has no TOML *writer*, so we emit our
own for the small, known schema below — repos keyed by "<ws>/<repo>", plus
persisted state (remembered FLAVOURS, last_run) that `rerun` reads back.
"""

import os
import re

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
    lines = []
    for k in ("user_email", "match", "max_retries"):
        if k in cfg:
            lines.append(f"{k} = {_val(cfg[k])}")
    if "retry_on" in cfg:
        lines.append(f"retry_on = {_val(cfg['retry_on'])}")

    for key, r in (cfg.get("repos") or {}).items():
        header = f"repos.{_key(key)}"
        lines.append("")
        lines.append(f"[{header}]")
        for k in ("provider", "default_project", "qa_branch_prefix", "prod_branch_prefix"):
            if k in r:
                lines.append(f"{k} = {_val(r[k])}")
        if r.get("flavours"):
            lines.append(f"flavours = {_val(r['flavours'])}")
        lr = r.get("last_run") or {}
        if lr:
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


def remember_flavour(cfg: dict, key: str, value: str):
    if not value:
        return
    flavours = ensure_repo(cfg, key).setdefault("flavours", [])
    if value not in flavours:
        flavours.append(value)


def set_last_run(cfg: dict, key: str, pipeline, branch, env, variables: dict):
    ensure_repo(cfg, key)["last_run"] = {
        "pipeline": pipeline,
        "branch": branch,
        "env": env,
        "vars": dict(variables),
    }
