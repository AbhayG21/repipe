"""Best-effort local desktop notifications.

Zero-dependency: shells out to the platform's native notifier and degrades to the
terminal bell when there isn't one. Every path is wrapped so a notification
failure can NEVER affect the watch loop's behavior or exit code.

- macOS  → osascript `display notification` (default sound only when sound=True)
- Linux  → notify-send, if it's installed (desktop only; absent on servers)
- else   → BEL (\\a) to stderr: rings the bell / flags the tab in most terminals
"""

import shutil
import subprocess
import sys


def _applescript_str(s) -> str:
    """Escape a string for embedding inside an AppleScript double-quoted literal,
    so a branch/step name with quotes or backslashes can't break (or inject into)
    the script."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _bell():
    try:
        sys.stderr.write("\a")
        sys.stderr.flush()
    except Exception:
        pass


def notify(title, message, sound=False):
    """Show a local notification, best-effort. `sound` plays the default
    notification sound (macOS); the terminal-bell fallback always beeps since the
    bell is the only channel a GUI-less terminal has."""
    try:
        if sys.platform == "darwin":
            script = (
                f'display notification "{_applescript_str(message)}" '
                f'with title "{_applescript_str(title)}"'
            )
            if sound:
                script += ' sound name "default"'
            subprocess.run(
                ["osascript", "-e", script], capture_output=True, timeout=5
            )
            return
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", str(title), str(message)],
                capture_output=True,
                timeout=5,
            )
            return
    except Exception:
        pass  # fall through to the bell
    _bell()
