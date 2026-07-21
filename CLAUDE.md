# repipe — project rules for Claude

Repo-specific rules and locked decisions. Read alongside `HANDOFF.md` (status +
architecture) and the per-project memory.

## Release checklist — do ALL of these before cutting a release

A release is not just a version bump. **The README and the website are part of
the release, not an afterthought** — a release that changes user-facing behavior
without updating both is incomplete. Before pushing/tagging any release:

1. Bump `__version__` in `src/repipe/__init__.py` (once per release, not per commit).
2. **Update `README.md`** to reflect the new/changed functionality.
3. **Update the website `docs/index.html`** — feature copy **and** the footer version.
4. Update `HANDOFF.md` (version line + a note on what shipped).
5. Rebuild the zipapp: `bash build.sh`; confirm `./repipe version`.
6. Run tests: `python3 -m unittest discover -s tests -t .` (all green).
7. Commit (see conventions below), push to `main`.
8. Cut the GitHub Release + tag `vX.Y.Z`, attach the `repipe` asset, mark it Latest.
   (`install.sh` / `repipe upgrade` pull from **Releases**, not `/main` — so
   pushing to main alone does not distribute anything.)

## Locked conventions

- Commit messages **OMIT** the `Co-Authored-By: Claude` trailer.
- **Zero runtime dependencies** — Python 3 stdlib only. No pip, no jq.
- Distribution is **release-based** (GitHub Releases); the release asset is named `repipe`.
- `HANDOFF.md` is **git-ignored** (a local reference doc) — don't try to commit it.
