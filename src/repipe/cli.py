"""argparse wiring + command handlers + main()."""

import argparse
import json
import os
import re
import sys
import time

from . import __version__
from .errors import (
    RepipeError,
    EXIT_OK,
    EXIT_CONFIG,
    EXIT_FAILED_NOMATCH,
    EXIT_RETRIES,
    EXIT_TIMEOUT,
)
from . import config
from . import interactive
from .gitutil import detect_repo, branch_candidates, run_git
from .http import get_auth, download_bytes, download_text
from .model import RunState
from .output import fmt_var, state_symbol
from .providers import choose_provider
from .retry import build_patterns, first_match, SUGGESTED_RETRY_PATTERNS
from .varschema import resolve_variables, allowed_values_for


def _parse_vars(pairs) -> dict:
    """Parse repeated --var KEY=VALUE into a dict (later wins)."""
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise RepipeError(
                f"--var must be KEY=VALUE, got '{item}'.", EXIT_CONFIG
            )
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise RepipeError(f"--var has an empty key: '{item}'.", EXIT_CONFIG)
        out[key] = value
    return out


def _autofill_value(entry, cfg, path):
    """A value for a variable from a known auto-source, or None. Currently the
    only source is `autofill = "git_email"` (config user_email / git email)."""
    if entry.get("autofill") == "git_email":
        return cfg.get("user_email") or run_git(["config", "user.email"], path)
    return None


def _apply_autofill(schema, provided, cfg, path):
    """Fill any not-yet-provided variable that declares an autofill source."""
    for vname, entry in schema.items():
        if vname not in provided:
            value = _autofill_value(entry, cfg, path)
            if value:
                provided[vname] = value


def _find_target(targets, name):
    for t in targets:
        if t.name == name:
            return t
    available = ", ".join(t.name for t in targets) or "(none)"
    raise RepipeError(
        f"pipeline '{name}' not found. Available: {available}", EXIT_CONFIG
    )


def cmd_list(args) -> int:
    host, workspace, repo, branch = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    targets = provider.parse_targets(args.path)

    print(f"repo:     {workspace}/{repo}")
    print(f"provider: {provider.NAME}  (host {host})")
    if branch:
        print(f"branch:   {branch}")
    if not targets:
        print(f"\nno custom {provider.TARGET_WORD}s found.")
        return EXIT_OK

    print(f"\n{provider.TARGET_WORD}s ({len(targets)}):\n")
    width = max(len(t.name) for t in targets)
    for t in targets:
        vars_str = ", ".join(fmt_var(v) for v in t.variables) if t.variables else "—"
        print(f"  {t.name.ljust(width)}  [{t.env:<4}]  vars: {vars_str}")
    return EXIT_OK


def cmd_status(args) -> int:
    host, workspace, repo, _ = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    auth = get_auth(required=True)
    run = provider.get_run(args.id, auth)

    print(f"{state_symbol(run.state)} {run.state}  (build #{run.number})")
    native = run.native_state or "?"
    if run.native_result:
        native += f" / {run.native_result}"
    print(f"  native:   {native}")
    if run.pipeline:
        print(f"  pipeline: {run.pipeline}")
    if run.ref:
        print(f"  ref:      {run.ref}")
    if run.web_url:
        print(f"  url:      {run.web_url}")
    return EXIT_OK


def cmd_logs(args) -> int:
    host, workspace, repo, _ = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    auth = get_auth(required=True)
    run = provider.get_run(args.id, auth)
    steps = provider.get_steps(run, auth)

    if not steps:
        print("(no steps found for this run)")
        return EXIT_OK

    only_failed = any(s.state == RunState.FAILED for s in steps) and not args.all
    shown = [s for s in steps if s.state == RunState.FAILED] if only_failed else steps
    if only_failed:
        print(f"# showing {len(shown)} failed step(s); pass --all for every step\n")

    for s in shown:
        header = f"── {s.name} [{s.native_result or s.state}] "
        print(header + "─" * max(0, 60 - len(header)))
        log = provider.get_step_log(run, s, auth)
        print(log.rstrip() if log.strip() else "(no log available)")
        print()
    return EXIT_OK


