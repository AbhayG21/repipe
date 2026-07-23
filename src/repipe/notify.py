"""Best-effort notifications — a local desktop channel and remote phone channels.

Zero-dependency: the local channel shells out to the platform's native notifier
and degrades to the terminal bell; the remote channels do one stdlib urllib POST
each. Every path is wrapped so a notification failure can NEVER affect the watch
loop's behavior or exit code.

Local (`notify`):
- macOS  → osascript `display notification` (default sound only when sound=True)
- Linux  → notify-send, if it's installed (desktop only; absent on servers)
- else   → BEL (\\a) to stderr: rings the bell / flags the tab in most terminals

Remote (phone push): one POST per configured provider. Unlike the local channel
these have no TTY requirement — they're the channel for a headless box (a VM
you've walked away from), where the point is to reach your phone precisely because
there's no terminal to watch.

Providers are pluggable via `PUSH_PROVIDERS`: each entry names a config key holding
its URL and a sender attribute on this module. Adding a destination (Slack, …) is
one registry entry + one `push_*` function — the CLI menu, dispatch, and `doctor`
all iterate the registry, so no wiring changes elsewhere.
- ntfy        (`push`)        — a public/self-hosted topic, header-safe JSON publish
- Google Chat (`push_gchat`)  — a private "space of one" incoming webhook, cardsV2
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


def push_gchat(url, title, message, click="", **_):
    """POST a notification to a Google Chat incoming-webhook URL, best-effort.
    Never raises — a failed push is swallowed exactly like `push`, so it can't
    affect the watch loop or exit code.

    Personal push on Google Chat is a "space of one": you create a private space
    with no other members and add an incoming webhook to it, so a message posted
    here reaches only you (your phone's Chat app delivers it).

    Formats as a cardsV2 message — a header with the title, the status line as
    decorated text, and (when `click` is set) a button that opens the pipeline run.
    A top-level `text` summary is sent alongside the card: Google Chat builds the
    phone/desktop notification PREVIEW from `text`, so without it the banner just
    reads "…sent a notification" and you'd have to open the message to see what
    happened. Unlike ntfy there is no topic/priority/tags/token machinery: the
    webhook URL already carries its own `key`+`token` credential, so we POST it
    verbatim.

    Ignores extra keyword args (priority/tags/token) so the dispatch loop can call
    every provider's sender with one uniform argument set.
    """
    if not url:
        return
    try:
        widgets = [{"decoratedText": {"text": str(message)}}]
        if click:
            widgets.append({"buttonList": {"buttons": [
                {"text": "Open pipeline",
                 "onClick": {"openLink": {"url": str(click)}}},
            ]}})
        payload = {
            # Drives the notification preview (card-only → "sent a notification").
            "text": f"{title} — {message}" if title else str(message),
            "cardsV2": [{
                "cardId": "repipe",
                "card": {
                    "header": {"title": str(title or "repipe")},
                    "sections": [{"widgets": widgets}],
                },
            }],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass  # best-effort — never surface to the loop


def push_slack(url, title, message, click="", **_):
    """POST a notification to a Slack incoming-webhook URL, best-effort. Never
    raises — a failed push is swallowed exactly like `push`, so it can't affect
    the watch loop or exit code.

    Slack incoming webhooks post to a single channel; for personal push, point it
    at a private channel with only you in it (the Slack equivalent of Google
    Chat's "space of one").

    Formats as Block Kit — a section with the bold title and the status line, and
    (when `click` is set) an actions block with a button that opens the pipeline
    run. Like Google Chat, the webhook URL is the credential, so we POST it as-is
    (no priority/tags/token machinery). Ignores extra keyword args so the dispatch
    loop can call every provider's sender with one uniform argument set.
    """
    if not url:
        return
    try:
        text = f"*{title or 'repipe'}*\n{message}"
        blocks = [{"type": "section",
                   "text": {"type": "mrkdwn", "text": text}}]
        if click:
            blocks.append({"type": "actions", "elements": [
                {"type": "button",
                 "text": {"type": "plain_text", "text": "Open pipeline"},
                 "url": str(click)},
            ]})
        # `text` is a required top-level fallback (notifications / old clients).
        payload = {"text": f"{title or 'repipe'} — {message}", "blocks": blocks}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception:
        pass  # best-effort — never surface to the loop


# Pluggable phone-push providers. `config_key` is the config field holding the
# provider's URL/webhook; `send` is the name of this module's sender function
# (referenced by name, not object, so the CLI's dispatch/menu/doctor stay
# mockable and new providers register without touching call sites);
# `can_generate` marks providers whose URL the config menu can auto-generate
# (ntfy topics — the rest are pasted from the provider's own UI).
PUSH_PROVIDERS = [
    {"id": "ntfy", "label": "ntfy", "config_key": "notify_url",
     "can_generate": True, "send": "push"},
    {"id": "gchat", "label": "Google Chat", "config_key": "notify_gchat_url",
     "can_generate": False, "send": "push_gchat"},
    {"id": "slack", "label": "Slack", "config_key": "notify_slack_url",
     "can_generate": False, "send": "push_slack"},
]
