#!/usr/bin/env bash
#
# repipe installer.
#
#   Remote (the published one-liner):
#     curl -fsSL https://raw.githubusercontent.com/AbhayG21/repipe/main/install.sh | bash
#
#   Local (testing before the repo is published):
#     bash install.sh          # copies the ./repipe sitting next to this script
#
# What it does:
#   1. Picks an install dir on PATH (prefers ~/.local/bin, else /usr/local/bin).
#   2. Installs the `repipe` script there and marks it executable.
#   3. Verifies python3 is present.
#   4. Prints how to set REPIPE_TOKEN, and a PATH hint if the dir isn't on PATH.

set -euo pipefail

# --- config: change this to the real repo before publishing -----------------
REPO="${REPIPE_REPO:-AbhayG21/repipe}"
REF="${REPIPE_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/${REPO}/${REF}"
# ----------------------------------------------------------------------------

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { printf '  %s\n' "$1"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '\033[33m!\033[0m %s\n' "$1"; }
err()  { printf '\033[31m✗\033[0m %s\n' "$1" >&2; }

# 1. python3 must exist (repipe is a python3 stdlib script).
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 was not found on PATH."
  info "repipe needs Python 3.8+. Install it, then re-run this installer:"
  info "  macOS:  brew install python   (or https://www.python.org/downloads/)"
  info "  Linux:  use your package manager (apt/dnf/…) to install python3"
  exit 1
fi

# 2. Choose an install directory.
choose_dir() {
  if [ -n "${REPIPE_BIN:-}" ]; then
    echo "$REPIPE_BIN"; return
  fi
  # Prefer ~/.local/bin (no sudo, standard user bin).
  echo "$HOME/.local/bin"
}

INSTALL_DIR="$(choose_dir)"
if ! mkdir -p "$INSTALL_DIR" 2>/dev/null; then
  # Fall back to /usr/local/bin if we somehow can't create ~/.local/bin.
  INSTALL_DIR="/usr/local/bin"
  warn "Falling back to $INSTALL_DIR"
fi
DEST="$INSTALL_DIR/repipe"

# 3. Get the repipe script — local copy if present, otherwise download.
#    BASH_SOURCE[0] is a real file for `bash install.sh`, but not when piped
#    from curl, so this cleanly distinguishes the two modes.
SRC_LOCAL=""
if [ -n "${BASH_SOURCE:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -f "$script_dir/repipe" ]; then
    SRC_LOCAL="$script_dir/repipe"
  fi
fi

if [ -n "$SRC_LOCAL" ]; then
  bold "Installing repipe (local copy)"
  info "from $SRC_LOCAL"
  cp "$SRC_LOCAL" "$DEST"
else
  # Remote: pull the binary from the latest GitHub Release (the counted, stable
  # channel). Pin a specific version with REPIPE_VERSION=v1.6.0. If the release
  # download fails (e.g. none published yet), fall back to raw main.
  if [ -n "${REPIPE_VERSION:-}" ]; then
    ASSET_URL="https://github.com/${REPO}/releases/download/${REPIPE_VERSION}/repipe"
  else
    ASSET_URL="https://github.com/${REPO}/releases/latest/download/repipe"
  fi
  bold "Installing repipe"
  info "from $ASSET_URL"
  if ! curl -fsSL "$ASSET_URL" -o "$DEST"; then
    warn "release download failed — falling back to $RAW_BASE/repipe"
    if ! curl -fsSL "$RAW_BASE/repipe" -o "$DEST"; then
      err "Download failed from the release and $RAW_BASE/repipe"
      info "Check the REPO at the top of this script, or your network."
      exit 1
    fi
  fi
fi

chmod +x "$DEST"
ok "repipe → $DEST"

# 4. Sanity check the freshly installed script.
if ! "$DEST" version >/dev/null 2>&1; then
  err "Installed but `repipe version` did not run cleanly."
  exit 1
fi
ok "$("$DEST" version)"

# 5. PATH hint.
case ":$PATH:" in
  *":$INSTALL_DIR:"*) : ;;  # already on PATH
  *)
    warn "$INSTALL_DIR is not on your PATH."
    info "Add this line to your shell profile (~/.zshrc or ~/.bashrc):"
    info "  export PATH=\"$INSTALL_DIR:\$PATH\""
    info "Then restart your shell (or: source ~/.zshrc)."
    ;;
esac

# 6. Next steps.
echo
bold "Next steps — set up a Bitbucket credential (pick ONE)"
info "A) Atlassian API token — no admin needed (recommended):"
info "   Create at https://id.atlassian.com/manage-profile/security/api-tokens"
info "   with scopes: read:pipeline:bitbucket, write:pipeline:bitbucket,"
info "   read:repository:bitbucket. Then export:"
info "     export REPIPE_EMAIL=<your-atlassian-email>"
info "     export REPIPE_API_TOKEN=<the-token>"
info ""
info "B) Bitbucket Access Token — needs repo/workspace admin:"
info "   Repo/Workspace settings → Access tokens (Pipelines read+write):"
info "   https://support.atlassian.com/bitbucket-cloud/docs/access-tokens/"
info "     export REPIPE_TOKEN=<the-token>"
info ""
info "Tip: add the export(s) to ~/.zshrc to persist across shells."
info "Then, from inside a Bitbucket repo, run:  repipe"
