# repipe

Trigger a CI pipeline on demand and **auto-retry it when it fails with a transient error** — from one word on the command line.

- **Zero dependencies.** A single Python 3 stdlib script. No `pip`, no `jq`.
- **Curl-installable.** One line onto your `PATH`.
- **Provider-agnostic core.** Bitbucket Cloud today; GitHub Actions / GitLab CI are drop-in adapters later.

> **Status: under construction.** Shipping in phases. Done: Phase 0 (scaffold + installer) and Phase 1 (provider abstraction + Bitbucket reads — `list` / `status` / `logs`). Coming: `run` (trigger + auto-retry), the interactive flow, `init`, `rerun`.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/<abhay>/repipe/main/install.sh | bash
```

This installs `repipe` into `~/.local/bin` (or `/usr/local/bin`) and checks for `python3`. If the install dir isn't on your `PATH`, the installer prints the `export PATH=…` line to add.

To install from a local clone (before the repo is published):

```bash
git clone <this-repo> && cd repipe
bash install.sh
```

## Authentication

repipe uses a **Bitbucket Access Token** (a machine credential bound to a repo/workspace, not a person) sent as `Authorization: Bearer`.

1. Create a token with **Pipelines: read + write** — Repo settings → *Access tokens*, or Workspace settings → *Access tokens*.
   <https://support.atlassian.com/bitbucket-cloud/docs/access-tokens/>
2. Export it (add to `~/.zshrc` to persist):
   ```bash
   export REPIPE_TOKEN=<your-access-token>
   ```

Never commit tokens. `config.toml` is for non-secret defaults only.

## Usage

```bash
repipe version      # print version                     ✅ available
repipe --help       # full command list                 ✅ available
repipe list         # list runnable pipelines           ⏳ phase 1
repipe status <id>  # state of a run                     ⏳ phase 1
repipe              # interactive trigger + retry        ⏳ phase 4
```

_Full usage and examples for `supply-core-new` will be filled in as phases land._

## Configuration

Copy [`config.example.toml`](./config.example.toml) to `~/.config/repipe/config.toml`. It holds **non-secret** global defaults (retry patterns, email, max retries) and per-repo defaults (provider, default project, branch prefixes). Everything is optional.

## Project layout

The source is a small package under `src/`; the distributable `repipe` is a
single executable built from it with the stdlib `zipapp` module — so `curl |
bash` still installs one file.

```
src/
  __main__.py            zipapp entry (propagates exit code)
  repipe/
    __init__.py          version + package map
    errors.py            RepipeError + exit codes
    model.py             normalized Run / Step / Target / Variable / RunState
    gitutil.py           git remote → host/workspace/repo, branch
    ymlparse.py          bitbucket-pipelines.yml subset parser
    http.py              auth + urllib helpers (providers only)
    output.py            formatting helpers
    cli.py               argparse + command handlers + main()
    providers/
      registry.py        host-keyed registry + choose_provider
      base.py            the Provider interface
      bitbucket.py       Bitbucket Cloud adapter
build.sh                 python3 -m zipapp src -o repipe
repipe                   ← built artifact (committed, curl-installable)
```

## Development

No build needed to test — run the package directly:

```bash
python3 src list --path /path/to/some/repo
python3 src status <build-number>
```

To produce the distributable and refresh the installed copy:

```bash
bash build.sh        # writes ./repipe (zipapp)
bash install.sh      # copies ./repipe onto your PATH
```

## Adding a CI provider

repipe's core is provider-agnostic. To support a new host (GitHub Actions,
GitLab CI, …):

1. Implement the `Provider` interface in `src/repipe/providers/<host>.py` —
   `parse_targets`, `get_run`, `get_steps`, `get_step_log` (and, from Phase 2,
   `trigger`/`poll`).
2. Decorate the class with `@register_provider` and set `NAME`, `HOSTS`,
   `TARGET_WORD`.
3. Import it in `providers/__init__.py` so registration runs.

The retry engine, config, and exit-code contract are untouched.

## License

[MIT](./LICENSE)