def cmd_run(args) -> int:
    host, workspace, repo, branch = detect_repo(args.path)
    if args.repo:
        if "/" not in args.repo:
            raise RepipeError("--repo must be <workspace>/<repo>.", EXIT_CONFIG)
        workspace, repo = args.repo.split("/", 1)
    provider = choose_provider(host, args.provider)(workspace, repo)

    targets = provider.parse_targets(args.path)
    target = _find_target(targets, args.pipeline)

    ref = args.branch or branch
    if not ref:
        raise RepipeError(
            "no branch to run against — pass -b/--branch (couldn't detect a "
            "current branch).",
            EXIT_CONFIG,
        )

    cfg = config.load()
    schema = config.repo_variables(cfg, f"{workspace}/{repo}")
    provided = _parse_vars(args.var)
    _apply_autofill(schema, provided, cfg, args.path)
    variables = resolve_variables(target, provided, schema)  # fail-fast validation

    return _finish_run(provider, target, ref, variables, args)


def _prod_gate(target, args):
    """Prod triggers require explicit confirmation to start at all."""
    if target.env != "prod" or args.dry_run:
        return
    if getattr(args, "yes", False):
        print(f"⚠ '{target.name}' is a PRODUCTION pipeline — proceeding (--yes).")
        return
    try:
        ans = input(
            f"⚠ '{target.name}' is a PRODUCTION pipeline. "
            f"Type the pipeline name to confirm: "
        ).strip()
    except EOFError:
        raise RepipeError(
            "prod requires confirmation — pass --yes to confirm non-interactively.",
            EXIT_CONFIG,
        )
    if ans != target.name:
        raise RepipeError("prod confirmation did not match — aborted.", EXIT_CONFIG)


def _finish_run(provider, target, ref, variables, args, confirmed=False) -> int:
    """Shared trigger path: dry-run, prod gate, conservative prod retry,
    trigger, then watch/retry. Used by `run`, the interactive flow, and `rerun`.
    `confirmed=True` skips the typed-name prod gate (caller already confirmed).
    """
    method, url, body = provider.trigger_request(target, ref, variables)

    if args.dry_run:
        print("DRY RUN — no request sent\n")
        print(f"{method} {url}")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print(f"\n({provider.TARGET_WORD} '{target.name}' [{target.env}] on branch '{ref}')")
        return EXIT_OK

    if not confirmed:
        _prod_gate(target, args)

    # Conservative prod policy: no auto-retry unless explicitly --force'd
    # (there is no shipped "safe" pattern set to fall back on).
    if target.env == "prod" and not getattr(args, "force", False):
        if args.retry_on or args.max_retries:
            print("  (prod: auto-retry disabled; use --force to retry on your configured patterns)")
        args.retry_on = None
        args.max_retries = 0

    auth = get_auth(required=True)
    run = provider.trigger(target, ref, variables, auth)
    _announce(target.name, ref, run)

    if args.no_wait:
        print("\n(--no-wait: triggered only, not watching)")
        return EXIT_OK

    return _watch_and_retry(provider, target, ref, variables, auth, run, args)


def _announce(pipeline, ref, run):
    print(interactive.cyan("➜") + f" triggered {interactive.bold(pipeline)} "
          f"on {interactive.bold(ref)}")
    if run.number is not None:
        print(f"  build:  #{run.number}")
    print(f"  state:  {run.native_state or run.state}")
    if run.web_url:
        print(f"  url:    {run.web_url}")


def _print_failure(failed, logs, tail_lines=30):
    """Print failed step names + a tail of their logs (fallback: names only)."""
    names = ", ".join(s.name for s in failed) or "(unknown)"
    print(f"  failed step(s): {names}")
    body = "\n".join(text for _, text in logs).strip()
    if not body:
        print("  (no log body available — reporting step result only)")
        return
    lines = body.splitlines()
    if len(lines) > tail_lines:
        print(f"  --- last {tail_lines} log lines ---")
        lines = lines[-tail_lines:]
    else:
        print("  --- log ---")
    for ln in lines:
        print(f"  | {ln}")


