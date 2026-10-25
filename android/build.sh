#!/usr/bin/env bash
# Build inside Docker (Android SDK 35, JDK 21). The host needs only Docker.
#   ./build.sh assembleDebug
#   ./build.sh testDebugUnitTest recordRoborazziDebug
#
# Every checkout on this machine shares one Gradle home (the Docker volume mailr-gradle: downloaded
# dependencies, the build cache and wrappers), so a second checkout does not download everything again. Gradle in
# one container cannot ask Gradle in another to let go of a cache lock, so builds take turns:
# each run holds /tmp/mailr-android-build.lock (flock) for its whole length. A second build waits.
set -euo pipefail
cd "$(dirname "$0")"
IMAGE=${ANDROID_BUILD_IMAGE:-ghcr.io/cirruslabs/android-sdk:35}
LOCK=${MAILR_ANDROID_LOCK:-/tmp/mailr-android-build.lock}
if [ -z "${MAILR_ANDROID_LOCKED:-}" ]; then
  exec 9>>"$LOCK"
  if ! flock -n 9; then
    echo "build.sh: another Android build holds $LOCK; waiting" >&2
    flock 9
  fi
  export MAILR_ANDROID_LOCKED=1
fi
# Per-deployment settings (app/build.gradle.kts): deploy.properties sits in this folder, which the
# container mounts; these environment variables, when set, override it. MAILR_TEST_FORKS and
# MAILR_TEST_FORK_EVERY set the unit-test JVMs (buildSrc/MailrTests.kt).
DEPLOY_ENV=()
for v in MAILR_APP_LINK_HOSTS MAILR_DEFAULT_SERVER MAILR_TEST_FORKS MAILR_TEST_FORK_EVERY; do
  if [ -n "${!v:-}" ]; then DEPLOY_ENV+=(-e "$v"); fi
done
# A release key kept elsewhere (another checkout's android/ folder): MAILR_KEYSTORE_DIR=<dir> mounts it
# read-only, and app/build.gradle.kts reads its keystore.properties.
KEY_MOUNT=()
if [ -n "${MAILR_KEYSTORE_DIR:-}" ]; then
  KEY_MOUNT=(-v "$(cd "$MAILR_KEYSTORE_DIR" && pwd)":/keystore:ro -e MAILR_KEYSTORE_PROPERTIES=/keystore/keystore.properties)
fi
docker volume create mailr-gradle >/dev/null
# ~/.android holds the debug keystore; keeping it lets debug builds install over each other.
docker volume create mailr-android-home >/dev/null
exec docker run --rm \
  -v "$PWD":/p -v mailr-gradle:/gradle -v mailr-android-home:/root/.android -v mailr-m2:/root/.m2 -e GRADLE_USER_HOME=/gradle -w /p \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" ${DEPLOY_ENV[@]+"${DEPLOY_ENV[@]}"} ${KEY_MOUNT[@]+"${KEY_MOUNT[@]}"} \
  "$IMAGE" bash -c './gradlew --console=plain "$@"; rc=$?; chown -R "$HOST_UID:$HOST_GID" /p 2>/dev/null; exit $rc' _ "$@"
