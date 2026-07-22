"""argparse wiring + command handlers + main()."""

import argparse
import getpass
import json
import os
import secrets
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
from . import http
from . import interactive
from . import notify as notify_mod
from .gitutil import detect_repo, branch_candidates, run_git
from .http import (
    get_auth, download_bytes, download_text, credentials_path, save_credentials,
)
from .model import RunState
from .output import fmt_var, state_symbol
from .providers import choose_provider, PROVIDERS_BY_NAME
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

    # Resolve notifications: explicit --notify/--no-notify wins, else config,
    # else default on (the TTY gate keeps "on" quiet in CI/piped runs).
    if args.notify is None:
        args.notify = cfg.get("notify", True)
    args.notify_steps = args.notify_steps or bool(cfg.get("notify_steps", False))
    # Phone push (independent of the TTY-gated local channel above).
    if getattr(args, "phone_notify", None) is None:
        args.phone_notify = True
    args.push_cfg = _push_cfg_from(cfg)

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


# Don't ping for near-instant runs — you didn't have time to look away.
NOTIFY_MIN_ELAPSED = 30
_STEP_TERMINAL = {RunState.SUCCESS, RunState.FAILED}


def _should_notify(args) -> bool:
    """Local desktop notifications fire only when enabled (default on) AND stdout
    is a live terminal — that TTY gate is what auto-suppresses CI / piped runs."""
    return getattr(args, "notify", True) and interactive.live()


def _push_cfg_from(cfg) -> dict:
    """Snapshot every push provider's configured URL from `cfg` into a
    {config_key: url} dict for the dispatch loop to consume. Driven by the
    registry so a new provider needs no change here."""
    return {p["config_key"]: (cfg.get(p["config_key"]) or "")
            for p in notify_mod.PUSH_PROVIDERS}


def _phone_push_summary(cfg) -> str:
    """One-line status for the config menu: which push providers are configured."""
    on = [p["label"] for p in notify_mod.PUSH_PROVIDERS if cfg.get(p["config_key"])]
    return ", ".join(on) if on else "off"


def _enabled_push_providers(args):
    """The push providers with a URL configured on `args`, unless phone push is
    disabled. Deliberately NOT gated on a TTY: phone push is the channel for a
    headless box you've walked away from, where the whole point is to reach your
    phone precisely because there's no terminal watching."""
    if not getattr(args, "phone_notify", True):
        return []
    push_cfg = getattr(args, "push_cfg", None) or {}
    return [p for p in notify_mod.PUSH_PROVIDERS if push_cfg.get(p["config_key"])]


def _notify_token():
    """Optional Bearer token for reserved / self-hosted / protected ntfy topics:
    env REPIPE_NOTIFY_TOKEN first, then the credentials file. None for the common
    public-topic case (no auth)."""
    v = os.environ.get("REPIPE_NOTIFY_TOKEN")
    if v:
        return v
    try:
        return http._load_credentials_file().get("REPIPE_NOTIFY_TOKEN")
    except Exception:
        return None


def _login_email():
    """The email repipe authenticates with (Bitbucket Basic auth): env
    REPIPE_EMAIL first, then the credentials file. Distinct from config's
    `user_email` (autofill/display) — this is the credential half of the pair."""
    v = os.environ.get("REPIPE_EMAIL")
    if v:
        return v
    try:
        return http._load_credentials_file().get("REPIPE_EMAIL")
    except Exception:
        return None


def _notify_result(target, run, outcome, elapsed, args, note=""):
    """Ping for a whole-run event across both channels. Final results play a sound
    (local) / normal-or-high priority (phone); retries are silent / low-priority.
    Suppressed for runs shorter than NOTIFY_MIN_ELAPSED. The two channels are gated
    independently — local needs a TTY, phone needs a notify_url — so a headless VM
    stays quiet locally while still buzzing your phone."""
    if elapsed < NOTIFY_MIN_ELAPSED:
        return
    local_on = _should_notify(args)
    providers = _enabled_push_providers(args)
    if not local_on and not providers:
        return
    title = f"repipe · {target.name}"
    n = f"#{run.number} " if run.number is not None else ""
    # outcome -> (message, local sound, ntfy priority, ntfy emoji tag)
    table = {
        "success": (f"✓ {n}succeeded", True, "default", "white_check_mark"),
        "halted": (f"‖ {n}paused at a manual gate", True, "default", "double_vertical_bar"),
        "failed": (f"✗ {n}failed", True, "high", "x"),
        "timeout": (f"⌛ {n}timed out", True, "default", "hourglass"),
        "retry": (f"↻ {n}failed — retrying{note}", False, "low", "arrows_counterclockwise"),
    }
    msg, sound, priority, tags = table[outcome]
    if local_on:
        notify_mod.notify(title, msg, sound=sound)
    if providers:
        push_cfg, click, token = args.push_cfg, getattr(run, "web_url", "") or "", _notify_token()
        for p in providers:
            # priority/tags/token are ntfy-only; other senders accept and ignore
            # them (**kwargs), so one uniform call fans out to every provider.
            getattr(notify_mod, p["send"])(
                push_cfg[p["config_key"]], title, msg,
                priority=priority, tags=tags, click=click, token=token,
            )


def _notify_step(target, run, step, args):
    """Silent ping when a single step/job finishes (opt-in, --notify-steps).
    Bypasses the elapsed gate — fast steps are exactly the progress opted into."""
    if not _should_notify(args):
        return
    tail = f" #{run.number}" if run.number is not None else ""
    sym = "✓" if step.state == RunState.SUCCESS else "✗"
    notify_mod.notify(f"repipe · {target.name}{tail}", f"{sym} {step.name}")


