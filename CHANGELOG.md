# Changelog

All notable changes to repipe are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Release notes for each version are also on the
[GitHub Releases](https://github.com/AbhayG21/repipe/releases) page.

## [Unreleased]

## [2.4.0] - 2026-08-20
- The Branch and Pipeline pickers now suggest what you recently ran, per
  environment: recent branches are listed first and marked `suggested`, and the
  cursor starts on your last-run pipeline.
- New `prod_retry` setting (global, with a per-repo override) records standing
  consent for prod auto-retry, so `repipe run` no longer needs `--force` every
  time. `--force` still wins; editable in `repipe config`.
- Triggering a prod pipeline from the interactive flow no longer asks you to type
  the pipeline name — its Confirm step is the confirmation. `repipe run` and
  `repipe rerun` still require the typed name.
- Added `connect timed out` and `operation timed out` to `repipe suggestions`.
- Pre-flight check: before triggering, verify the branch exists on the `origin`
  remote and fail early with a clear message if it isn't pushed — instead of an
  opaque 404 after the prod confirmation. Best-effort (bounded, never blocks when
  the remote can't be reached).
- Clearer 404 errors: the "not found" message now includes the failing URL and a
  provider-specific hint (Bitbucket: branch not pushed / wrong repo or token;
  GitHub: missing workflow/repo, or a private repo the token can't access).

## [2.3.0] - 2026-07-23
- Slack push notifications.
- Per-repo retry configuration.
- Pipeline event filters.
- Safer config handling.

## [2.2.0] - 2026-07-22
- Google Chat push notifications.
- "Update available" hint when a newer release exists.

## [2.1.0] - 2026-07-22
- `repipe doctor`: one-command setup check.

## [2.0.1] - 2026-07-21
- Fix login email handling.
- Add token-generation links to the login flow.

## [2.0.0] - 2026-07-18
- Phone push notifications via ntfy — get pinged when your deploy finishes.

<!-- Releases v1.6.0–v1.8.0 predate this changelog; see the GitHub Releases page. -->

[Unreleased]: https://github.com/AbhayG21/repipe/compare/v2.4.0...HEAD
[2.4.0]: https://github.com/AbhayG21/repipe/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/AbhayG21/repipe/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/AbhayG21/repipe/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/AbhayG21/repipe/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/AbhayG21/repipe/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/AbhayG21/repipe/compare/v1.8.0...v2.0.0