def _watch_and_retry(provider, target, ref, variables, auth, run, args) -> int:
    patterns = build_patterns(args.retry_on)
    terminal = {RunState.SUCCESS, RunState.FAILED, RunState.HALTED}
    deadline = time.monotonic() + args.timeout
    attempt = 0

    print("\n" + interactive.dim(
        f"watching · poll {args.poll_interval}s · timeout {args.timeout}s "
        f"· max-retries {args.max_retries}"))
    start = time.monotonic()
    frame = 0

    while True:
        # Poll the current run until it reaches a terminal state (or times out).
        last = None
        while run.state not in terminal:
            if time.monotonic() > deadline:
                interactive.clear_line()
                print(interactive.yellow(
                    f"⌛ timed out after {args.timeout}s "
                    f"(last {run.native_state or run.state})") + f" — {run.web_url}")
                return EXIT_TIMEOUT

            # Wait one poll interval, animating a spinner in place on a TTY.
            wait_until = time.monotonic() + args.poll_interval
            if interactive.live():
                while time.monotonic() < wait_until and time.monotonic() <= deadline:
                    el = int(time.monotonic() - start)
                    sp = interactive.SPINNER[frame % len(interactive.SPINNER)]
                    frame += 1
                    sys.stdout.write(
                        f"\r\x1b[K  {interactive.cyan(sp)}  #{run.number}  "
                        f"{run.native_state or run.state}  "
                        + interactive.dim(f"{el // 60:02d}:{el % 60:02d}")
                    )
                    sys.stdout.flush()
                    time.sleep(0.1)
            else:
                time.sleep(args.poll_interval)

            run = provider.get_run(run.id, auth)
            if run.native_state != last:
                interactive.clear_line()
                print(f"  {interactive.dim('…')} {run.native_state or run.state}")
                last = run.native_state

        interactive.clear_line()
        if run.state == RunState.SUCCESS:
            print(interactive.green(f"✓ #{run.number} SUCCESSFUL") + f" — {run.web_url}")
            return EXIT_OK

        if run.state == RunState.HALTED:
            print(interactive.yellow(f"‖ #{run.number} paused at a manual gate")
                  + " (build+push done). Approve the deploy:\n  " + (run.web_url or ""))
            return EXIT_OK

        # FAILED — decide whether to retry.
        failed, logs = provider.failed_step_logs(run, auth)
        combined = "\n".join(text for _, text in logs)
        hit = first_match(combined, patterns, args.match)

        if hit is None:
            print(interactive.red(f"✗ #{run.number} FAILED") + " — not retrying.")
            if not patterns:
                print(interactive.dim("  no retry patterns configured. Add them to "
                                      "config `retry_on` or pass --retry-on."))
                print(interactive.dim("  see `repipe suggestions` for a starter list."))
            else:
                print(interactive.dim(f"  no configured pattern matched "
                                      f"({len(patterns)} checked, mode={args.match})."))
            _print_failure(failed, logs)
            print(f"  {run.web_url}")
            return EXIT_FAILED_NOMATCH

        if attempt >= args.max_retries:
            print(interactive.red(f"✗ #{run.number} FAILED")
                  + f" — matched '{hit}' but retries exhausted ({args.max_retries}).")
            _print_failure(failed, logs)
            print(f"  {run.web_url}")
            return EXIT_RETRIES

        attempt += 1
        print(interactive.yellow(f"↻ #{run.number} FAILED")
              + f" — matched '{hit}', re-triggering (retry {attempt}/{args.max_retries}) …")
        run = provider.trigger(target, ref, variables, auth)
        _announce(target.name, ref, run)


def _run_args_namespace(**overrides):
    """A Namespace with every field _finish_run/_watch_and_retry expects,
    for the interactive flow and rerun (which don't come from the run parser)."""
    d = dict(
        dry_run=False, yes=False, no_wait=False, force=False,
        retry_on=None, match="substring",
        max_retries=2, poll_interval=20, timeout=3600,
    )
    d.update(overrides)
    return argparse.Namespace(**d)


