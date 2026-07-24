<!--
  Thanks for contributing to repipe! Keep the two hard constraints in mind:
    • Zero runtime dependencies — Python 3 stdlib only (no pip, no jq).
    • Provider-agnostic core — adapters stay behind the provider interface.
-->

## What & why

<!-- What does this change do, and why? Link any related issue: "Closes #123". -->

## Type of change

- [ ] 🐛 Bug fix
- [ ] ✨ New feature
- [ ] ♻️ Refactor / internal cleanup
- [ ] 📘 Docs / website
- [ ] 🔧 Build / CI / tooling

## Checklist

- [ ] Tests pass locally: `python3 -m unittest discover -s tests -t .`
- [ ] No new runtime dependencies (stdlib only).
- [ ] If I changed anything under `src/`, I **rebuilt the zipapp** (`bash build.sh`) and committed the updated `repipe` in the same PR. <!-- CI diffs the committed zipapp against a fresh build on every PR. -->
- [ ] `./repipe version` runs after the build.
- [ ] Docs updated if user-facing behavior changed (`README.md` and/or `docs/index.html`).
- [ ] Commit messages omit the `Co-Authored-By: Claude` trailer.

## Notes for reviewers

<!-- Anything worth calling out: tradeoffs, follow-ups, areas you're unsure about. -->