def _notify_new_steps(provider, target, run, auth, prev, args) -> dict:
    """Diff the current steps against `prev` (a {(run_id, step_key): state} map),
    ping any that newly reached a terminal state, and return the fresh snapshot.
    First sighting of a step just seeds it — no ping — so attaching to an
    already-running run doesn't replay past steps. Best-effort: a failed
    get_steps leaves the snapshot untouched rather than breaking the watch."""
    try:
        steps = provider.get_steps(run, auth)
    except RepipeError:
        return prev
    snapshot = dict(prev)
    for s in steps:
        key = (run.id, s.uuid or s.name)
        was = snapshot.get(key)
        snapshot[key] = s.state
        if was is None or was in _STEP_TERMINAL:
            continue
        if s.state in _STEP_TERMINAL:
            _notify_step(target, run, s, args)
    return snapshot


def _watch_and_retry(provider, target, ref, variables, auth, run, args) -> int:
    patterns = build_patterns(args.retry_on)
    terminal = {RunState.SUCCESS, RunState.FAILED, RunState.HALTED}
    deadline = time.monotonic() + args.timeout
    attempt = 0
    step_states = {}  # (run_id, step_key) -> RunState, for --notify-steps

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
                _notify_result(target, run, "timeout",
                               time.monotonic() - start, args)
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
            if getattr(args, "notify_steps", False) and _should_notify(args):
                step_states = _notify_new_steps(
                    provider, target, run, auth, step_states, args)
            if run.native_state != last:
                interactive.clear_line()
                print(f"  {interactive.dim('…')} {run.native_state or run.state}")
                last = run.native_state

        interactive.clear_line()
        if run.state == RunState.SUCCESS:
            print(interactive.green(f"✓ #{run.number} SUCCESSFUL") + f" — {run.web_url}")
            _notify_result(target, run, "success", time.monotonic() - start, args)
            return EXIT_OK

        if run.state == RunState.HALTED:
            print(interactive.yellow(f"‖ #{run.number} paused at a manual gate")
                  + " (build+push done). Approve the deploy:\n  " + (run.web_url or ""))
            _notify_result(target, run, "halted", time.monotonic() - start, args)
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
            _notify_result(target, run, "failed", time.monotonic() - start, args)
            return EXIT_FAILED_NOMATCH

        if attempt >= args.max_retries:
            print(interactive.red(f"✗ #{run.number} FAILED")
                  + f" — matched '{hit}' but retries exhausted ({args.max_retries}).")
            _print_failure(failed, logs)
            print(f"  {run.web_url}")
            _notify_result(target, run, "failed", time.monotonic() - start, args)
            return EXIT_RETRIES

        attempt += 1
        print(interactive.yellow(f"↻ #{run.number} FAILED")
              + f" — matched '{hit}', re-triggering (retry {attempt}/{args.max_retries}) …")
        _notify_result(target, run, "retry", time.monotonic() - start, args,
                       note=f" ({attempt}/{args.max_retries})")
        run = provider.trigger(target, ref, variables, auth)
        _announce(target.name, ref, run)


def _run_args_namespace(**overrides):
    """A Namespace with every field _finish_run/_watch_and_retry expects,
    for the interactive flow and rerun (which don't come from the run parser)."""
    d = dict(
        dry_run=False, yes=False, no_wait=False, force=False,
        retry_on=None, match="substring",
        max_retries=2, poll_interval=20, timeout=3600,
        notify=True, notify_steps=False,
        push_cfg=None, phone_notify=True,
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

    # Only probe when the banner will actually render (a live TTY) — no wasted
    # network call in piped/CI invocations of bare `repipe`.
    newer = _update_available() if interactive.live() else None
    note = (f"↑ repipe {newer} is available — run `repipe upgrade`"
            if newer else None)
    interactive.banner(repo_key, provider.NAME, __version__, update_note=note)

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
        poll_interval=cfg.get("poll_interval", 20),
        timeout=cfg.get("timeout", 3600),
        notify=cfg.get("notify", True),
        notify_steps=cfg.get("notify_steps", False),
        push_cfg=_push_cfg_from(cfg),
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
# Pin to a specific release tag, e.g. REPIPE_UPGRADE_VERSION=v1.6.0
UPGRADE_VERSION = os.environ.get("REPIPE_UPGRADE_VERSION")


def _version_tuple(v):
    parts = []
    for p in str(v).split("."):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple(parts)


def _latest_release(repo, timeout=30):
    """Return (version, asset_url) for the repo's latest GitHub Release, or
    (None, None). Reads the public Releases API unauthenticated — it never sends
    the user's token (which may be a Bitbucket credential), and the API is fresh
    (no raw/branch CDN lag). `timeout` is kept short for the passive welcome-screen
    check so it can't stall the banner."""
    try:
        data = json.loads(download_text(
            f"https://api.github.com/repos/{repo}/releases/latest",
            timeout=timeout) or "{}")
    except (RepipeError, ValueError):
        return None, None
    tag = data.get("tag_name") or ""
    version = tag[1:] if tag.startswith("v") else tag
    asset_url = None
    for asset in data.get("assets") or []:
        if asset.get("name") == "repipe":
            asset_url = asset.get("browser_download_url")
            break
    return (version or None), asset_url


# Passive "update available" check shown on the welcome screen. Throttled to one
# network probe per interval via a tiny cache file, so bare `repipe` stays fast.
_UPDATE_CHECK_INTERVAL = 24 * 3600  # seconds (once a day)


def _update_cache_path():
    return os.path.join(config.config_dir(), "update-check.json")


def _read_update_cache():
    """(checked_at_epoch, cached_latest_version). (0, '') if no/unreadable cache."""
    try:
        with open(_update_cache_path(), encoding="utf-8") as f:
            d = json.load(f)
        return float(d.get("checked_at", 0)), str(d.get("latest") or "")
    except Exception:
        return 0.0, ""


def _write_update_cache(latest):
    try:
        os.makedirs(config.config_dir(), exist_ok=True)
        with open(_update_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"checked_at": time.time(), "latest": latest or ""}, f)
    except Exception:
        pass  # cache is an optimization — a failed write just means we re-check


