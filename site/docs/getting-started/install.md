# Install with Docker Compose

This page covers setting up a working Mailr on one host. Production details are in the [Admin guide](../admin/deployment.md).

Before you start, check the [system requirements](index.md#system-requirements). BuildKit is required for the image build. With a Docker version that does not use it by default, install the `buildx` plugin.

## 0. Install Docker

Skip this step when `docker compose version` and `docker buildx version` already answer. On Ubuntu 24.04 or Debian 12, the distribution's packages are enough:

```sh
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx git
sudo usermod -aG docker "$USER"    # then log out and in again, so docker runs without sudo
```

On other systems, or for Docker's own newer packages, follow [Docker's install guide](https://docs.docker.com/engine/install/).

With the files as they are, only one Mailr stack can run on a host: the Compose project name, the internal networks and the mail server's image tag are fixed.

## 1. Get the files

Clone the repository and copy the example environment file.

```sh
git clone https://github.com/murtaza-nasir/mailr.git
cd mailr/deploy
cp .env.example .env
```

The `deploy/` folder holds `docker-compose.yml`, the Stalwart image definition and `.env.example`. All installation-specific settings go into `.env`.

## 2. Generate the secrets

Two random keys are required. Generate each one with `openssl`:

```sh
openssl rand -base64 32   # copy into MAILR_SECRET_KEY
openssl rand -base64 32   # copy into MAILR_SESSION_SECRET
```

Any source of 32 random bytes in base64 works. Mailr's own command can also be used, before or after the first start. Before the first start the image is built first, which takes a few minutes, and the secret is printed after the build output:

```sh
docker compose run --rm --no-deps cli mailr gen-secret
```

!!! warning "Keep `MAILR_SECRET_KEY` safe"
    The stored mail account passwords and model API keys are encrypted with `MAILR_SECRET_KEY`. If the key is lost, those passwords must be entered again. Keep a copy with your backups, and never commit it.

## 3. Fill in `.env`

The required values:

| Variable | Value |
|---|---|
| `MAILR_BASE_URL` | The address at which Mailr is opened, such as `https://mail.example.com`. No trailing slash. For a name on your local network, such as `https://mailr.lan`, see [step 5](#5-put-a-reverse-proxy-in-front). |
| `MAILR_SECRET_KEY` | The first generated secret. |
| `MAILR_SESSION_SECRET` | The second generated secret. Optional: the single sign-on state is signed with it, or with `MAILR_SECRET_KEY` when `MAILR_SESSION_SECRET` is empty. |
| `POSTGRES_PASSWORD` | A password for Mailr's database. |
| `STALWART_ADMIN_PASSWORD` | The recovery password of the bundled mail server. |
| `MAILR_SVC_PASSWORD` | The password for Mailr's connection to the mail server. |
| `MAILR_BOOTSTRAP_ADMIN_EMAIL` | The email address of the first admin account. |
| `MAILR_BOOTSTRAP_ADMIN_PASSWORD` | That account's password. |
| `MAILR_IMAP_HOSTNAME` | The host name for IMAP in mail apps, such as `mail.example.com`. |

!!! note "Mail server passwords"
    Use only letters, digits and `.`, `_` and `-` in `STALWART_ADMIN_PASSWORD` and `MAILR_SVC_PASSWORD`. The base64 output of `openssl rand -base64` contains `+` and `/`, so it does not fit there.

This command prints a random password of letters and digits. Run it once for each of `POSTGRES_PASSWORD`, `STALWART_ADMIN_PASSWORD` and `MAILR_SVC_PASSWORD`:

```sh
openssl rand -hex 24
```

A minimal `.env`:

```ini
MAILR_BASE_URL=https://mail.example.com
MAILR_SECRET_KEY=Zm9v...
MAILR_SESSION_SECRET=YmFy...
POSTGRES_PASSWORD=change-me-postgres
STALWART_ADMIN_PASSWORD=change-me-stalwart
MAILR_SVC_PASSWORD=change-me-service
MAILR_BOOTSTRAP_ADMIN_EMAIL=alex@northwind.example
MAILR_BOOTSTRAP_ADMIN_PASSWORD=a-long-passphrase
MAILR_IMAP_HOSTNAME=mail.example.com
```

Every other setting is optional. See [Configuration](../admin/configuration.md) for a tour and [Environment variables](../reference/environment.md) for the full list.

## 4. Start the stack

Check `.env` first. The image is built for this check, which takes a few minutes; the start below then reuses it. With `--offline`, the checks that need the running database and mail server are left out:

```sh
docker compose run --rm --no-deps cli mailr check-config --offline
```

The answer `ok: no problems found in the configuration` means the stack can start. Otherwise, each line is one problem, with the setting to fix ([Configuration](../admin/configuration.md)). Then start the stack:

```sh
docker compose up -d
```

The first build of the images from source takes a few minutes (about 3 minutes on 4 cores). Later starts reuse them: `docker compose up -d` after editing `.env` builds nothing and recreates only the containers whose settings changed (a changed value recreates the containers that use it). A new version is built only when you ask for it; see [Upgrades](../admin/upgrades.md). To skip the build, [use a published image](#use-a-published-image).

On the first start:

- Stalwart is set up and restarted once. When it is done, `provisioned` appears in `docker compose logs stalwart | grep mailr-init`.
- The database migrations are applied and the bootstrap admin is created.
- Accounts are synced as soon as they are added.

Check that the app is up:

```sh
curl http://localhost:8025/api/v1/health
# {"ok":true}
```

## 5. Put a reverse proxy in front

The app is on port 8025 (`MAILR_HTTP_PORT`). Point the HTTPS reverse proxy at it. Turn off response buffering for the live event stream. [Reverse proxy](../admin/reverse-proxy.md) has examples for Caddy, nginx and Traefik.

For a quick test on a local machine, set `MAILR_BASE_URL=http://localhost:8025` and open that address. A warning that the address still points at this machine is then logged; for a local test it is expected.

On a local network without a public domain (a name such as `mailr.lan`), a public certificate cannot be issued. Caddy can serve its own certificate instead; see [A name on the local network](../admin/reverse-proxy.md#a-name-on-the-local-network). Install Caddy from your distribution (`sudo apt-get install -y caddy` on Ubuntu or Debian) or from [Caddy's install page](https://caddyserver.com/docs/install).

## Ports

| Port | Published on | Variables |
|---|---|---|
| 8025, the web app and the API | `127.0.0.1` only | `MAILR_HTTP_PORT`, `MAILR_HTTP_BIND` |
| 993, IMAP over TLS for mail apps | every interface (`0.0.0.0`) | `MAILR_IMAPS_PORT`, `MAILR_IMAPS_BIND` |
| 143, IMAP with STARTTLS | `127.0.0.1` only | `MAILR_IMAP_PORT`, `MAILR_IMAP_BIND` |

When a port is already taken on the host, set another port in `.env`, such as `MAILR_HTTP_PORT=8125`. To keep IMAP off the network, set `MAILR_IMAPS_BIND=127.0.0.1`. When the reverse proxy runs on another machine, set `MAILR_HTTP_BIND` to this host's address on the network the proxy uses.

## Use a published image

By default the image is built from the repository (`MAILR_IMAGE` unset, the image is tagged `mailr:local`). To run a published image instead, set two lines in `.env`:

```ini
MAILR_IMAGE=ghcr.io/murtaza-nasir/mailr:latest
MAILR_PULL_POLICY=missing
```

Then fetch and start it without a local build:

```sh
docker compose pull
docker compose up -d
```

The image is published for `amd64` and `arm64` hosts. `latest` is the newest release. Each release is also tagged with its version, such as `ghcr.io/murtaza-nasir/mailr:0.40.13`, and with its minor version, such as `0.40`. With a version tag in `MAILR_IMAGE`, the server stays on that release until the tag is changed. The changes in each release are listed under [Releases](https://github.com/murtaza-nasir/mailr/releases) on GitHub and in `CHANGELOG.md` in the repository.

To go back to building from source, remove both lines and run `docker compose up -d --build`. The bundled mail server image (`deploy/stalwart`) is always built locally; it is small and takes seconds.

## Next

[Set up the server's first admin](first-admin.md).
