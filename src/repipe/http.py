"""Auth resolution + urllib HTTP helpers. Only providers import these."""

import base64
import json
import os
import urllib.error
import urllib.request

from .errors import RepipeError, EXIT_CONFIG


def get_auth(required: bool = True):
    """Resolve credentials from the environment.

    Returns ("bearer", token) or ("basic", email, api_token), or None.
    Bearer covers a Bitbucket Access token OR a GitHub token; Basic (email +
    API token) is Bitbucket-only.
    """
    token = os.environ.get("REPIPE_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return ("bearer", token)
    email = os.environ.get("REPIPE_EMAIL")
    api_token = os.environ.get("REPIPE_API_TOKEN")
    if email and api_token:
        return ("basic", email, api_token)
    if required:
        raise RepipeError(
            "no credentials found. Set REPIPE_TOKEN (Bitbucket Access token, or "
            "a GitHub token / GITHUB_TOKEN with actions:write), or set "
            "REPIPE_EMAIL + REPIPE_API_TOKEN for Bitbucket.",
            EXIT_CONFIG,
        )
    return None


def _auth_header(auth) -> str:
    if auth[0] == "bearer":
        return "Bearer " + auth[1]
    raw = f"{auth[1]}:{auth[2]}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _headers(auth, base: dict, extra=None) -> dict:
    """Auth header + `base` defaults, with provider `extra` headers layered on."""
    h = {"Authorization": _auth_header(auth)}
    h.update(base)
    if extra:
        h.update(extra)
    return h


def _raise_http(e: urllib.error.HTTPError):
    if e.code in (401, 403):
        raise RepipeError(
            f"authentication failed ({e.code}). Check REPIPE_TOKEN and its "
            "Pipelines scopes.",
            EXIT_CONFIG,
        )
    if e.code == 404:
        raise RepipeError("not found (404) — check the id / repo.", EXIT_CONFIG)
    body = ""
    try:
        body = e.read().decode(errors="replace")[:400]
    except Exception:
        pass
    raise RepipeError(f"HTTP {e.code} from server: {body}", EXIT_CONFIG)


def api_get_json(url: str, auth, headers=None) -> dict:
    req = urllib.request.Request(
        url, headers=_headers(auth, {"Accept": "application/json"}, headers)
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        _raise_http(e)
    except urllib.error.URLError as e:
        raise RepipeError(f"network error: {e.reason}", EXIT_CONFIG)
    return json.loads(data.decode() or "{}")


def api_post_json(url: str, body: dict, auth, headers=None) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers=_headers(
            auth,
            {"Content-Type": "application/json", "Accept": "application/json"},
            headers,
        ),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        _raise_http(e)
    except urllib.error.URLError as e:
        raise RepipeError(f"network error: {e.reason}", EXIT_CONFIG)
    return {}


def download_bytes(url: str, timeout: int = 30) -> bytes:
    """GET raw bytes from a public URL (no auth) — used by `repipe upgrade`."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RepipeError(f"download failed: HTTP {e.code} for {url}", EXIT_CONFIG)
    except urllib.error.URLError as e:
        raise RepipeError(f"network error: {e.reason}", EXIT_CONFIG)


def download_text(url: str, timeout: int = 30) -> str:
    return download_bytes(url, timeout).decode(errors="replace")


def api_get_text(url: str, auth, headers=None) -> str:
    """GET raw text, following redirects, tolerating empty bodies."""
    req = urllib.request.Request(url, headers=_headers(auth, {}, headers))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""  # some steps have no log endpoint content
        _raise_http(e)
    except urllib.error.URLError as e:
        raise RepipeError(f"network error: {e.reason}", EXIT_CONFIG)
    return ""