def _update_available(now=None):
    """Best-effort: the newer version string if a published release is ahead of
    the running build, else None. Probes the Releases API at most once per
    _UPDATE_CHECK_INTERVAL (cached), never raises, and blocks only for a short
    timeout. Silent when pinned (REPIPE_UPGRADE_VERSION) or opted out
    (REPIPE_NO_UPDATE_CHECK)."""
    if os.environ.get("REPIPE_NO_UPDATE_CHECK") or UPGRADE_VERSION:
        return None
    now = time.time() if now is None else now
    checked_at, latest = _read_update_cache()
    if now - checked_at >= _UPDATE_CHECK_INTERVAL:
        fetched, _ = _latest_release(UPGRADE_REPO, timeout=3)
        if fetched:
            latest = fetched
        # Stamp the attempt either way — preserves a good cached `latest` while
        # still throttling retries when the API is unreachable.
        _write_update_cache(latest)
    if latest and _version_tuple(latest) > _version_tuple(__version__):
        return latest
    return None


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

    current = __version__

    if UPGRADE_VERSION:
        # Pinned to a specific release tag — reinstall it regardless of version.
        tag = UPGRADE_VERSION
        latest = tag[1:] if tag.startswith("v") else tag
        asset_url = f"https://github.com/{UPGRADE_REPO}/releases/download/{tag}/repipe"
        behind = True
        print(f"installed: {current}   pinned release: {latest}")
    else:
        latest, asset_url = _latest_release(UPGRADE_REPO)
        behind = bool(latest) and _version_tuple(latest) > _version_tuple(current)
        if latest:
            status = "update available" if behind else "up to date"
            print(f"installed: {current}   latest release: {latest}   → {status}")
        else:
            print(f"installed: {current}   (no published release found for {UPGRADE_REPO})")

    if args.check:
        return EXIT_OK
    if not UPGRADE_VERSION and latest and not behind and not args.force:
        print("nothing to do (use --force to reinstall anyway).")
        return EXIT_OK
    if not asset_url:
        raise RepipeError(
            f"no downloadable release asset found for {UPGRADE_REPO} — "
            "re-run the install one-liner instead.",
            EXIT_CONFIG,
        )

    target = _self_path()
    if not os.path.isfile(target):
        raise RepipeError(
            f"can't locate the installed repipe binary ({target}). "
            "Re-run the install one-liner instead.",
            EXIT_CONFIG,
        )

    print(f"downloading {asset_url} …")
    data = download_bytes(asset_url)

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
        poll_interval=cfg.get("poll_interval", 20),
        timeout=cfg.get("timeout", 3600),
        notify=cfg.get("notify", True),
        notify_steps=cfg.get("notify_steps", False),
        push_cfg=_push_cfg_from(cfg),
    )
    return _finish_run(provider, target, ref, variables, rargs)


# --- config editor -----------------------------------------------------------

# Effective defaults for the global settings the menu edits (source of truth for
# both the interactive menu and `--show`). Keys mirror what config.dumps() emits.
_CONFIG_DEFAULTS = {
    "max_retries": 2, "match": "substring",
    "poll_interval": 20, "timeout": 3600,
    "notify": True, "notify_steps": False,
}
# Each push provider contributes its (empty-by-default) URL key, so adding a
# provider to the registry needs no change here.
for _p in notify_mod.PUSH_PROVIDERS:
    _CONFIG_DEFAULTS.setdefault(_p["config_key"], "")


def _config_show(cfg) -> int:
    """Non-interactive dump of effective global settings (no TTY needed)."""
    onoff = lambda b: "on" if b else "off"
    print("repipe global config:")
    print(f"  retry patterns : {len(cfg.get('retry_on') or [])}")
    print(f"  max retries    : {cfg.get('max_retries', _CONFIG_DEFAULTS['max_retries'])}")
    print(f"  match mode     : {cfg.get('match', _CONFIG_DEFAULTS['match'])}")
    print(f"  poll interval  : {cfg.get('poll_interval', _CONFIG_DEFAULTS['poll_interval'])}s")
    print(f"  timeout        : {cfg.get('timeout', _CONFIG_DEFAULTS['timeout'])}s")
    print(f"  notifications  : {onoff(cfg.get('notify', _CONFIG_DEFAULTS['notify']))}")
    print(f"  notify per-step: {onoff(cfg.get('notify_steps', _CONFIG_DEFAULTS['notify_steps']))}")
    print(f"  phone push     : {_phone_push_summary(cfg)}")
    for p in notify_mod.PUSH_PROVIDERS:
        print(f"    {p['label']:<12} : {cfg.get(p['config_key']) or '(off)'}")
    print(f"  autofill email : {cfg.get('user_email') or '(unset)'}")
    print(f"\nfile: {config.config_path()}")
    return EXIT_OK


def _ask_int(label, current, minimum):
    """Ask for an int >= minimum; re-prompt on bad input. Blank/back keeps
    `current`. Returns the (possibly unchanged) value."""
    while True:
        raw = interactive.ask(label, default=str(current))
        if raw is interactive.BACK:
            return current
        raw = str(raw).strip()
        if not raw:
            return current
        try:
            v = int(raw)
        except ValueError:
            print(f"  enter a whole number ≥ {minimum}")
            continue
        if v < minimum:
            print(f"  must be ≥ {minimum}")
            continue
        return v


