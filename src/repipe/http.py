"""Auth resolution + urllib HTTP helpers. Only providers import these."""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import config
from .errors import RepipeError, EXIT_CONFIG

_CRED_KEYS = ("REPIPE_TOKEN", "GITHUB_TOKEN", "REPIPE_EMAIL", "REPIPE_API_TOKEN",
              "REPIPE_NOTIFY_TOKEN")


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
        """Return (value, source) — env wins over the file."""
        v = os.environ.get(key)
        if v:
            return v, "env"
        v = creds.get(key)
        if v:
            return v, "file"
        return None, None

    token, tok_src = pick("REPIPE_TOKEN")
    tok_key = "REPIPE_TOKEN"
    if not token:
        token, tok_src = pick("GITHUB_TOKEN")
        tok_key = "GITHUB_TOKEN"
    email, email_src = pick("REPIPE_EMAIL")
    api_token, _ = pick("REPIPE_API_TOKEN")
    has_basic = bool(email and api_token)

    if token:
        # Surface the ambiguity that silently bit before: both a Bearer token
        # and the Basic pair are present, but Bearer wins.
        if has_basic:
            print(
                f"repipe: authenticating with {tok_key} ({tok_src}); ignoring the "
                f"REPIPE_EMAIL + REPIPE_API_TOKEN pair ({email_src}). "
                f"Unset {tok_key} to use that pair instead.",
                file=sys.stderr,
            )
        return ("bearer", token)
    if has_basic:
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


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url or "").hostname or ""
    except ValueError:
        return ""


def _scope_hint(host: str) -> str:
    if "bitbucket" in host:
        return ("the API token needs read/write:pipeline:bitbucket "
                "(and read:repository:bitbucket), or use a Bitbucket Access token")
    if "github" in host:
        return "the token needs the actions:write scope"
    return "the token is missing a required scope"


def _raise_http(e: urllib.error.HTTPError):
    host = _host_of(getattr(e, "url", "") or getattr(e, "filename", ""))
    where = host or "the server"
    if e.code == 401:
        raise RepipeError(
            f"authentication failed (401): {where} rejected the credentials — "
            "check they're valid and for this host. Note: environment variables "
            f"override {credentials_path()}.",
            EXIT_CONFIG,
        )
    if e.code == 403:
        raise RepipeError(
            f"authorization failed (403): authenticated to {where}, but "
            f"{_scope_hint(host)}.",
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
