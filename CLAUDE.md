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
4. **Update `CHANGELOG.md`** — add a dated `## [X.Y.Z]` section with the
   user-facing changes, and refresh the compare links at the bottom.
5. Update `HANDOFF.md` (version line + a note on what shipped).
6. Rebuild the zipapp: `bash build.sh`; confirm `./repipe version`.
7. Run tests: `python3 -m unittest discover -s tests -t .` (all green).
8. Commit (see conventions below), push to `main`.
9. Cut the GitHub Release + tag `vX.Y.Z`, attach the `repipe` asset, mark it Latest.
   (`install.sh` / `repipe upgrade` pull from **Releases**, not `/main` — so
   pushing to main alone does not distribute anything.)
10. **Update the Homebrew tap** (`AbhayG21/homebrew-tap`, `Formula/repipe.rb`):
   bump the `url` to the new `vX.Y.Z` and the `sha256` (`shasum -a 256 repipe`),
   commit + push. That's how `brew upgrade repipe` gets the new version — the tap
   does not auto-follow releases.

## Locked conventions

- Commit messages **OMIT** the `Co-Authored-By: Claude` trailer.
- **Zero runtime dependencies** — Python 3 stdlib only. No pip, no jq.
- Distribution is **release-based** (GitHub Releases); the release asset is named `repipe`.
- `HANDOFF.md` is **git-ignored** (a local reference doc) — don't try to commit it.