def _edit_patterns(cfg) -> bool:
    """Sub-editor for the global `retry_on` list. Returns True if it changed."""
    changed = False
    while True:
        patterns = list(cfg.get("retry_on") or [])
        if patterns:
            print(interactive.dim("current retry patterns:"))
            for i, p in enumerate(patterns, 1):
                print(f"  {i}) {p}")
        else:
            print(interactive.dim("no retry patterns configured"))
        actions = [
            ("suggest", "Add from suggestions"),
            ("custom", "Add custom…"),
            ("remove", "Remove…"),
            ("done", "Done"),
        ]
        act = interactive.pick("Retry patterns", actions,
                               to_str=lambda a: a[1], allow_back=False)[0]
        if act == "done":
            return changed
        if act == "suggest":
            avail = [p for p in SUGGESTED_RETRY_PATTERNS if p not in patterns]
            if not avail:
                print("  all suggestions already added.")
                continue
            sel = interactive.pick("Add which pattern?", avail)
            if sel is interactive.BACK:
                continue
            cfg["retry_on"] = patterns + [sel]
            changed = True
        elif act == "custom":
            raw = interactive.ask("New pattern")
            raw = "" if raw is interactive.BACK else str(raw).strip()
            if raw and raw not in patterns:
                cfg["retry_on"] = patterns + [raw]
                changed = True
        elif act == "remove":
            if not patterns:
                print("  nothing to remove.")
                continue
            sel = interactive.pick("Remove which pattern?", patterns)
            if sel is interactive.BACK:
                continue
            cfg["retry_on"] = [p for p in patterns if p != sel]
            changed = True


def _resolve_repo_key(args, cfg):
    """Which repo to edit: detect from --path, else pick from configured repos.
    Returns a key, or None if there's nothing to edit / the user backed out."""
    try:
        _, ws, repo, _ = detect_repo(getattr(args, "path", ".") or ".")
        return f"{ws}/{repo}"
    except RepipeError:
        pass
    keys = sorted((cfg.get("repos") or {}).keys())
    if not keys:
        print("  not in a repo and no repos configured yet — "
              "run `repipe init` inside a repo first.")
        return None
    sel = interactive.pick("Which repo?", keys)
    return None if sel is interactive.BACK else sel


def _pick_provider(cur):
    """Pick a provider name (known adapters + custom). Returns a name, or None
    if the user backed out."""
    known = sorted(PROVIDERS_BY_NAME)
    options = [(n, n) for n in known] + [("__custom__", "custom…")]
    default_idx = known.index(cur) if cur in known else 0
    sel = interactive.pick("Provider", options, default_idx=default_idx,
                           to_str=lambda x: x[1])
    if sel is interactive.BACK:
        return None
    if sel[0] == "__custom__":
        raw = interactive.ask("Provider name", default=cur or None)
        return None if raw is interactive.BACK else str(raw).strip()
    return sel[0]


def _edit_repo(cfg, key) -> bool:
    """Submenu editing a repo's flat string fields. Returns True if changed."""
    changed = False
    fields = [
        ("provider", "Provider"),
        ("qa_branch_prefix", "QA branch prefix"),
        ("prod_branch_prefix", "Prod branch prefix"),
    ]
    while True:
        r = config.get_repo(cfg, key)
        rows = [(fk, f"{label:<18}{r.get(fk) or '(unset)'}") for fk, label in fields]
        rows.append(("__vars__", f"Variables ({len(r.get('variables') or {})})  ›"))
        rows.append(("done", "Done"))
        sel = interactive.pick(f"Repo: {key}", rows,
                               to_str=lambda x: x[1], allow_back=False)[0]
        if sel == "done":
            return changed
        if sel == "__vars__":
            changed = _edit_variables(cfg, key) or changed
            continue
        cur = config.get_repo(cfg, key).get(sel) or ""
        if sel == "provider":
            new = _pick_provider(cur)
        else:
            raw = interactive.ask(dict(fields)[sel], default=cur or None)
            new = None if raw is interactive.BACK else str(raw).strip()
        if new is not None and new != cur:
            config.ensure_repo(cfg, key)[sel] = new
            changed = True


# --- per-variable schema editor (Phase 3) -----------------------------------
#
# Each helper mutates a single variable's `entry` dict (or the `schema` dict for
# add/remove) in place and returns True if it changed something. String setters
# treat blank input as "keep" and a literal "-" as "clear" (delete the key), so
# optional fields can be unset. Toggles never write the implicit default.

def _set_str_field(entry, field, label) -> bool:
    cur = entry.get(field) or ""
    raw = interactive.ask(f"{label} (- to clear)", default=cur or None)
    if raw is interactive.BACK:
        return False
    raw = str(raw).strip()
    if raw == "":
        return False
    if raw == "-":
        if field in entry:
            del entry[field]
            return True
        return False
    if raw != cur:
        entry[field] = raw
        return True
    return False


def _toggle_field(entry, field, label, default) -> bool:
    cur = bool(entry.get(field, default))
    new = interactive.confirm(f"{label}?", default=cur)
    if new != cur:
        entry[field] = new
        return True
    return False


def _edit_enum(entry) -> bool:
    """Add/remove the variable's allowed-values list. Removing the last value
    drops the `enum` key entirely (⇒ any input allowed)."""
    changed = False
    while True:
        values = list(entry.get("enum") or [])
        if values:
            print(interactive.dim("enum values:"))
            for i, v in enumerate(values, 1):
                print(f"  {i}) {v}")
        else:
            print(interactive.dim("no enum values (any input allowed)"))
        act = interactive.pick("Enum", [
            ("add", "Add value…"), ("remove", "Remove…"), ("done", "Done"),
        ], to_str=lambda a: a[1], allow_back=False)[0]
        if act == "done":
            return changed
        if act == "add":
            raw = interactive.ask("New value")
            raw = "" if raw is interactive.BACK else str(raw).strip()
            if raw and raw not in values:
                entry["enum"] = values + [raw]
                changed = True
        elif act == "remove":
            if not values:
                print("  nothing to remove.")
                continue
            sel = interactive.pick("Remove which value?", values)
            if sel is interactive.BACK:
                continue
            remaining = [v for v in values if v != sel]
            if remaining:
                entry["enum"] = remaining
            else:
                entry.pop("enum", None)
            changed = True


