"""Terminal UI: colors, an arrow-key selector, prompts, and a poll spinner.

Zero-dependency (ANSI + termios/tty from the stdlib). Everything degrades
gracefully: colors switch off when stdout isn't a TTY (or NO_COLOR is set),
and the selector falls back to a numbered input prompt when stdin isn't a TTY
— so piped/CI use and the plain prompt path keep working unchanged.
"""

import os
import sys

from .errors import RepipeError, EXIT_CONFIG

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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


def _numbered(label, items, default_idx, to_str):
    for i, it in enumerate(items):
        marker = cyan("→") if i == default_idx else " "
        print(f"  {marker} {dim(str(i + 1) + ')')} {to_str(it)}")
    while True:
        raw = _input(f"{cyan('?')} {bold(label)} [{default_idx + 1}]: ").strip()
        if not raw:
            return items[default_idx]
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print(f"  enter a number 1–{len(items)}.")


def pick(label, items, default_idx=0, to_str=str):
    """Arrow-key selector (↑/↓/Enter, digit to jump-select). Returns the chosen
    item. Falls back to a numbered prompt when stdin isn't a TTY."""
    if not items:
        raise RepipeError(f"nothing to choose for '{label}'.", EXIT_CONFIG)
    default_idx = max(0, min(default_idx, len(items) - 1))
    if not _can_raw():
        return _numbered(label, items, default_idx, to_str)

    import termios
    import tty

    idx = default_idx
    n = len(items)
    print(f"{cyan('?')} {bold(label)} {dim('(↑/↓ · Enter)')}")

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
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x03":  # Ctrl-C
                raise KeyboardInterrupt
            if ch == "\x1b":  # arrow escape sequence
                seq = sys.stdin.read(2)
                if seq == "[A":
                    idx = (idx - 1) % n
                elif seq == "[B":
                    idx = (idx + 1) % n
                else:
                    continue
            elif ch in ("\r", "\n"):
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
    sys.stdout.write("\n")
    return items[idx]


def ask(label, default=None) -> str:
    suffix = dim(f" [{default}]") if default else ""
    raw = _input(f"{cyan('?')} {bold(label)}{suffix}: ").strip()
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