def _ask_var(var, provided, entry, remembered, step):
    """Prompt for a single pipeline variable; returns the value or interactive.BACK.
    Uses any prior answer (from going back) as the default. All behavior is
    driven by the per-variable config `entry` — no hardcoded variable names."""
    name = var.name
    prior = provided.get(name)
    default = entry.get("default") if entry.get("default") is not None else var.default
    allowed = allowed_values_for(var, entry)
    if allowed:
        cur = prior if prior in allowed else default
        di = allowed.index(cur) if cur in allowed else 0
        return interactive.pick(name, allowed, default_idx=di, step=step)
    if entry.get("hint"):
        print(interactive.dim("  " + entry["hint"]))
    if entry.get("remember") and remembered.get(name):
        print(interactive.dim("  recent: " + ", ".join(remembered[name][-5:])))
    return interactive.ask(name, default=prior or default, step=step)


def cmd_interactive(args) -> int:
    host, workspace, repo, branch = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    repo_key = f"{workspace}/{repo}"
    cfg = config.load()
    rcfg = config.get_repo(cfg, repo_key)
    schema = config.repo_variables(cfg, repo_key)
    remembered = config.get_remembered(cfg, repo_key)

    targets = provider.parse_targets(args.path)
    if not targets:
        raise RepipeError(f"no {provider.TARGET_WORD}s found in this repo.", EXIT_CONFIG)

    interactive.banner(repo_key, provider.NAME, __version__)

    st = {"provided": {}, "new_email": None, "vars_for": None, "total": 4}

    def total():
        return st["total"]

    def autofill_value(var):
        return _autofill_value(schema.get(var.name, {}), cfg, args.path)

    def promptable(target):
        # Skip any variable that can be auto-filled from a known source.
        return [v for v in target.variables if not autofill_value(v)]

    def step_pipeline():
        di = targets.index(st["target"]) if st.get("target") in targets else 0
        chosen = interactive.pick(
            "Pipeline", targets, default_idx=di, allow_back=False, step=(1, total()),
            to_str=lambda t: f"{t.name}  {interactive.env_badge(t.env)}",
        )
        if chosen is interactive.BACK:
            return "back"
        st["target"] = chosen
        st.setdefault("env", chosen.env)
        st["total"] = 5 if promptable(chosen) else 4
        return "next"

    def step_env():
        opts = ["qa", "prod"]
        cur = st.get("env", st["target"].env)
        chosen = interactive.pick("Environment", opts, default_idx=opts.index(cur),
                                  to_str=interactive.env_badge, step=(2, total()))
        if chosen is interactive.BACK:
            return "back"
        st["env"] = chosen
        st["target"].env = chosen
        return "next"

    def step_branch():
        prefix = rcfg.get(f"{st['env']}_branch_prefix") or f"{st['env']}-release"
        cands = branch_candidates(args.path, branch, prefix)
        options = cands + ["enter manually…"]
        di = options.index(st["ref"]) if st.get("ref") in options else 0
        chosen = interactive.pick("Branch", options, default_idx=di, step=(3, total()))
        if chosen is interactive.BACK:
            return "back"
        if chosen == "enter manually…":
            typed = interactive.ask("Branch name", default=st.get("ref") or branch,
                                    step=(3, total()))
            if typed is interactive.BACK:
                return "back"
            st["ref"] = typed
        else:
            st["ref"] = chosen
        if not st["ref"]:
            raise RepipeError("no branch selected.", EXIT_CONFIG)
        return "next"

    def step_vars():
        target = st["target"]
        provided = st["provided"]
        if st["vars_for"] != target.name:      # pipeline changed → clear stale answers
            provided.clear()
            st["vars_for"] = target.name
        # auto-fill values from known sources (not prompted steps)
        for var in target.variables:
            if var.name in provided:
                continue
            filled = autofill_value(var)
            if filled:
                provided[var.name] = filled
                if schema.get(var.name, {}).get("autofill") == "git_email" \
                        and not cfg.get("user_email"):
                    st["new_email"] = filled
        prompts = promptable(target)
        j = 0
        while j < len(prompts):
            entry = schema.get(prompts[j].name, {})
            res = _ask_var(prompts[j], provided, entry, remembered, step=(4, total()))
            if res is interactive.BACK:
                if j == 0:
                    return "back"
                j -= 1
                continue
            provided[prompts[j].name] = res
            j += 1
        st["variables"] = resolve_variables(target, provided, schema)
        return "next"

    def step_confirm():
        target, ref, variables = st["target"], st["ref"], st["variables"]
        rows = [("pipeline", target.name),
                ("env", interactive.env_badge(target.env)),
                ("branch", ref)] + [(k, v) for k, v in variables]
        kw = max(len(k) for k, _ in rows)
        print("\n" + interactive.bold("Summary"))
        for k, v in rows:
            print(f"  {interactive.dim(k.ljust(kw))}   {v}")
        if args.dry_run or args.yes:
            return "next"
        choice = interactive.pick("Confirm", ["Trigger it", "Go back", "Cancel"],
                                  step=(total(), total()))
        if choice is interactive.BACK or choice == "Go back":
            return "back"
        if choice == "Cancel":
            return "abort"
        return "next"

    steps = [step_pipeline, step_env, step_branch, step_vars, step_confirm]
    i = 0
    while i < len(steps):
        result = steps[i]()
        if result == "back":
            i = max(0, i - 1)
        elif result == "abort":
            print("aborted.")
            return EXIT_OK
        else:
            i += 1

    target, ref, variables = st["target"], st["ref"], st["variables"]
    rargs = _run_args_namespace(
        dry_run=args.dry_run, yes=args.yes,
        match=cfg.get("match", "substring"),
        max_retries=cfg.get("max_retries", 2),
        retry_on=cfg.get("retry_on"),
    )
    code = _finish_run(provider, target, ref, variables, rargs,
                       confirmed=(target.env != "prod"))

    if not args.dry_run:
        if st["new_email"]:
            cfg["user_email"] = st["new_email"]
        vmap = dict(variables)
        for vname, value in vmap.items():
            if schema.get(vname, {}).get("remember"):
                config.remember_value(cfg, repo_key, vname, value)
        config.set_last_run(cfg, repo_key, target.name, ref, target.env, vmap)
        config.save(cfg)
    return code