def _edit_default(entry) -> bool:
    """Edit `default`. If an enum is set, offer it as a picker; else free text."""
    values = list(entry.get("enum") or [])
    if not values:
        return _set_str_field(entry, "default", "default")
    cur = entry.get("default") or ""
    opts = ([("__clear__", "(clear)")] + [(v, v) for v in values]
            + [("__custom__", "custom…")])
    default_idx = 1 + values.index(cur) if cur in values else 0
    sel = interactive.pick("Default", opts, default_idx=default_idx,
                           to_str=lambda x: x[1])
    if sel is interactive.BACK:
        return False
    if sel[0] == "__clear__":
        return entry.pop("default", None) is not None
    if sel[0] == "__custom__":
        return _set_str_field(entry, "default", "default")
    if sel[0] != cur:
        entry["default"] = sel[0]
        return True
    return False


def _edit_autofill(entry) -> bool:
    """Autofill has one known source (git_email); offer that or (none)."""
    cur = entry.get("autofill") or ""
    opts = [("", "(none)"), ("git_email", "git_email")]
    sel = interactive.pick("Autofill", opts,
                           default_idx=1 if cur == "git_email" else 0,
                           to_str=lambda x: x[1])
    if sel is interactive.BACK:
        return False
    val = sel[0]
    if val == cur:
        return False
    if val == "":
        entry.pop("autofill", None)
    else:
        entry["autofill"] = val
    return True


def _edit_variable(schema, vname) -> bool:
    """Per-field editor for one variable. Returns True if changed; may remove
    the variable from `schema`."""
    changed = False
    while True:
        entry = schema.get(vname, {})
        yn = lambda b: "yes" if b else "no"
        sv = lambda f: entry.get(f) or "(unset)"
        rows = [
            ("enum", f"enum             {entry.get('enum') or '(none)'}"),
            ("default", f"default          {sv('default')}"),
            ("required", f"required         {yn(entry.get('required', True))}"),
            ("pattern", f"pattern          {sv('pattern')}"),
            ("autofill", f"autofill         {sv('autofill')}"),
            ("remember", f"remember         {yn(entry.get('remember', False))}"),
            ("no_spaces_unless", f"no_spaces_unless {sv('no_spaces_unless')}"),
            ("hint", f"hint             {sv('hint')}"),
            ("__remove__", "Remove this variable"),
            ("__done__", "Done"),
        ]
        sel = interactive.pick(f"Variable: {vname}", rows,
                               to_str=lambda x: x[1], allow_back=False)[0]
        if sel == "__done__":
            return changed
        if sel == "__remove__":
            if interactive.confirm(f"Remove variable '{vname}'?", default=False):
                schema.pop(vname, None)
                return True
            continue
        entry = schema.setdefault(vname, {})
        if sel == "enum":
            changed = _edit_enum(entry) or changed
        elif sel == "default":
            changed = _edit_default(entry) or changed
        elif sel == "autofill":
            changed = _edit_autofill(entry) or changed
        elif sel in ("required", "remember"):
            changed = _toggle_field(entry, sel, sel, sel == "required") or changed
        else:  # pattern, no_spaces_unless, hint
            changed = _set_str_field(entry, sel, sel) or changed


def _edit_variables(cfg, key) -> bool:
    """List the repo's variables; pick one to edit, or add a new one."""
    changed = False
    while True:
        schema = config.ensure_repo(cfg, key).setdefault("variables", {})
        rows = [("var:" + n, f"{n}  ›") for n in sorted(schema)]
        rows.append(("__add__", "Add variable…"))
        rows.append(("__done__", "Done"))
        sel = interactive.pick(f"Variables — {key}", rows,
                               to_str=lambda x: x[1], allow_back=False)[0]
        if sel == "__done__":
            return changed
        if sel == "__add__":
            raw = interactive.ask("Variable name")
            raw = "" if raw is interactive.BACK else str(raw).strip()
            if not raw:
                continue
            if raw not in schema:
                schema[raw] = {}
                changed = True
            _edit_variable(schema, raw)
            continue
        changed = _edit_variable(schema, sel[len("var:"):]) or changed


def _random_ntfy_url() -> str:
    """A hard-to-guess public ntfy topic. On ntfy.sh anyone who knows the topic
    can read your notifications, so the whole point is that this is random enough
    that nobody guesses it — hence 128 bits of entropy, not a friendly name."""
    return "https://ntfy.sh/repipe-" + secrets.token_hex(16)


def _edit_phone_push(cfg) -> bool:
    """Submenu: pick which push destination to configure. Every provider in the
    registry is listed with its current URL; each is edited independently and any
    number can be active at once — all configured providers fire on a run event."""
    changed = False
    while True:
        rows = [(p, f"{p['label']:<12} {cfg.get(p['config_key']) or '(off)'}")
                for p in notify_mod.PUSH_PROVIDERS]
        sel = interactive.pick("Phone push", rows, to_str=lambda r: r[1])
        if sel is interactive.BACK:
            return changed
        changed = _edit_push_provider(cfg, sel[0]) or changed


