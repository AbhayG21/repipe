#!/usr/bin/env bash
#
# Build the distributable `repipe` from the src/ package using stdlib zipapp.
# Produces a single executable file (a zip with a python3 shebang) at repo root.
# Commit the built `repipe` so `install.sh` / the curl one-liner can fetch it.
#
# Dev tip: you don't need to build to test — run the package directly:
#   python3 src <args>        # e.g. python3 src list --path /path/to/repo

set -euo pipefail
cd "$(dirname "$0")"

# Don't let stale bytecode leak into the archive.
find src -name '__pycache__' -type d -prune -exec rm -rf {} +

python3 -m zipapp src \
  --output repipe \
  --python '/usr/bin/env python3'
chmod +x repipe

echo "built repipe (zipapp) → $(pwd)/repipe"
./repipe version
