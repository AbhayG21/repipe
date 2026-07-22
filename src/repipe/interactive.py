"""Terminal UI: colors, a back-aware arrow-key selector, prompts, banner, spinner.

Zero-dependency (ANSI + termios/tty from the stdlib). Everything degrades
gracefully: colors switch off when stdout isn't a TTY (or NO_COLOR is set), and
the selector falls back to a numbered input prompt when stdin isn't a TTY — so
piped/CI use keeps working unchanged.
"""

import os
import sys

from .errors import RepipeError, EXIT_CONFIG

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Sentinel returned by pick()/ask() when the user asks to go back a step.
BACK = object()


# --- color ------------------------------------------------------------------

def color_enabled() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(s, code):
    return f"\x1b[{code}m{s}\x1b[0m" if color_enabled() else s


def bold(s):
    return _c(s, "1")


def dim(s):
    return _c(s, "2")


def green(s):
    return _c(s, "32")


def red(s):
    return _c(s, "31")


def yellow(s):
    return _c(s, "33")


def cyan(s):
    return _c(s, "36")


def env_badge(env: str) -> str:
    return green("[qa]") if env == "qa" else red(f"[{env}]")


def banner(repo_key, provider_name, version, update_note=None):
    """Compact welcome header. Shown whenever interactive (stdout is a TTY);
    colors within it still respect NO_COLOR. `update_note`, when set, is a short
    'a newer version is available' line shown under the version."""
    if not sys.stdout.isatty():
        return
    print(bold(cyan("repipe")) + dim(f"  v{version}"))
    print(dim(f"{repo_key} · {provider_name}"))
    if update_note:
        print(yellow(update_note))
    print(dim("↑/↓ move · ← back · Enter select · ^C quit"))
    print(dim("─" * 46))


# --- input helpers ----------------------------------------------------------

def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        raise RepipeError(
            "interactive input required — run `repipe run …` for non-interactive use.",
            EXIT_CONFIG,
        )


def _can_raw() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
        return True
    except Exception:
        return False


def _step_line(step):
    if step:
        print(dim(f"Step {step[0]} of {step[1]}"))
        return 1
    return 0


def _numbered(label, items, default_idx, to_str, allow_back, step):
    _step_line(step)
    for i, it in enumerate(items):
        marker = cyan("→") if i == default_idx else " "
        print(f"  {marker} {dim(str(i + 1) + ')')} {to_str(it)}")
    hint = "  (‹ back)" if allow_back else ""
    while True:
        raw = _input(f"{cyan('?')} {bold(label)}{dim(hint)} [{default_idx + 1}]: ").strip()
        if allow_back and raw in ("<", "b"):
            return BACK
        if not raw:
            return items[default_idx]
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print(f"  enter a number 1–{len(items)}" + (" (or '<' to go back)" if allow_back else ""))


def pick(label, items, default_idx=0, to_str=str, allow_back=True, step=None):
    """Back-aware arrow-key selector. Returns the chosen item, or BACK.
    ↑/↓ move, Enter/digit select, ←/Backspace go back. Falls back to a
    numbered prompt when stdin isn't a TTY."""
    if not items:
        raise RepipeError(f"nothing to choose for '{label}'.", EXIT_CONFIG)
    default_idx = max(0, min(default_idx, len(items) - 1))
    if not _can_raw():
        return _numbered(label, items, default_idx, to_str, allow_back, step)

    import termios
    import tty

    idx = default_idx
    n = len(items)
    header = _step_line(step)
    hint = "↑/↓ · Enter" + (" · ← back" if allow_back else "")
    print(f"{cyan('?')} {bold(label)} {dim('(' + hint + ')')}")
    header += 1

    def render():
        for i, it in enumerate(items):
            selected = i == idx
            pointer = green("❯") if selected else " "
            text = to_str(it)
            if selected:
                text = bold(text)
            sys.stdout.write(f"\r\x1b[K {pointer} {text}\r\n")
        sys.stdout.flush()

    render()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    go_back = False
    try:
        tty.setraw(fd)
        while True:
            ch = os.read(fd, 1).decode(errors="ignore")
            if not ch:
                break
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if ch == "\x1b":  # escape sequence (arrows)
                seq = os.read(fd, 2).decode(errors="ignore")
                if seq == "[A":
                    idx = (idx - 1) % n
                elif seq == "[B":
                    idx = (idx + 1) % n
                elif seq == "[D" and allow_back:  # left
                    go_back = True
                    break
                else:
                    continue
            elif ch in ("\r", "\n"):
                break
            elif ch == "\x7f" and allow_back:  # backspace
                go_back = True
                break
            elif ch.isdigit() and 1 <= int(ch) <= n:
                idx = int(ch) - 1
                break
            else:
                continue
            sys.stdout.write(f"\x1b[{n}A")  # move up to repaint the block
            render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    up = header + n
    sys.stdout.write(f"\x1b[{up}A\r\x1b[J")  # clear header + block
    if go_back:
        print(dim(f"‹ {label}"))
        return BACK
    chosen = items[idx]
    print(f"{green('✓')} {dim(label)}  {bold(to_str(chosen))}")
    return chosen


def ask(label, default=None, allow_back=True, step=None):
    if color_enabled():
        _step_line(step)
    suffix = dim(f" [{default}]") if default else ""
    hint = dim(" (‹ back)") if allow_back else ""
    raw = _input(f"{cyan('?')} {bold(label)}{suffix}{hint}: ").strip()
    if allow_back and raw == "<":
        return BACK
    return raw or (default or "")


def confirm(label, default=False) -> bool:
    hint = dim("[Y/n]" if default else "[y/N]")
    raw = _input(f"{cyan('?')} {bold(label)} {hint} ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# --- spinner ----------------------------------------------------------------

def live() -> bool:
    """True when we can animate in place (stdout is a TTY)."""
    return sys.stdout.isatty()


def clear_line():
    if live():
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()