def _edit_push_provider(cfg, provider) -> bool:
    """Set / clear one provider's URL, then offer a test send so the user can
    confirm their phone before relying on it. ntfy can GENERATE a random topic
    (a user-picked name is easy to guess); other providers paste a URL from the
    destination's own UI (e.g. a Google Chat incoming-webhook)."""
    key, label = provider["config_key"], provider["label"]
    cur = cfg.get(key) or ""
    if provider["id"] == "gchat":
        print("Google Chat phone push posts to a private space's incoming webhook —")
        print('a "space of one" reaches only you. In Google Chat create a space')
        print("(invite nobody), then Apps & integrations → Manage webhooks → Add,")
        print("and paste the URL below. Keep it private — it is the credential.")
    else:
        print(f"{label} phone push sends the finish notification to your phone —")
        print("it fires even with no terminal watching.")
    rows = []
    if provider["can_generate"]:
        rows.append(("gen", "Generate a random ntfy.sh topic (recommended)"))
    rows.append(("manual", f"Enter the {label} URL myself"))
    if cur:
        rows.append(("off", f"Turn {label} off"))
    sel = interactive.pick(label, rows, to_str=lambda x: x[1])
    if sel is interactive.BACK:
        return False
    changed = False
    if sel[0] == "gen":
        cfg[key] = _random_ntfy_url()
        changed = True
        print(f"{interactive.green('✓')} generated: {cfg[key]}")
        print("  → open the ntfy app on your phone and subscribe to that topic")
        print("    (copy the part after the last '/'). Keep this URL private.")
    elif sel[0] == "manual":
        raw = interactive.ask(f"{label} URL", default=cur or None)
        if raw is not interactive.BACK:
            raw = str(raw).strip()
            if raw and raw != cur:
                cfg[key] = raw
                changed = True
    elif sel[0] == "off":
        cfg.pop(key, None)
        changed = True
        print(f"{interactive.green('✓')} {label} turned off")
    url = cfg.get(key)
    if url and interactive.confirm("Send a test push now?", default=True):
        getattr(notify_mod, provider["send"])(
            url, "repipe · test", "phone push is wired up ✓",
            tags="bell", click="", token=_notify_token())
        print(f"{interactive.green('✓')} sent — check your phone "
              "(nothing arriving? verify the destination is set up correctly)")
    return changed


def cmd_config(args) -> int:
    cfg = config.load()
    if getattr(args, "show", False):
        return _config_show(cfg)

    d = _CONFIG_DEFAULTS
    dirty = False
    while True:
        onoff = lambda b: "on" if b else "off"
        rows = [
            ("patterns", f"Retry patterns ({len(cfg.get('retry_on') or [])})  ›"),
            ("max_retries", f"Max retries       {cfg.get('max_retries', d['max_retries'])}"),
            ("match", f"Match mode        {cfg.get('match', d['match'])}"),
            ("poll_interval", f"Poll interval     {cfg.get('poll_interval', d['poll_interval'])}s"),
            ("timeout", f"Timeout           {cfg.get('timeout', d['timeout'])}s"),
            ("notify", f"Notifications     {onoff(cfg.get('notify', d['notify']))}"),
            ("notify_steps", f"Notify per-step   {onoff(cfg.get('notify_steps', d['notify_steps']))}"),
            ("phone_push", f"Phone push        {_phone_push_summary(cfg)}  ›"),
            ("user_email", f"Email (autofill)  {cfg.get('user_email') or '(unset)'}"),
            ("repo", "Repo settings     ›"),
            ("save", "Save & exit"),
            ("quit", "Quit (discard)"),
        ]
        key = interactive.pick("repipe config", rows,
                               to_str=lambda r: r[1], allow_back=False)[0]

        if key == "save":
            path = config.save(cfg)
            print(f"{interactive.green('✓')} saved {path}")
            return EXIT_OK
        if key == "quit":
            if dirty and not interactive.confirm("Discard changes?", default=False):
                continue
            return EXIT_OK
        if key == "patterns":
            dirty = _edit_patterns(cfg) or dirty
        elif key == "max_retries":
            v = _ask_int("Max retries", cfg.get("max_retries", d["max_retries"]), 0)
            if v != cfg.get("max_retries", d["max_retries"]):
                cfg["max_retries"] = v
                dirty = True
        elif key == "match":
            cur = cfg.get("match", d["match"])
            modes = ["substring", "regex"]
            sel = interactive.pick("Match mode", modes,
                                   default_idx=modes.index(cur) if cur in modes else 0)
            if sel is not interactive.BACK and sel != cur:
                cfg["match"] = sel
                dirty = True
        elif key in ("poll_interval", "timeout"):
            v = _ask_int(key.replace("_", " ").capitalize(), cfg.get(key, d[key]), 1)
            if v != cfg.get(key, d[key]):
                cfg[key] = v
                dirty = True
        elif key in ("notify", "notify_steps"):
            cur = cfg.get(key, d[key])
            prompt = "Enable notifications?" if key == "notify" else "Notify on each step?"
            new = interactive.confirm(prompt, default=cur)
            if new != cur:
                cfg[key] = new
                dirty = True
        elif key == "phone_push":
            dirty = _edit_phone_push(cfg) or dirty
        elif key == "user_email":
            print(interactive.dim(
                "  Used to autofill pipeline variables (autofill = \"git_email\") "
                "and shown here."))
            print(interactive.dim(
                "  This is NOT your login email — to change that, run `repipe login`."))
            raw = interactive.ask("Autofill email",
                                  default=cfg.get("user_email") or None)
            if raw is not interactive.BACK:
                raw = str(raw).strip()
                if raw != (cfg.get("user_email") or ""):
                    cfg["user_email"] = raw
                    dirty = True
                    login_email = _login_email()
                    if raw and login_email and raw != login_email:
                        print(interactive.dim(
                            f"  note: your Bitbucket login email ({login_email}) is "
                            "unchanged — run `repipe login` to change that."))
        elif key == "repo":
            rkey = _resolve_repo_key(args, cfg)
            if rkey and _edit_repo(cfg, rkey):
                dirty = True


def _ask_token(label) -> str:
    token = getpass.getpass(f"  {label} (input hidden): ").strip()
    if not token:
        raise RepipeError("no token entered — nothing saved.", EXIT_CONFIG)
    return token


def _persist_login_email(mapping):
    """Bridge login → config: the Bitbucket API-token method collects an email
    (saved as the credential REPIPE_EMAIL). Mirror it into config's `user_email`
    — a different key, in a different file — so it shows up in `repipe config`
    and feeds `git_email` autofill. Only fills a blank one, never clobbers a
    user-set value. Returns the email if it wrote one, else None."""
    email = (mapping or {}).get("REPIPE_EMAIL")
    if not email:
        return None
    cfg = config.load()
    if cfg.get("user_email"):
        return None
    cfg["user_email"] = email
    config.save(cfg)
    return email


