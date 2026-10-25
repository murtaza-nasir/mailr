# Security policy

## Supported versions

Security fixes are made in the latest release only. The latest release is the newest entry under
[Releases](https://github.com/murtaza-nasir/mailr/releases), and its image is
`ghcr.io/murtaza-nasir/mailr:latest`. A server on an older release receives a fix by upgrading.

| Version | Security fixes |
|---|---|
| Latest release | Yes |
| Earlier releases | No |

The Android apps follow the same rule: fixes are made in the APKs attached to the latest release.

## Reporting a vulnerability

Security issues must not be reported in public issues, discussions or pull requests. A public
report shows the problem to everyone before a fix exists.

Vulnerabilities are reported privately through GitHub:

1. Open the [Security tab](https://github.com/murtaza-nasir/mailr/security) of the repository.
2. Select **Report a vulnerability**.
3. Fill in the form and submit it.

The report is visible only to the maintainers and to the person who reported it. The direct link is
[github.com/murtaza-nasir/mailr/security/advisories/new](https://github.com/murtaza-nasir/mailr/security/advisories/new).

### What to include

- The affected component: the web app, the API, the worker, the bundled mail server
  configuration (`deploy/`), the Android app or the Notes app.
- The Mailr version. The web app shows the server's version at the bottom of its Support menu (the
  question mark in the top bar), and `GET /api/v1/me` answers it as `version`. The Android app shows its own version and the server's at the foot
  of Settings, and the Notes app shows its version under Settings → About. For a server built from
  source, the commit as well.
- A description of the problem and its impact: what an attacker can read, change or run, and which
  access they need first (none, a user account, an API token, an admin account).
- Steps to reproduce, with a minimal request, script or configuration. Real mail, real addresses
  and real tokens must be left out or replaced with examples.
- Any relevant settings, such as single sign-on, the reverse proxy or the model configuration.
- A suggested fix, when one is known.

## After a report

- Receipt of the report is confirmed in the advisory, and questions are asked there.
- When the problem is confirmed, a fix is prepared in a private fork of the advisory and released
  as a new version. The advisory is then published with credit to the reporter, unless the reporter
  asks not to be named.
- Details are kept private until the fixed release is available.