def cmd_init(args) -> int:
    host, workspace, repo, _ = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    targets = provider.parse_targets(args.path)

    cfg = config.load()
    key = f"{workspace}/{repo}"
    r = config.ensure_repo(cfg, key)
    r.setdefault("provider", provider.NAME)
    r.setdefault("qa_branch_prefix", "qa-release")
    r.setdefault("prod_branch_prefix", "prod-release")
    if not cfg.get("user_email"):
        em = run_git(["config", "user.email"], args.path)
        if em:
            cfg["user_email"] = em

    # Scaffold a variable-schema stub for each discovered variable, seeding
    # `enum` from any yml-declared allowed-values. Users fill in the rest
    # (default/required/pattern/autofill/remember/no_spaces_unless).
    vars_seen = {}
    for t in targets:
        for v in t.variables:
            vars_seen.setdefault(v.name, list(v.allowed_values or []))
    if vars_seen:
        vtable = r.setdefault("variables", {})
        for vname, allowed in vars_seen.items():
            entry = vtable.setdefault(vname, {})
            if allowed and "enum" not in entry:
                entry["enum"] = allowed

    path = config.save(cfg)
    print(f"wrote {path}")
    print(f"  repo:      {key}")
    print(f"  provider:  {provider.NAME}")
    print(f"  pipelines: {len(targets)}")
    if vars_seen:
        print(f"  variables: {', '.join(sorted(vars_seen))}")
    print("\nEdit that file to set your defaults (branch prefixes, max_retries)")
    print("and to constrain inputs under [repos.\"" + key + "\".variables.NAME]")
    print("(enum / default / required / pattern / autofill / remember).")
    print("repipe does NOT retry by default — add your own `retry_on` patterns.")
    print("Run `repipe suggestions` for a starter list you can copy.")
    return EXIT_OK


# Where `repipe upgrade` fetches from. Overridable so forks can self-update.
UPGRADE_REPO = os.environ.get("REPIPE_UPGRADE_REPO", "AbhayG21/repipe")
UPGRADE_REF = os.environ.get("REPIPE_UPGRADE_REF", "main")


def _version_tuple(v):
    parts = []
    for p in str(v).split("."):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple(parts)


def _latest_version(raw_base):
    """Read __version__ from the published source, or None if unavailable."""
    try:
        txt = download_text(f"{raw_base}/src/repipe/__init__.py")
    except RepipeError:
        return None
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', txt)
    return m.group(1) if m else None