def _access_token_url(workspace, repo) -> str:
    """The Bitbucket repository Access-tokens settings page. Deep-links straight
    to this repo's page when we know it; otherwise the generic settings path."""
    if workspace and repo:
        return f"https://bitbucket.org/{workspace}/{repo}/admin/access-tokens"
    return ("https://bitbucket.org → Repository (or Workspace) settings → "
            "Access tokens")


def _print_token_url(url, scopes=None):
    """Show where to generate the token — the URL on its own line so terminals
    render it as a clickable link, with any scope hint on a separate dim line."""
    if not url:
        return
    print(interactive.dim("  generate the token at:"))
    print("    " + url)
    if scopes:
        print(interactive.dim("    scopes/permissions needed: " + scopes))


def _prompt_credential(name, workspace=None, repo=None):
    """Interactively collect a credential, tailored to the detected host.
    Returns (mapping, auth). `name` is the provider name or None (unknown)."""
    if name == "github":
        # GitHub authenticates with a single Bearer token — no email variant.
        print("  GitHub needs a personal access token with the "
              + interactive.bold("actions:write") + " scope (to trigger workflows).")
        _print_token_url(
            "https://github.com/settings/personal-access-tokens/new",
            "fine-grained: Actions = Read and write   ·   "
            "classic (github.com/settings/tokens): repo + workflow")
        token = _ask_token("paste the token")
        return {"REPIPE_TOKEN": token}, ("bearer", token)

    # Bitbucket (or an unknown host): two ways in — arrow-key pick, then entry.
    if name == "bitbucket":
        methods = [
            {"id": "api", "label": "Atlassian API token",
             "desc": "works without admin · email + token",
             "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
             "scopes": "read/write:pipeline:bitbucket, read:repository:bitbucket"},
            {"id": "access", "label": "Access token",
             "desc": "repo/workspace token · needs admin",
             "url": _access_token_url(workspace, repo),
             "scopes": "Pipelines: read + write"},
        ]
    else:
        methods = [
            {"id": "access", "label": "A single token",
             "desc": "GitHub PAT or Bitbucket access token",
             "url": "GitHub → https://github.com/settings/personal-access-tokens/new"
                    "   ·   Bitbucket → repo/workspace settings → Access tokens",
             "scopes": "GitHub: actions:write · Bitbucket: Pipelines read + write"},
            {"id": "api", "label": "Bitbucket API token",
             "desc": "Atlassian email + API token",
             "url": "https://id.atlassian.com/manage-profile/security/api-tokens",
             "scopes": "read/write:pipeline:bitbucket, read:repository:bitbucket"},
        ]

    choice = interactive.pick(
        "How do you want to authenticate?",
        methods,
        to_str=lambda m: f"{m['label']}  {interactive.dim('— ' + m['desc'])}",
        allow_back=False,
    )
    _print_token_url(choice.get("url"), choice.get("scopes"))

    if choice["id"] == "api":
        email = interactive.ask("Atlassian account email", allow_back=False)
        if not email:
            raise RepipeError("email is required for the API-token method.", EXIT_CONFIG)
        token = _ask_token("paste the API token")
        return {"REPIPE_EMAIL": email, "REPIPE_API_TOKEN": token}, ("basic", email, token)
    token = _ask_token("paste the token")
    return {"REPIPE_TOKEN": token}, ("bearer", token)


def cmd_login(args) -> int:
    """Prompt for a token (hidden) and save it to the credentials file, 0o600.
    With --verify, check it against the current repo's host before saving."""
    provider = None
    workspace = repo = None
    try:
        host, workspace, repo, _ = detect_repo(args.path)
        provider = choose_provider(host, args.provider)(workspace, repo)
    except RepipeError:
        pass  # not in a recognized repo — fine unless --verify

    path = credentials_path()
    if os.path.exists(path) and not args.force:
        raise RepipeError(
            f"{path} already exists — pass --force to overwrite.", EXIT_CONFIG
        )

    name = provider.NAME if provider else None
    print(interactive.bold("repipe login"))
    if name:
        print(interactive.dim(f"  detected {name} · {workspace}/{repo}"))
    print()
    mapping, auth = _prompt_credential(name, workspace, repo)

    if args.verify:
        if not provider:
            raise RepipeError(
                "--verify must run inside a recognized repo (it needs a host + "
                "repo to make a read-only check). Re-run there, or drop --verify.",
                EXIT_CONFIG,
            )
        code = provider.verify_auth(auth)
        if code == 200:
            print(interactive.green("  ✓ token verified"))
        elif code == 401:
            if not args.force:
                raise RepipeError(
                    "token rejected (401) — not saving. Re-run with --force to "
                    "save it anyway.",
                    EXIT_CONFIG,
                )
            print(interactive.yellow("  ⚠ token rejected (401) — saving anyway (--force)"))
        elif code == 403:
            print(interactive.yellow(
                "  ⚠ authenticated but missing a scope (403) — saving; check the "
                "token's Pipelines/Actions scopes"))
        elif code == 0:
            print(interactive.yellow("  ⚠ couldn't reach the host to verify — saving anyway"))
        elif code is None:
            print(interactive.dim("  (provider can't verify; saving without a check)"))
        else:
            print(interactive.yellow(f"  ⚠ unexpected status {code} — saving anyway"))

    saved = save_credentials(mapping)
    print(f"  wrote {saved} (chmod 600)")
    print(interactive.dim("  the environment still overrides this file when set."))
    mirrored = _persist_login_email(mapping)
    if mirrored:
        print(interactive.dim(
            f"  also set user_email = {mirrored} in {config.config_path()}"))
    return EXIT_OK


