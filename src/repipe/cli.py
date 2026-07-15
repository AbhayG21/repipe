"""argparse wiring + command handlers + main()."""

import argparse
import json
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
from .http import get_auth
from .model import RunState
from .output import fmt_var, state_symbol
from .providers import choose_provider
from .retry import build_patterns, first_match
from .varschema import resolve_variables, PROJECT_VALUES


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

    provided = _parse_vars(args.var)
    variables = resolve_variables(target, provided)  # fail-fast validation

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


def _finish_run(provider, target, ref, variables, args) -> int:
    """Shared trigger path: dry-run, prod gate, conservative prod retry,
    trigger, then watch/retry. Used by `run`, the interactive flow, and `rerun`.
    """
    method, url, body = provider.trigger_request(target.name, ref, variables)

    if args.dry_run:
        print("DRY RUN — no request sent\n")
        print(f"{method} {url}")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print(f"\n(pipeline '{target.name}' [{target.env}] on branch '{ref}')")
        return EXIT_OK

    _prod_gate(target, args)

    # Conservative prod policy: low retry cap, built-in transient patterns only
    # (never custom --retry-on), unless --force.
    if target.env == "prod" and not getattr(args, "force", False):
        if args.max_retries > 1:
            print("  (prod: capping retries at 1; use --force to override)")
            args.max_retries = 1
        if args.retry_on:
            print("  (prod: ignoring custom --retry-on; built-in patterns only)")
            args.retry_on = None
        args.no_default_patterns = False

    auth = get_auth(required=True)
    run = provider.trigger(target.name, ref, variables, auth)
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
    patterns = build_patterns(args.retry_on, use_defaults=not args.no_default_patterns)
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
            print(interactive.red(f"✗ #{run.number} FAILED")
                  + " — no retry pattern matched, not retrying.")
            print(interactive.dim(f"  checked {len(patterns)} pattern(s) (mode={args.match})."))
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
        run = provider.trigger(target.name, ref, variables, auth)
        _announce(target.name, ref, run)


def _run_args_namespace(**overrides):
    """A Namespace with every field _finish_run/_watch_and_retry expects,
    for the interactive flow and rerun (which don't come from the run parser)."""
    d = dict(
        dry_run=False, yes=False, no_wait=False, force=False,
        retry_on=None, match="substring", no_default_patterns=False,
        max_retries=2, poll_interval=20, timeout=3600,
    )
    d.update(overrides)
    return argparse.Namespace(**d)


def cmd_interactive(args) -> int:
    host, workspace, repo, branch = detect_repo(args.path)
    provider = choose_provider(host, args.provider)(workspace, repo)
    repo_key = f"{workspace}/{repo}"
    cfg = config.load()
    rcfg = config.get_repo(cfg, repo_key)

    targets = provider.parse_targets(args.path)
    if not targets:
        raise RepipeError(f"no {provider.TARGET_WORD}s found in this repo.", EXIT_CONFIG)

    print(f"{interactive.bold('repipe')} · {repo_key} "
          f"{interactive.dim('(' + provider.NAME + ')')}\n")

    # 1. pipeline
    target = interactive.pick(
        "Pipeline", targets,
        to_str=lambda t: f"{t.name}  {interactive.env_badge(t.env)}",
    )

    # 2. env — inferred default, overridable
    env_opts = ["qa", "prod"]
    target.env = interactive.pick("Environment", env_opts,
                                  default_idx=env_opts.index(target.env),
                                  to_str=interactive.env_badge)

    # 3. branch — newest release-prefixed + current, or manual
    prefix = rcfg.get(f"{target.env}_branch_prefix") or f"{target.env}-release"
    cands = branch_candidates(args.path, branch, prefix)
    options = cands + ["(enter manually)"]
    picked = interactive.pick(f"Branch (prefix '{prefix}')", options,
                              default_idx=0 if cands else len(options) - 1)
    ref = interactive.ask("Branch name", default=branch) if picked == "(enter manually)" else picked
    if not ref:
        raise RepipeError("no branch selected.", EXIT_CONFIG)

    # 4. variables — prompt only what's needed
    provided = {}
    new_email = None
    for var in target.variables:
        if var.name == "USEREMAIL":
            existing = cfg.get("user_email") or run_git(["config", "user.email"], args.path)
            if existing:
                provided[var.name] = existing
                if not cfg.get("user_email"):
                    new_email = existing
            else:
                provided[var.name] = new_email = interactive.ask("USEREMAIL")
        elif var.name == "Project":
            default_proj = rcfg.get("default_project") or var.default or PROJECT_VALUES[0]
            di = PROJECT_VALUES.index(default_proj) if default_proj in PROJECT_VALUES else 0
            provided[var.name] = interactive.pick("Project", PROJECT_VALUES, default_idx=di)
        elif var.allowed_values:
            di = var.allowed_values.index(var.default) if var.default in var.allowed_values else 0
            provided[var.name] = interactive.pick(var.name, var.allowed_values, default_idx=di)
        else:
            if var.name == "FLAVOURS" and rcfg.get("flavours"):
                print("  recent FLAVOURS: " + ", ".join(rcfg["flavours"][-5:]))
            provided[var.name] = interactive.ask(var.name, default=var.default)

    variables = resolve_variables(target, provided)

    # 5. summary + confirm (prod is confirmed by the typed-name gate in _finish_run)
    print(f"\n{interactive.cyan('→')} {interactive.bold(target.name)} "
          f"{interactive.env_badge(target.env)} on {interactive.bold(ref)}")
    for k, v in variables:
        print(f"    {interactive.dim(k + ' =')} {v}")
    if not args.dry_run and not args.yes and target.env != "prod":
        if not interactive.confirm("Proceed?", default=True):
            print("aborted.")
            return EXIT_OK

    rargs = _run_args_namespace(
        dry_run=args.dry_run, yes=args.yes,
        match=cfg.get("match", "substring"),
        max_retries=cfg.get("max_retries", 2),
        retry_on=cfg.get("retry_on"),
    )
    code = _finish_run(provider, target, ref, variables, rargs)

    # 6. persist (only after a real trigger passed the prod gate)
    if not args.dry_run:
        if new_email:
            cfg["user_email"] = new_email
        vmap = dict(variables)
        if "FLAVOURS" in vmap:
            config.remember_flavour(cfg, repo_key, vmap["FLAVOURS"])
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
    has_project = any(v.name == "Project" for t in targets for v in t.variables)
    if has_project:
        r.setdefault("default_project", "NON-PCI")
    if not cfg.get("user_email"):
        em = run_git(["config", "user.email"], args.path)
        if em:
            cfg["user_email"] = em

    path = config.save(cfg)
    print(f"wrote {path}")
    print(f"  repo:      {key}")
    print(f"  provider:  {provider.NAME}")
    print(f"  pipelines: {len(targets)}")
    if has_project:
        print(f"  default_project: {r.get('default_project')}")
    print("\nEdit that file to tune defaults (retry_on, branch prefixes, max_retries).")
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
    variables = resolve_variables(target, dict(lr.get("vars") or {}))
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
                       help="how --retry-on/built-in patterns match (default: substring)")
    p_run.add_argument("--no-default-patterns", action="store_true",
                       help="don't use the built-in transient-error patterns")
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