def _self_path():
    """Absolute path of the currently-running repipe executable."""
    import shutil
    cand = sys.argv[0]
    if os.sep in cand or (os.altsep and os.altsep in cand):
        return os.path.realpath(cand)
    found = shutil.which(cand) or shutil.which("repipe")
    return os.path.realpath(found) if found else os.path.realpath(cand)


def cmd_upgrade(args) -> int:
    import subprocess
    import tempfile

    raw = os.environ.get("REPIPE_UPGRADE_BASE") or \
        f"https://raw.githubusercontent.com/{UPGRADE_REPO}/{UPGRADE_REF}"
    latest = _latest_version(raw)
    current = __version__
    behind = bool(latest) and _version_tuple(latest) > _version_tuple(current)

    if latest:
        status = "update available" if behind else "up to date"
        print(f"installed: {current}   latest ({UPGRADE_REF}): {latest}   → {status}")
    else:
        print(f"installed: {current}   (could not read latest version from {UPGRADE_REF})")

    if args.check:
        return EXIT_OK
    if latest and not behind and not args.force:
        print("nothing to do (use --force to reinstall anyway).")
        return EXIT_OK

    target = _self_path()
    if not os.path.isfile(target):
        raise RepipeError(
            f"can't locate the installed repipe binary ({target}). "
            "Re-run the install one-liner instead.",
            EXIT_CONFIG,
        )

    print(f"downloading {raw}/repipe …")
    data = download_bytes(f"{raw}/repipe")

    # Write next to the target (same filesystem → atomic replace), verify, swap.
    fd, tmp = tempfile.mkstemp(prefix=".repipe-upgrade-", dir=os.path.dirname(target) or ".")
    try:
        os.write(fd, data)
        os.close(fd)
        os.chmod(tmp, 0o755)
        check = subprocess.run([sys.executable, tmp, "version"],
                               capture_output=True, text=True)
        if check.returncode != 0:
            raise RepipeError(
                "downloaded build failed to run; keeping your current version.",
                EXIT_CONFIG,
            )
        os.replace(tmp, target)
    except PermissionError:
        raise RepipeError(f"cannot write {target} (permission denied).", EXIT_CONFIG)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    newver = check.stdout.strip().split()[-1] if check.stdout.strip() else "?"
    print(f"✓ upgraded {current} → {newver}   ({target})")
    return EXIT_OK


def cmd_suggestions(args) -> int:
    print("repipe applies NO retry patterns by default — you choose them.\n")
    print("Suggested patterns (common transient/infra errors):")
    for p in SUGGESTED_RETRY_PATTERNS:
        print(f"  {p}")
    print("\nCopy the ones you want into ~/.config/repipe/config.toml:\n")
    print("retry_on = [")
    for p in SUGGESTED_RETRY_PATTERNS:
        print(f'  "{p}",')
    print("]")
    print("\n…or per run:  repipe run … --retry-on \"pattern\" [--retry-on …]")
    print("Matching is case-insensitive substring by default (--match regex for regex).")
    return EXIT_OK


