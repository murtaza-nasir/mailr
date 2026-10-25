# Upgrades

## Upgrade Mailr

1. Check that the last [database dump](backups.md#database-dumps) is recent (`docker compose run --rm cli mailr db-dump --list`). A dump is also written at start when the new version brings migrations.
2. Get the new version:

    === "Built from the repository"

        ```sh
        git pull
        cd deploy
        docker compose build
        ```

    === "Published image"

        With `MAILR_IMAGE` on `latest`, or set to the new version's tag:

        ```sh
        cd deploy
        docker compose pull
        ```

3. Restart:

    ```sh
    docker compose up -d
    ```

Database migrations are applied when the app starts. A database dump is written first, on the backup target or, without one, in the data volume ([Before migrations](backups.md#before-migrations)). When it fails, the app does not start and nothing is migrated. To run them by hand, for example before you start the app:

```sh
docker compose run --rm cli mailr migrate
```

The `cli` service works whether the app is running or not. `docker compose run --rm app …` fails while the app runs, because the app's address and port are already in use.

## Pinned images

Every base image (Postgres, Stalwart, the resolver, the VPN container) is pinned to a version and digest, so no different image is introduced by a rebuild. Update them deliberately:

- **Postgres**: minor versions are drop-in. For a new major version, a dump and restore are required.
- **Stalwart**: read its release notes before a new minor version.
- After changing a pin, rebuild and restart, then check the app as below.

## Postgres settings

A new compose file can change the Postgres settings ([Deployment](deployment.md#postgres-settings)). They take effect only when the `postgres` container is restarted, which `docker compose up -d` does when its configuration changed. Write a dump first, then restart Postgres, then the rest:

```sh
docker compose run --rm cli mailr db-dump --reason before-restart
docker compose up -d postgres
docker compose up -d
```

The app and the worker reconnect by themselves after the restart; requests made during it fail for a few seconds.

## After an upgrade

- Check the version at the bottom of the web app's **Support (?)** menu. It is the release you upgraded to ([The running version](troubleshooting.md#the-running-version)).
- Open **Settings → Admin → Diagnostics** and check that the mail store is reachable and no jobs failed.
- Errors from the new version are shown with `docker compose logs -f app worker`, in `/data/logs`, and in the [error tracker](errors.md) when one is set up. Reports carry the release, so errors of the new version are told apart from older ones.

## Maintenance commands

| Command | Use |
|---|---|
| `mailr migrate` | Apply database migrations by hand. |
| `mailr rethread alex@northwind.example` | Rebuild one user's conversations, for example after a large import with broken threading headers. |
| `mailr reembed` | Rebuild the search vectors after you change the search index model. See [Models](models.md). |

Run them with `docker compose exec app mailr <command>`. The [command line reference](../reference/cli.md) lists every command, including the one-off repairs for installs that were upgraded from early builds.
