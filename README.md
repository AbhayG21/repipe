<p align="center">
  <img src="logo.png" alt="repipe logo" width="150">
</p>

<h1 align="center">repipe</h1>

Trigger a CI pipeline on demand and **auto-retry it when it fails with a transient error** — from one word on the command line.

- **Zero dependencies.** A single Python 3 stdlib script. No `pip`, no `jq`.
- **Curl-installable.** One line onto your `PATH`.
- **Provider-agnostic core.** Ships with **Bitbucket Cloud** and **GitHub Actions** adapters; GitLab CI is a drop-in adapter later.

> **📘 Docs & showcase → [abhayg21.github.io/repipe](https://abhayg21.github.io/repipe/)**
>
> Everything's shipped: `run` (trigger + watch + auto-retry), the interactive flow, `init` / `rerun` / `list` / `status` / `logs` / `suggestions` / `upgrade`, with Bitbucket Cloud and GitHub Actions adapters.

## Install

### Homebrew (macOS)

```bash
brew install abhayg21/tap/repipe
```

Pulls from the [tap](https://github.com/AbhayG21/homebrew-tap) and runs repipe through Homebrew's `python@3.12`. Update with `brew upgrade repipe`.

### curl (macOS / Linux)

```bash
curl -fsSL https://raw.githubusercontent.com/AbhayG21/repipe/main/install.sh | bash
```

This installs `repipe` into `~/.local/bin` (or `/usr/local/bin`) and checks for `python3`. If the install dir isn't on your `PATH`, the installer prints the `export PATH=…` line to add. The binary is pulled from the **latest [GitHub Release](https://github.com/AbhayG21/repipe/releases)** — pin a specific one with `REPIPE_VERSION=v1.6.0` before the pipe.

To install from a local clone:

```bash
git clone <this-repo> && cd repipe
bash install.sh          # uses the built ./repipe next to the script
```

### First-time setup

```bash
repipe login             # store a CI credential (see Authentication below)
cd your-repo && repipe init   # scaffold config for this repo (provider auto-detected)
repipe config            # optional: retry patterns, notifications, phone push
repipe doctor            # confirm creds, auth, config, and alerts are wired up
```

`repipe` reads/writes a single non-secret config at `~/.config/repipe/config.toml` (see [Configuration](#configuration)); credentials live separately (see [Authentication](#authentication)). Both survive upgrades.

## Upgrading

```bash
repipe upgrade            # fetch the latest release and replace itself
repipe upgrade --check    # just report installed vs latest release
```

Upgrades track the latest **GitHub Release** (not `main`), read straight from the Releases API — so `--check` is always current, with no CDN lag. Pin an exact version with `REPIPE_UPGRADE_VERSION=v1.6.0 repipe upgrade`.

You don't have to remember to check: the interactive **welcome screen** (bare `repipe`) shows a quiet `↑ repipe X.Y.Z is available` line when a newer release exists. The check is best-effort and **throttled to once a day** (cached in `update-check.json` in the config dir), so it never slows the CLI down, and it stays silent when offline. Opt out entirely with `REPIPE_NO_UPDATE_CHECK=1`.

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

Or let repipe write it for you — `repipe login` prompts for the token with hidden input and saves the file `0o600`. It **shows you exactly where to generate the token** for your host (a repo-specific Bitbucket access-token link, the Atlassian API-token page, or the GitHub PAT page, with the scopes you need), and when you use the Atlassian email+token method it also records your email as `user_email` in `config.toml` so `repipe config` and `git_email` autofill pick it up. Add `--verify` (inside a repo) to confirm the token works with one read-only API call before saving.

> Two emails, two jobs: `REPIPE_EMAIL` in the credentials file is your **login** credential (paired with the API token); `user_email` in `config.toml` is the **autofill/display** email. To change the login email, re-run `repipe login`; to change the autofill one, use `repipe config`.

### Verify your credential

Confirm auth without triggering anything — a read-only call:

```bash
cd /path/to/your/repo-clone
repipe status <a-recent-build-number>
```

If it prints a real state, you're set. Common HTTP codes if you debug with `curl`: `200` = good; `401` = token rejected (wrong type/value — e.g. an API token used as Bearer); `403` = authenticated but missing a scope; `404` on the repo root usually means no `read:repository` scope (harmless — repipe only calls `/pipelines/…`).

Or run **`repipe doctor`** for an all-in-one check — it confirms your credentials resolve and pass a read-only auth probe, that your config parses, whether auto-retry patterns are set, and (when a phone-push provider is configured) fires a test push to your phone. Exit code `0` = healthy, `3` = something's wrong.

Never commit tokens. `config.toml` is for non-secret defaults only, and `.gitignore` covers any `credentials` file.

## Usage

```bash
repipe                                  # interactive: pick pipeline/env/branch/vars, trigger, watch, retry
repipe login [--verify]                 # save a CI token to the credentials file (hidden input)
repipe doctor                           # check your setup: creds, auth, config, alerts (+ test push)
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

`repipe config` opens an arrow-key menu (same UX as the interactive run flow) to view and change settings without hand-editing TOML. The top level covers the globals: default retry patterns (add from suggestions, add custom, remove), max retries, match mode, poll interval, timeout, desktop notifications, per-step notifications, which events notify you, phone-push providers, and your email. **Repo settings ›** edits the per-repo basics — provider, QA branch prefix, prod branch prefix — for the repo it detects from the working tree (or, if you're not in one, a repo you pick from those already configured). Under it, **Variables ›** is a full editor for the per-repo input schema: pick a variable (or add one) and set its `enum` (add/remove allowed values), `default`, `required`, `pattern`, `autofill`, `remember`, `no_spaces_unless`, and `hint` — the same table you'd otherwise hand-write. `repipe config --show` prints the current effective globals and exits (no terminal needed, so it works over a pipe / in CI); webhook URLs are **masked** (they carry credentials) — add `--reveal` to print them in full.

### Auto-retry (opt-in — you define the patterns)

repipe ships **no** retry patterns and retries **nothing** by default. You decide which failures are worth re-triggering: list patterns in config `retry_on`, or pass `--retry-on` per run. On failure, repipe reads the failed step's log and re-triggers **only** if it matches one of *your* patterns; no match ⇒ it stops and surfaces the failure (never loops).

```bash
repipe suggestions          # a starter list of common transient errors to copy
```

**Default + per-repo patterns.** The top-level `retry_on` is the **default** set, used by every repo. A repo whose transient failures differ can set its own `retry_on` under `[repos."ws/repo"]` (edit it via `repipe config → Repo settings → Retry patterns`); when present it **fully replaces** the default for that repo (not merged). Precedence on any run: an explicit `--retry-on` beats a per-repo override beats the default.

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

#### Push to your phone (ntfy or Google Chat)

The desktop notification above needs a terminal — which is exactly what you *don't* have when you run repipe on an always-on box and close your laptop. For that, configure one or more **phone-push providers** and the finish notification (and each retry) is pushed to your **phone**. This channel is **independent of the TTY gate**: it fires precisely because there's no terminal watching. Tapping the banner opens the run.

Providers are pluggable — configure any combination and **every** enabled one fires:

| Provider | Best for | Set-up |
| --- | --- | --- |
| **ntfy** | zero-setup personal push | repipe generates a random topic; subscribe in the ntfy app |
| **Google Chat** | a personal feed inside a Google-Chat org | paste an incoming-webhook URL from a private space |
| **Slack** | a personal feed inside a Slack org | paste an incoming-webhook URL pointed at a private channel |

```bash
repipe config                          # → "Phone push ›" → pick a provider → send a test
repipe run -p deploy-qa                # pushes on finish when a provider is configured
repipe run -p deploy-qa --no-phone-notify   # skip the phone for this run
```

**ntfy** — run `repipe config → Phone push → ntfy` and pick **Generate a random ntfy.sh topic** (don't hand-pick a name; a guessable one is readable by anyone). Install the ntfy app, subscribe to that topic, and use **send a test** to confirm. It lands in config as `notify_url = "https://ntfy.sh/repipe-<random>"`.

> **Use a long, random topic.** On the public ntfy.sh server anyone who knows the topic name can read your notifications — treat it like a password. Reserved or self-hosted topics that require auth: put the token in the credentials file as `REPIPE_NOTIFY_TOKEN` (env also works), never in `config.toml`.

**Google Chat (personal push)** — Google Chat can only webhook into a *space*, not a DM, so the personal pattern is a **space of one**: a private space that only you are in, so anything posted there notifies only you. Set it up once:

1. **Create the space.** In Google Chat: **+ New space → Create a space**, name it (e.g. `repipe alerts`), and **invite nobody**.
2. **Add a webhook.** Open the space's menu (click its name) → **Apps & integrations → Manage webhooks → Add**, name it (e.g. `repipe`), and **Save**.
3. **Copy the webhook URL** — `https://chat.googleapis.com/v1/spaces/AAAA…/messages?key=…&token=…`.
4. **Tell repipe:** `repipe config → Phone push → Google Chat`, paste the URL at the prompt, and answer **yes** to *Send a test push now?* A `repipe · test` card should appear in your space.
5. **Get it on your phone:** install the **Google Chat** app (or use the Gmail Chat tab), signed into the same account, with notifications enabled and the space un-muted.

It lands in config as `notify_gchat_url = "https://chat.googleapis.com/v1/spaces/…"`. On a real run you get a card — header `repipe · <pipeline>`, the status line (`✓ #158 succeeded` / `✗ failed` / `↻ retrying`), and an **Open pipeline** button.

> The webhook URL *is* the credential (its `key`+`token` query params) — keep it private, like the ntfy topic. Your org admin can disable incoming webhooks entirely (Admin console → Apps → Google Chat); if **Manage webhooks** isn't there, that's why, and there's no repipe-side workaround. Like every notification path, a failed push never affects the run's outcome or exit code.

**Slack (personal push)** — Slack incoming webhooks post to a single channel, so the personal pattern is a **channel of one**: a private channel only you are in.

1. **Create a private channel** (e.g. `repipe-alerts`), just you.
2. At [api.slack.com/apps](https://api.slack.com/apps) → your app → **Incoming Webhooks** → enable → **Add New Webhook to Workspace**, pick that channel, and copy the URL (`https://hooks.slack.com/services/…`).
3. `repipe config → Phone push → Slack`, paste the URL, send a test. You get a Block Kit message (title, status line, **Open pipeline** button) and a phone push via the Slack app.

It lands in config as `notify_slack_url`. Same rule: the webhook URL is the credential — keep it private.

#### Choosing which events notify you

By default every outcome pings you. To cut the noise, `repipe config → Notify on events` toggles **success / failure / timeout / paused / retry** independently (applies to **all** channels — desktop and every push provider). E.g. leave only *failure* and *timeout* on to be pinged only when something needs you. Stored as `notify_events` (absent = all on).

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
