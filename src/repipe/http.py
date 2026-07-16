"""Auth resolution + urllib HTTP helpers. Only providers import these."""

import base64
import json
import os
import sys
import urllib.error
import urllib.request

from . import config
from .errors import RepipeError, EXIT_CONFIG

_CRED_KEYS = ("REPIPE_TOKEN", "GITHUB_TOKEN", "REPIPE_EMAIL", "REPIPE_API_TOKEN")


def credentials_path() -> str:
    return os.path.join(config.config_dir(), "credentials")


def _load_credentials_file() -> dict:
    """Read ~/.config/repipe/credentials — a dotenv-style KEY=VALUE file, the
    non-env fallback for tokens. Returns {} if it's absent or unreadable.

    Warns (never fails) if the file is group/other-readable, since it holds a
    live token; suggests `chmod 600`.
    """
    path = credentials_path()
    try:
        st = os.stat(path)
    except OSError:
        return {}
    if st.st_mode & 0o077:
        print(
            f"repipe: warning — {path} is readable by others; run "
            f"`chmod 600 {path}`.",
            file=sys.stderr,
        )
    creds = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in _CRED_KEYS and value:
                    creds[key] = value
    except OSError:
        return {}
    return creds


def get_auth(required: bool = True):
    """Resolve credentials: environment first, then ~/.config/repipe/credentials.

    Returns ("bearer", token) or ("basic", email, api_token), or None.
    Bearer covers a Bitbucket Access token OR a GitHub token; Basic (email +
    API token) is Bitbucket-only. The env always wins over the file, so CI's
    injected GITHUB_TOKEN or an ad-hoc export overrides a saved file.
    """
    creds = _load_credentials_file()

    def pick(key):
        return os.environ.get(key) or creds.get(key)

    token = pick("REPIPE_TOKEN") or pick("GITHUB_TOKEN")
    if token:
        return ("bearer", token)
    email = pick("REPIPE_EMAIL")
    api_token = pick("REPIPE_API_TOKEN")
    if email and api_token:
        return ("basic", email, api_token)
    if required:
        raise RepipeError(
            "no credentials found. Set REPIPE_TOKEN (Bitbucket Access token, or "
            "a GitHub token / GITHUB_TOKEN with actions:write), or set "
            "REPIPE_EMAIL + REPIPE_API_TOKEN for Bitbucket — as environment "
            f"variables or in {credentials_path()} (chmod 600).",
            EXIT_CONFIG,
        )
    return None


def save_credentials(mapping: dict) -> str:
    """Write the given credential vars to the credentials file, mode 0o600.

    Written atomically (temp file + rename) so a token never lands in a
    partially-written or world-readable file. Returns the path.
    """
    import tempfile

    os.makedirs(config.config_dir(), exist_ok=True)
    path = credentials_path()
    body = ["# repipe credentials — keep private (chmod 600). Never commit.\n"]
    for key in _CRED_KEYS:
        if mapping.get(key):
            body.append(f"{key}={mapping[key]}\n")

    fd, tmp = tempfile.mkstemp(dir=config.config_dir(), prefix=".cred-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(body))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def probe(url: str, auth, headers=None) -> int:
    """GET `url` with `auth` and return the HTTP status code (0 on network
    error). Unlike api_get_*, this never raises on 4xx/5xx — it's for
    verifying a credential (200 ok, 401 rejected, 403 missing scope)."""
    req = urllib.request.Request(
        url, headers=_headers(auth, {"Accept": "application/json"}, headers)
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return getattr(r, "status", 200) or 200
    except urllib.error.HTTPError as e:
        return e.code
    except urllib.error.URLError:
        return 0


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