def cmd_rerun(args) -> int:
    host, workspace, repo, branch = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    key = f"{workspace}/{repo}"
    cfg = config.load()
    lr = config.get_repo(cfg, key).get("last_run")
    if not lr:
        raise RepipeError(
            f"no last run recorded for {key} — run `repipe` first.", EXIT_CONFIG
        )
    targets = provider.parse_targets(args.path)
    target = _find_target(targets, lr["pipeline"])
    target.env = lr.get("env", target.env)
    ref = lr.get("branch") or branch
    schema = config.repo_variables(cfg, key)
    variables = resolve_variables(target, dict(lr.get("vars") or {}), schema)
    print(f"rerun: {target.name} [{target.env}] on '{ref}'")
    rargs = _run_args_namespace(
        dry_run=args.dry_run, yes=args.yes,
        match=cfg.get("match", "substring"),
        max_retries=cfg.get("max_retries", 2),
        retry_on=cfg.get("retry_on"),
    )
    return _finish_run(provider, target, ref, variables, rargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repipe",
        description="Trigger a CI pipeline and auto-retry it when it fails with "
        "a transient error. Bitbucket Cloud first; provider-agnostic core.",
        epilog="Run `repipe <command> --help` for command-specific help.",
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"repipe {__version__}")
    # Top-level options usable by the bare interactive flow (`repipe [--dry-run]`).
    parser.add_argument("--path", default=".", help=argparse.SUPPRESS)
    parser.add_argument("--provider", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true",
                        help="bare `repipe`: preview the interactive run, no API call")
    parser.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--path", default=".",
                        help="repo working tree to read (default: current dir)")
    common.add_argument("--provider", default=None,
                        help="override provider detection (e.g. bitbucket)")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("version", help="print the repipe version")
    sub.add_parser("list", parents=[common],
                   help="list runnable pipelines from the repo")

    p_status = sub.add_parser("status", parents=[common],
                              help="show the state of a run")
    p_status.add_argument("id", help="pipeline uuid or build number")

    p_logs = sub.add_parser("logs", parents=[common],
                            help="print step logs of a run")
    p_logs.add_argument("id", help="pipeline uuid or build number")
    p_logs.add_argument("--all", action="store_true",
                        help="show all steps, not just failed ones")

    p_run = sub.add_parser("run", parents=[common],
                           help="trigger a pipeline, then watch and auto-retry")
    p_run.add_argument("-p", "--pipeline", required=True,
                       help="pipeline name (see `repipe list`)")
    p_run.add_argument("-b", "--branch", default=None,
                       help="branch ref (default: current branch)")
    p_run.add_argument("-r", "--repo", default=None,
                       help="override detected repo as <workspace>/<repo>")
    p_run.add_argument("--var", action="append", metavar="KEY=VALUE",
                       help="set a pipeline variable (repeatable)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="print the exact request without calling the API")
    p_run.add_argument("--yes", "--non-interactive", dest="yes",
                       action="store_true", help="disable prompts (CI/scripting)")
    p_run.add_argument("--no-wait", action="store_true",
                       help="trigger only; don't poll or retry")
    p_run.add_argument("--retry-on", action="append", metavar="PATTERN",
                       help="extra retry pattern (repeatable; appends to built-ins)")
    p_run.add_argument("--match", choices=["substring", "regex"], default="substring",
                       help="how retry patterns match (default: substring)")
    p_run.add_argument("--max-retries", type=int, default=2,
                       help="max re-triggers on a matching failure (default: 2)")
    p_run.add_argument("--poll-interval", type=int, default=20,
                       help="seconds between status polls (default: 20)")
    p_run.add_argument("--timeout", type=int, default=3600,
                       help="give up watching after N seconds (default: 3600)")
    p_run.add_argument("--force", action="store_true",
                       help="override conservative prod retry policy")

    sub.add_parser("init", parents=[common],
                   help="scaffold ~/.config/repipe/config.toml from this repo")
    sub.add_parser("suggestions",
                   help="print suggested retry patterns to copy into config")

    p_up = sub.add_parser("upgrade", help="update repipe to the latest published build")
    p_up.add_argument("--check", action="store_true",
                      help="only report whether an update is available")
    p_up.add_argument("--force", action="store_true",
                      help="reinstall even if already up to date")

    p_rerun = sub.add_parser("rerun", parents=[common],
                             help="repeat the last invocation for this repo")
    p_rerun.add_argument("--dry-run", action="store_true",
                         help="preview without calling the API")
    p_rerun.add_argument("--yes", action="store_true",
                         help="disable prompts / confirm prod")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command is None:
            return cmd_interactive(args)
        if args.command == "version":
            print(f"repipe {__version__}")
            return EXIT_OK
        if args.command == "list":
            return cmd_list(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "logs":
            return cmd_logs(args)
        if args.command == "run":
            return cmd_run(args)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "suggestions":
            return cmd_suggestions(args)
        if args.command == "upgrade":
            return cmd_upgrade(args)
        if args.command == "rerun":
            return cmd_rerun(args)
    except RepipeError as e:
        print(f"repipe: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("\naborted.", file=sys.stderr)
        return EXIT_CONFIG

    parser.print_help()
    return EXIT_OK
