# repipe

Trigger a CI pipeline on demand and **auto-retry it when it fails with a transient error** — from one word on the command line.

- **Zero dependencies.** A single Python 3 stdlib script. No `pip`, no `jq`.
- **Curl-installable.** One line onto your `PATH`.
- **Provider-agnostic core.** Ships with **Bitbucket Cloud** and **GitHub Actions** adapters; GitLab CI is a drop-in adapter later.

> **📘 Docs & showcase → [abhayg21.github.io/repipe](https://abhayg21.github.io/repipe/)**
>
> Everything's shipped: `run` (trigger + watch + auto-retry), the interactive flow, `init` / `rerun` / `list` / `status` / `logs` / `suggestions` / `upgrade`, with Bitbucket Cloud and GitHub Actions adapters.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/AbhayG21/repipe/main/install.sh | bash
```

This installs `repipe` into `~/.local/bin` (or `/usr/local/bin`) and checks for `python3`. If the install dir isn't on your `PATH`, the installer prints the `export PATH=…` line to add. The binary is pulled from the **latest [GitHub Release](https://github.com/AbhayG21/repipe/releases)** — pin a specific one with `REPIPE_VERSION=v1.6.0` before the pipe.

To install from a local clone:

```bash
git clone <this-repo> && cd repipe
bash install.sh          # uses the built ./repipe next to the script
```

## Upgrading

```bash
repipe upgrade            # fetch the latest release and replace itself
repipe upgrade --check    # just report installed vs latest release
```

Upgrades track the latest **GitHub Release** (not `main`), read straight from the Releases API — so `--check` is always current, with no CDN lag. Pin an exact version with `REPIPE_UPGRADE_VERSION=v1.6.0 repipe upgrade`.

Your config (`~/.config/repipe/config.toml`) and credentials are untouched — only the binary is swapped (atomically, after verifying the download runs). Re-running the install one-liner does the same thing.

## Authentication

repipe needs a credential for your CI host, set as an environment variable (never in a file) and read at runtime.

**GitHub Actions:** a token with `actions:write` (a fine-grained or classic PAT, or the `GITHUB_TOKEN` inside CI). repipe reads `REPIPE_TOKEN` or `GITHUB_TOKEN` and sends it as a Bearer token:

```bash
export REPIPE_TOKEN="<your-github-token>"   # or rely on GITHUB_TOKEN in CI
```

**Bitbucket Cloud** has two supported kinds — pick whichever you can create.

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

### Credentials file (set it once)

Rather than re-exporting every shell, drop your token in `~/.config/repipe/credentials` — a simple `KEY=VALUE` file using the same names as the env vars. repipe reads it as a fallback; **environment variables always win** (so CI's `GITHUB_TOKEN` or an ad-hoc `export` still overrides it).

```bash
install -m 600 /dev/null ~/.config/repipe/credentials   # create it locked-down
cat > ~/.config/repipe/credentials <<'EOF'
REPIPE_TOKEN=ghp_your_github_or_bitbucket_token
# …or, for Bitbucket's API-token pair:
# REPIPE_EMAIL=you@yourcompany.com
# REPIPE_API_TOKEN=your_api_token
EOF
```

`chmod 600` it (repipe warns if it's readable by others). It's already covered by `.gitignore`.

Or let repipe write it for you — `repipe login` prompts for the token with hidden input and saves the file `0o600`. Add `--verify` (inside a repo) to confirm the token works with one read-only API call before saving.

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
repipe                                  # interactive: pick pipeline/env/branch/vars, trigger, watch, retry
repipe login [--verify]                 # save a CI token to the credentials file (hidden input)
repipe config [--show]                  # view/edit settings via a menu (--show just prints them)
repipe init                             # scaffold ~/.config/repipe/config.toml from this repo
repipe suggestions                      # print suggested retry patterns to copy into config
repipe upgrade [--check]                # update to the latest published build (--check just reports)
repipe rerun                            # repeat the last invocation for this repo
repipe list                             # list runnable pipelines (offline, from the yml)
repipe status <uuid|build#>             # state of a run
repipe logs   <uuid|build#> [--all]     # step logs of a run
repipe run -p <PIPELINE> -b <branch>    # trigger + watch + auto-retry (scriptable)
repipe version | --help
```

### Interactive (the everyday command)

Run `repipe` with no arguments inside a repo. It discovers what it can and asks only for the rest: it lists the pipelines, infers QA vs prod from the name, offers the newest `qa-release*`/`prod-release*` branch (plus your current branch), and drives its variable prompts from the per-repo schema you configure — offering pickers for `enum` values, defaults, auto-filling variables from your git email, and remembering values you've entered before so it stops asking. Then it triggers, watches, and auto-retries.

### Scriptable (CI / Claude)

```bash
# preview the exact API request without sending it
repipe run -p deploy-qa -b qa-release-2024-05-29 --dry-run

# trigger, watch, and auto-retry on transient failures
repipe run -p deploy-qa -b qa-release-2024-05-29 \
  --retry-on "OutOfMemoryError" --max-retries 3 --yes
```

Bad or missing variables are caught locally in ~1s (before any API call), using the per-repo variable schema you define in config: `enum` values, `required` flags, regex `pattern`s, and a `no_spaces_unless` cross-field rule.

### Editing settings

`repipe config` opens an arrow-key menu (same UX as the interactive run flow) to view and change settings without hand-editing TOML. The top level covers the globals: retry patterns (add from suggestions, add custom, remove), max retries, match mode, poll interval, timeout, desktop notifications, per-step notifications, and your email. **Repo settings ›** edits the per-repo basics — provider, QA branch prefix, prod branch prefix — for the repo it detects from the working tree (or, if you're not in one, a repo you pick from those already configured). Under it, **Variables ›** is a full editor for the per-repo input schema: pick a variable (or add one) and set its `enum` (add/remove allowed values), `default`, `required`, `pattern`, `autofill`, `remember`, `no_spaces_unless`, and `hint` — the same table you'd otherwise hand-write. `repipe config --show` prints the current effective globals and exits (no terminal needed, so it works over a pipe / in CI).

### Auto-retry (opt-in — you define the patterns)

repipe ships **no** retry patterns and retries **nothing** by default. You decide which failures are worth re-triggering: list patterns in config `retry_on`, or pass `--retry-on` per run. On failure, repipe reads the failed step's log and re-triggers **only** if it matches one of *your* patterns; no match ⇒ it stops and surfaces the failure (never loops).

```bash
repipe suggestions          # a starter list of common transient errors to copy
```

Matching is case-insensitive substring by default (`--match regex` for regex). Exit codes: `0` success/halted-at-gate, `1` failed-no-match, `2` retries exhausted, `3` config/auth, `4` timeout.

### Production safety

Pipelines named `*_PROD`/`*CANARY*` are treated as production: triggering one **requires confirmation** (type the pipeline name, or `--yes` for CI), prod runs **do not auto-retry** unless you pass `--force`, and because the API can't resume a manual deploy gate, repipe reports **HALTED** as a clean stop with a deep-link to approve the deploy in the UI — it never hangs or retries a paused run.

### Notifications

While repipe is watching a run, it sends a local desktop notification when the run finishes — success, failure, halted-at-gate, or timeout — plus one on each auto-retry, so you can walk away from the terminal. It's **on by default in an interactive terminal** and auto-suppressed in CI / piped runs (nothing to pop up there). Sound plays only on the final result; retry pings are silent banners.

```bash
repipe run -p deploy-qa               # notifies on finish (default)
repipe run -p deploy-qa --no-notify   # stay silent (also mutes the terminal bell)
repipe run -p deploy-qa --notify-steps  # also ping as each step/job completes
```

Mechanism is zero-dependency and best-effort: macOS uses `osascript`, Linux uses `notify-send` when present, and anything else falls back to the terminal bell. A notification never affects the run's outcome or exit code. Set `notify` / `notify_steps` in config to make your choice the default.

#### Push to your phone (ntfy)

The desktop notification above needs a terminal — which is exactly what you *don't* have when you run repipe on an always-on box and close your laptop. For that, point `notify_url` at an [ntfy](https://ntfy.sh) topic and the finish notification (and each retry) is pushed to your **phone**. This channel is **independent of the TTY gate**: it fires precisely because there's no terminal watching. Tapping the banner opens the run.

```bash
repipe config                          # → "Phone push (ntfy)" → generate a topic → send a test
repipe run -p deploy-qa                # pushes on finish when notify_url is set
repipe run -p deploy-qa --no-phone-notify   # skip the phone for this run
```

Set it up: run `repipe config → Phone push (ntfy)` and pick **Generate a random ntfy.sh topic** — repipe mints a hard-to-guess topic for you (don't hand-pick a name; a guessable one is readable by anyone). Then install the ntfy app, subscribe to that topic, and use the built-in **send a test** to confirm it reaches your phone. It lands in config as:

```toml
notify_url = "https://ntfy.sh/repipe-<random>"
```

> **Use a long, random topic.** On the public ntfy.sh server anyone who knows the topic name can read your notifications — treat it like a password. Reserved or self-hosted topics that require auth: put the token in the credentials file as `REPIPE_NOTIFY_TOKEN` (env also works), never in `config.toml`. Like the local channel, a failed push never affects the run's outcome or exit code.

## Configuration

Copy [`config.example.toml`](./config.example.toml) to `~/.config/repipe/config.toml`. It holds **non-secret** global defaults (retry patterns, email, max retries, notifications), per-repo defaults (provider, branch prefixes), and a per-repo variable schema so you can design your own pipeline inputs (enums, defaults, required flags, patterns, autofill, remembered values). Everything is optional.

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
