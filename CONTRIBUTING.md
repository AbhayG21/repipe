# Contributing to repipe

Thanks for your interest in improving repipe! This guide covers everything you
need to make a change that lands cleanly.

## Two hard constraints

repipe has two rules that shape almost every decision. A change that violates
either will not be merged:

1. **Zero runtime dependencies.** repipe is a single Python 3 **stdlib-only**
   script. No `pip`, no `jq`, no vendored packages. If you reach for a
   third-party library, there's a stdlib way to do it — or the feature needs a
   different design.
2. **Provider-agnostic core.** CI providers (Bitbucket Cloud, GitHub Actions,
   …) live behind an adapter interface in `src/repipe/providers/`. Core logic
   must never special-case a provider inline — add or extend an adapter instead.

## Requirements

- **Python 3.11+** (3.11 is the floor so `tomllib` is available; CI runs 3.12).
- **macOS or Linux.** The committed zipapp is built on macOS.
- Git. That's it — no dependency install step, because there are no dependencies.

## Project layout

```
src/repipe/            # the package (core logic, CLI)
src/repipe/providers/  # per-provider adapters (Bitbucket, GitHub Actions)
src/__main__.py        # zipapp entry point
tests/                 # stdlib unittest suite (no network, no secrets)
build.sh               # builds the reproducible `repipe` zipapp from src/
repipe                 # the committed, built zipapp artifact (see below!)
docs/index.html        # the website
```

## Development workflow

1. **Fork & branch.** Branch off `main`; never commit to `main` directly.

2. **Make your change in `src/`.** Edit the package, not the built `repipe`
   artifact.

3. **Run the tests** — they use only the stdlib `unittest` runner, no network,
   no credentials:

   ```bash
   python3 -m unittest discover -s tests -t .
   ```

   Add or update tests for any behavior change. New provider adapters and
   parsing logic especially should come with tests.

4. **Rebuild the zipapp — this step trips people up.** The committed `repipe`
   file at the repo root is a built artifact, and **CI diffs it against a fresh
   reproducible build on every PR**. If you touched anything under `src/`, you
   must rebuild and commit the result *in the same PR*:

   ```bash
   bash build.sh
   ./repipe version      # smoke-test the built binary
   ```

   `build.sh` pins mtimes and `TZ` so the output is byte-stable. If you forget
   this step, the `build-determinism` CI job goes red.

5. **Update docs if behavior changed.** User-facing changes must update both
   `README.md` and the website (`docs/index.html`). A behavior change without
   docs is incomplete.

## Commit & PR conventions

- Keep commits focused; write clear, imperative subject lines
  (e.g. "Add GitLab CI adapter", not "changes").
- **Do not** add `Co-Authored-By:` or "Generated with …" trailers.
- Fill out the pull request template — its checklist mirrors the steps above.
- Link the issue your PR addresses (`Closes #123`).

## Adding a new CI provider adapter

1. Add `src/repipe/providers/<name>.py` implementing the provider interface
   (model it on the existing `bitbucket` / `github_actions` adapters).
2. Wire it into provider selection.
3. Add tests under `tests/`.
4. Document it in `README.md` and `docs/index.html`.
5. `bash build.sh` and commit the updated `repipe`.

## Reporting bugs & requesting features

Use the issue templates — pick **Bug report** or **Feature request** when you
open a new issue. For questions, check the
[docs](https://abhayg21.github.io/repipe/) first.

## Security

Please **do not** open a public issue for security vulnerabilities. See
[SECURITY.md](./SECURITY.md) for how to report them privately.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](./LICENSE).
