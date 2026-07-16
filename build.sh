#!/usr/bin/env bash
#
# Build the distributable `repipe` from the src/ package using stdlib zipapp.
# Produces a single executable file (a zip with a python3 shebang) at repo root.
# Commit the built `repipe` so `install.sh` / the curl one-liner can fetch it.
#
# The build is REPRODUCIBLE: given the same src/, it emits byte-identical output
# on any machine, so CI can enforce "committed artifact matches src". zipapp
# already sorts entries and stores them uncompressed; the two remaining sources
# of nondeterminism are file mtimes and the timezone used to derive the embedded
# DOS timestamps — we pin both (SOURCE_DATE_EPOCH + TZ=UTC).
#
# Dev tip: you don't need to build to test — run the package directly:
#   python3 src <args>        # e.g. python3 src list --path /path/to/repo

set -euo pipefail
cd "$(dirname "$0")"

# Fixed default epoch (2023-11-14) so the archive is stable across builds.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1700000000}"
export TZ=UTC

# Don't let stale bytecode leak into the archive.
find src -name '__pycache__' -type d -prune -exec rm -rf {} +

# Normalize mtimes (portable via os.utime — BSD/GNU `touch` differ on @epoch).
python3 - "$SOURCE_DATE_EPOCH" <<'PY'
import os, sys
epoch = int(sys.argv[1])
for root, dirs, files in os.walk("src"):
    for name in files:
        os.utime(os.path.join(root, name), (epoch, epoch))
    os.utime(root, (epoch, epoch))
PY

python3 -m zipapp src \
  --output repipe \
  --python '/usr/bin/env python3'
chmod +x repipe

echo "built repipe (zipapp) → $(pwd)/repipe"
./repipe version