def cmd_doctor(args) -> int:
    """Diagnose the local setup — repo detection, credentials, auth, config,
    retry patterns, and notifications. Read-only, except it fires one test phone
    push when notify_url is set and we're in a terminal. Exits 3 (config/auth)
    if any hard check fails (✗), else 0; warnings (⚠) don't affect the code."""
    ok, warn, bad = interactive.green("✓"), interactive.yellow("⚠"), interactive.red("✗")
    info = interactive.dim("·")
    failed = False

    print(interactive.bold("repipe doctor") + interactive.dim(f"  (v{__version__})"))
    print()

    # 1. Repo detection
    provider = None
    try:
        host, workspace, repo, _ = detect_repo(args.path)
        provider = choose_provider(host, args.provider)(workspace, repo)
        print(f"  {ok} repo           {provider.NAME} · {workspace}/{repo}")
    except RepipeError as e:
        print(f"  {warn} repo           not a recognized CI repo here")
        print(interactive.dim(f"      {e}"))
        print(interactive.dim("      auth + trigger checks need to run inside a repo"))

    # 2. Credentials (env or credentials file)
    auth = None
    try:
        auth = get_auth(required=False)
    except RepipeError:
        auth = None
    if auth:
        detail = auth[0] + (f" · {auth[1]}" if auth[0] == "basic" else "")
        print(f"  {ok} credentials    {detail}")
    else:
        print(f"  {bad} credentials    none found — run `repipe login`")
        failed = True

    # 3. Auth validity — a cheap read-only probe (needs a repo + credentials)
    if provider and auth:
        code = provider.verify_auth(auth)
        if code == 200:
            print(f"  {ok} auth           verified ({provider.NAME}, 200)")
        elif code == 401:
            print(f"  {bad} auth           rejected (401) — token invalid or wrong host")
            failed = True
        elif code == 403:
            print(f"  {warn} auth           authenticated but missing a scope (403)")
        elif code == 0:
            print(f"  {warn} auth           couldn't reach {provider.NAME} to verify")
        elif code is None:
            print(f"  {info} auth           {provider.NAME} can't verify (skipped)")
        else:
            print(f"  {warn} auth           unexpected status {code}")
    elif auth:
        print(f"  {info} auth           skipped (not in a repo)")

    # 4. Config file — load() swallows parse errors, so re-parse to surface them
    cfg = config.load()
    cfg_path = config.config_path()
    if not os.path.isfile(cfg_path):
        print(f"  {info} config         none yet (optional; `repipe init` scaffolds one)")
    elif config.tomllib is None:
        print(f"  {info} config         present (can't parse-check on this Python)")
    else:
        try:
            with open(cfg_path, "rb") as f:
                config.tomllib.load(f)
            print(f"  {ok} config         {cfg_path}")
        except Exception as e:
            print(f"  {bad} config         parse error — {e}")
            failed = True

    # 5. Retry patterns
    patterns = cfg.get("retry_on") or []
    if patterns:
        print(f"  {ok} retry patterns {len(patterns)} configured")
    else:
        print(f"  {warn} retry patterns none — auto-retry is off (`repipe suggestions`)")

    # 6. Notifications
    print(f"  {ok if cfg.get('notify', True) else info} desktop alerts "
          f"{'on' if cfg.get('notify', True) else 'off'}")
    enabled = [p for p in notify_mod.PUSH_PROVIDERS if cfg.get(p["config_key"])]
    if not enabled:
        print(f"  {info} phone push     off (`repipe config` → Phone push)")
    else:
        labels = ", ".join(p["label"] for p in enabled)
        if interactive.live():
            for p in enabled:
                getattr(notify_mod, p["send"])(
                    cfg[p["config_key"]], "repipe · doctor", "doctor test push ✓",
                    tags="stethoscope", click="", token=_notify_token())
            print(f"  {ok} phone push     {labels} configured — test sent, check your phone")
        else:
            print(f"  {ok} phone push     {labels} configured (run in a terminal to send a test)")

    print()
    if failed:
        print(interactive.red("  problems found above (✗) — fix those first."))
        return EXIT_CONFIG
    print(interactive.green("  all good."))
    return EXIT_OK


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
    p_run.add_argument("--notify", dest="notify", action="store_true", default=None,
                       help="desktop notification on finish (default: on in a terminal)")
    p_run.add_argument("--no-notify", dest="notify", action="store_false",
                       help="never notify (also suppresses the terminal bell)")
    p_run.add_argument("--notify-steps", dest="notify_steps", action="store_true",
                       help="also ping (silently) as each step/job completes")
    p_run.add_argument("--phone-notify", dest="phone_notify", action="store_true",
                       default=None,
                       help="push the finish notification to your phone via ntfy "
                            "(needs notify_url in config; fires even headless)")
    p_run.add_argument("--no-phone-notify", dest="phone_notify",
                       action="store_false", help="never push to your phone")

    p_login = sub.add_parser("login", parents=[common],
                             help="save a CI token to ~/.config/repipe/credentials")
    p_login.add_argument("--verify", action="store_true",
                         help="check the token with a read-only API call before "
                              "saving (run inside a repo)")
    p_login.add_argument("--force", action="store_true",
                         help="overwrite an existing file / save despite a failed verify")

    sub.add_parser("doctor", parents=[common],
                   help="diagnose your setup (credentials, auth, config, alerts)")
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

    p_config = sub.add_parser("config", parents=[common],
                              help="view / edit repipe settings (interactive)")
    p_config.add_argument("--show", action="store_true",
                          help="print current global settings and exit (no TTY needed)")

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
        if args.command == "login":
            return cmd_login(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "init":
            return cmd_init(args)
        if args.command == "suggestions":
            return cmd_suggestions(args)
        if args.command == "upgrade":
            return cmd_upgrade(args)
        if args.command == "rerun":
            return cmd_rerun(args)
        if args.command == "config":
            return cmd_config(args)
    except RepipeError as e:
        print(f"repipe: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("\naborted.", file=sys.stderr)
        return EXIT_CONFIG

    parser.print_help()
    return EXIT_OK
