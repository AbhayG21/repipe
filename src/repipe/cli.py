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
from .gitutil import detect_repo
from .http import get_auth
from .model import RunState
from .output import fmt_var, state_symbol
from .providers import choose_provider
from .retry import build_patterns, first_match
from .varschema import resolve_variables


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

    method, url, body = provider.trigger_request(target.name, ref, variables)

    if args.dry_run:
        print("DRY RUN — no request sent\n")
        print(f"{method} {url}")
        print(json.dumps(body, indent=2, ensure_ascii=False))
        print(f"\n(pipeline '{target.name}' [{target.env}] on branch '{ref}')")
        return EXIT_OK

    auth = get_auth(required=True)
    run = provider.trigger(target.name, ref, variables, auth)
    _announce(target.name, ref, run)

    if args.no_wait:
        print("\n(--no-wait: triggered only, not watching)")
        return EXIT_OK

    return _watch_and_retry(provider, target, ref, variables, auth, run, args)


def _announce(pipeline, ref, run):
    print(f"➜ triggered {pipeline} on {ref}")
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

    print(f"\nwatching (poll {args.poll_interval}s, timeout {args.timeout}s, "
          f"max-retries {args.max_retries}) …")

    while True:
        # Poll the current run until it reaches a terminal state (or times out).
        last = None
        while run.state not in terminal:
            if time.monotonic() > deadline:
                print(f"⌛ timed out after {args.timeout}s (last state "
                      f"{run.native_state or run.state}) — {run.web_url}")
                return EXIT_TIMEOUT
            time.sleep(args.poll_interval)
            run = provider.get_run(run.id, auth)
            if run.native_state != last:
                print(f"  … {run.native_state or run.state}")
                last = run.native_state

        if run.state == RunState.SUCCESS:
            print(f"✓ #{run.number} SUCCESSFUL — {run.web_url}")
            return EXIT_OK

        if run.state == RunState.HALTED:
            print(f"‖ #{run.number} paused at a manual gate (build+push done). "
                  f"Approve the deploy here:\n  {run.web_url}")
            return EXIT_OK

        # FAILED — decide whether to retry.
        failed, logs = provider.failed_step_logs(run, auth)
        combined = "\n".join(text for _, text in logs)
        hit = first_match(combined, patterns, args.match)

        if hit is None:
            print(f"✗ #{run.number} FAILED — no retry pattern matched, not retrying.")
            print(f"  checked {len(patterns)} pattern(s) (mode={args.match}).")
            _print_failure(failed, logs)
            print(f"  {run.web_url}")
            return EXIT_FAILED_NOMATCH

        if attempt >= args.max_retries:
            print(f"✗ #{run.number} FAILED — matched '{hit}' but retries "
                  f"exhausted ({args.max_retries}).")
            _print_failure(failed, logs)
            print(f"  {run.web_url}")
            return EXIT_RETRIES

        attempt += 1
        print(f"↻ #{run.number} FAILED — matched '{hit}', re-triggering "
              f"(retry {attempt}/{args.max_retries}) …")
        run = provider.trigger(target.name, ref, variables, auth)
        _announce(target.name, ref, run)


def _not_yet(command: str, phase: int) -> int:
    print(
        f"repipe: `{command}` is not implemented yet (lands in phase {phase}).",
        file=sys.stderr,
    )
    return EXIT_CONFIG


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repipe",
        description="Trigger a CI pipeline and auto-retry it when it fails with "
        "a transient error. Bitbucket Cloud first; provider-agnostic core.",
        epilog="Run `repipe <command> --help` for command-specific help.",
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"repipe {__version__}")

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
                           help="trigger a pipeline (watch/retry lands in phase 3)")
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

    # Still stubbed — implemented in later phases.
    sub.add_parser("init", help="[phase 4] scaffold config from the repo")
    sub.add_parser("rerun", help="[phase 4] repeat the last invocation")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    try:
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
        if args.command in ("init", "rerun"):
            return _not_yet(args.command, 4)
    except RepipeError as e:
        print(f"repipe: {e}", file=sys.stderr)
        return e.code

    parser.print_help()
    return EXIT_OK
