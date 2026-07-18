"""Best-effort notifications — a local desktop channel and a remote phone channel.

Zero-dependency: the local channel shells out to the platform's native notifier
and degrades to the terminal bell; the remote channel does one stdlib urllib POST
to ntfy. Every path is wrapped so a notification failure can NEVER affect the
watch loop's behavior or exit code.

Local (`notify`):
- macOS  → osascript `display notification` (default sound only when sound=True)
- Linux  → notify-send, if it's installed (desktop only; absent on servers)
- else   → BEL (\\a) to stderr: rings the bell / flags the tab in most terminals

Remote (`push`): POST to an ntfy topic URL. Unlike the local channel this has no
TTY requirement — it's the channel for a headless box (a VM you've walked away
from), where the point is to reach your phone precisely because there's no
terminal to watch.
"""

import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request


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


_PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "max": 5}


def push(url, title, message, priority="default", tags="", click="", token=None):
    """POST a notification to an ntfy topic URL, best-effort. Never raises — a
    failed push (bad URL, offline, timeout) is swallowed exactly like the local
    channel, so it can't affect the watch loop or exit code.

    Uses ntfy's JSON publishing (POST the topic in a JSON body to the server
    root) rather than the header-based API: header values are latin-1 and ntfy
    reads them as UTF-8, which mojibakes a `·` or emoji in the title. A JSON body
    is UTF-8 end-to-end, so titles/messages render correctly.

    - message  = notification body (keeps ✓/✗/… glyphs)
    - title    = notification title (full UTF-8)
    - priority = ntfy priority, name (min/low/default/high/max) or int 1–5
    - tags     = comma-separated ntfy emoji shortcodes (e.g. "white_check_mark")
    - click    = URL opened when the phone notification is tapped
    - token    = optional Bearer token (reserved / self-hosted / protected topics)
    """
    if not url:
        return
    try:
        parts = urllib.parse.urlsplit(url)
        topic = parts.path.strip("/")
        if not topic:
            return  # no topic segment → nothing to publish to
        root = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        payload = {"topic": topic, "message": str(message)}
        if title:
            payload["title"] = str(title)
        pr = _PRIORITY.get(priority, priority)
        if isinstance(pr, int):
            payload["priority"] = pr
        taglist = [t.strip() for t in str(tags).split(",") if t.strip()]
        if taglist:
            payload["tags"] = taglist
        if click:
            payload["click"] = str(click)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + str(token)
        req = urllib.request.Request(
            root,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass  # remote push is strictly best-effort — never surface to the loop
