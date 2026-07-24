# Security Policy

## Supported versions

repipe is distributed through GitHub Releases and the Homebrew tap. Only the
**latest release** receives security fixes. Upgrade with `repipe upgrade` or
`brew upgrade repipe` before reporting, in case the issue is already fixed.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, use one of the following private channels:

- **Preferred:** [GitHub private vulnerability reporting](https://github.com/AbhayG21/repipe/security/advisories/new)
  — "Report a vulnerability" on the repo's Security tab.
- **Email:** abhay19021@gmail.com

Please include as much of the following as you can:

- A description of the vulnerability and its impact.
- Steps to reproduce, or a proof-of-concept.
- The repipe version (`repipe version`) and your OS/Python version.
- Any relevant configuration (**with all tokens and credentials redacted**).

## What to expect

- Acknowledgement of your report as soon as reasonably possible.
- An assessment and, if confirmed, a fix in a subsequent release.
- Credit in the release notes if you'd like it (let me know).

## Handling credentials

repipe stores and transmits CI credentials (e.g. Bitbucket app passwords,
GitHub tokens) to trigger and watch pipelines. Keep this in mind:

- **Never paste real tokens** into issues, PRs, logs, or bug reports. Redact them.
- Credentials are stored locally via `repipe login`; treat that store like any
  other secret on your machine.
- If you believe repipe mishandles, leaks, or logs a credential, that is a
  security issue — please report it privately using the channels above.
