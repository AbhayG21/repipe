# repipe

Trigger a CI pipeline on demand and **auto-retry it when it fails with a transient error** — from one word on the command line.

- **Zero dependencies.** A single Python 3 stdlib script. No `pip`, no `jq`.
- **Curl-installable.** One line onto your `PATH`.
- **Provider-agnostic core.** Bitbucket Cloud today; GitHub Actions / GitLab CI are drop-in adapters later.

> **Status: under construction.** Shipping in phases. Done: Phase 0 (scaffold + installer) and Phase 1 (provider abstraction + Bitbucket reads — `list` / `status` / `logs`). Coming: `run` (trigger + auto-retry), the interactive flow, `init`, `rerun`.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AbhayG21/repipe/main/install.sh | bash
```

This installs `repipe` into `~/.local/bin` (or `/usr/local/bin`) and checks for `python3`. If the install dir isn't on your `PATH`, the installer prints the `export PATH=…` line to add.

To install from a local clone (before the repo is published):

```bash
git clone <this-repo> && cd repipe
bash install.sh
```

## Authentication

repipe needs a Bitbucket credential. There are two supported kinds — pick whichever you can create. **The token never goes in a file** — you set it as an environment variable and repipe reads it at runtime.

### Option A — Atlassian API token (works without admin) ✅ verified

Any Bitbucket user can create this for their own account; no repo/workspace admin needed. It authenticates via HTTP **Basic** auth (email + token). This is what we used to run the first live QA pipeline.

1. Go to **<https://id.atlassian.com/manage-profile/security/api-tokens>** → **Create API token with scopes**.
2. Select the Bitbucket scopes:
   - `read:pipeline:bitbucket` — read runs/steps/logs (`status`, `logs`)
   - `write:pipeline:bitbucket` — start runs (`run`)
   - `read:repository:bitbucket` — so run/repo lookups resolve cleanly
3. Export your **email** and the token (repipe falls back to Basic auth when `REPIPE_TOKEN` is unset):
   ```bash
   export REPIPE_EMAIL="you@yourcompany.com"
   export REPIPE_API_TOKEN="<the-api-token>"
   ```

### Option B — Bitbucket Access token (needs admin)

A machine credential bound to a repo/workspace (not a person), sent as `Authorization: Bearer`. Preferred for shared automation, but creating one requires admin on the repo/workspace.

1. Repo settings → *Access tokens* (or Workspace settings → *Access tokens*), scopes **Pipelines: read + write**.
   <https://support.atlassian.com/bitbucket-cloud/docs/access-tokens/>
2. Export it:
   ```bash
   export REPIPE_TOKEN="<your-access-token>"
   ```

> **App passwords are not supported** — they're end-of-life. Use one of the above.

### Verify your credential

Confirm auth without triggering anything — a read-only call:

```bash
cd /path/to/your/repo-clone
repipe status <a-recent-build-number>
```

If it prints a real state, you're set. Common HTTP codes if you debug with `curl`: `200` = good; `401` = token rejected (wrong type/value — e.g. an API token used as Bearer); `403` = authenticated but missing a scope; `404` on the repo root usually means no `read:repository` scope (harmless — repipe only calls `/pipelines/…`).

Never commit tokens. `config.toml` is for non-secret defaults only, and `.gitignore` covers any `credentials` file.

## Usage

```bash
repipe version                          # print version                    ✅
repipe --help                           # full command list                ✅
repipe list                             # list runnable pipelines           ✅
repipe status <uuid|build#>             # state of a run                    ✅
repipe logs   <uuid|build#> [--all]     # step logs of a run                ✅
repipe run -p <PIPELINE> -b <branch>    # trigger a run (--dry-run to preview)  ✅
repipe run ... --var KEY=VALUE          # set pipeline variables (repeatable)   ✅
repipe                                  # interactive trigger + retry       ⏳ phase 4
```

`run` triggers today; **watch + auto-retry** land in phase 3. Example against `supply-core-new`:

```bash
# preview the exact API request without sending it
repipe run -p BUILD_AND_DEPLOY_SUPPLY_CORE_NEW_QA -b qa-release-29-May --dry-run

# trigger for real → prints build number + web URL
repipe run -p BUILD_AND_DEPLOY_SUPPLY_CORE_NEW_QA -b qa-release-29-May
```

Bad or missing variables are caught locally in ~1s (before any API call), e.g. `Project` must be `PCI`/`NON-PCI`, and `MULTI=false` forbids space-separated `FLAVOURS`.

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
