#!/bin/sh
# mailr wrapper entrypoint for stalwartlabs/stalwart (v0.16.x).
#
# On a blank data store: boot Stalwart once, apply provision.json through the
# JMAP management API (as the STALWART_RECOVERY_ADMIN credential), stop it, then
# exec the real server. Listener changes only take effect on restart, which is
# why the server is started twice on first boot. On later boots the domain
# already exists and provisioning is skipped.
#
# Required env: STALWART_RECOVERY_ADMIN (user:pass), STALWART_HOSTNAME,
#               MAILR_DOMAIN, MAILR_SVC_PASSWORD
# Optional env: MAILR_SVC_USER (default mailr-svc), MAILR_TRUSTED_NET (default 172.16.0.0/12;
#               the compose file passes the app and worker addresses only)
# Values are substituted into JSON with sed: keep them to [A-Za-z0-9._@:/-].
set -eu

CONF="${STALWART_CONFIG:-/etc/stalwart/config.json}"
PLAN="${MAILR_PROVISION_PLAN:-/opt/mailr/provision.json}"
# The calendar and contacts settings, a second request: one JMAP request takes at most 16 calls.
# The mailr backend applies the same settings to existing stores at every start.
PLAN2="${MAILR_PROVISION_PLAN2:-/opt/mailr/provision-groupware.json}"
API="http://127.0.0.1:8080"
BIN=/usr/local/bin/stalwart
: "${MAILR_SVC_USER:=mailr-svc}"
: "${MAILR_TRUSTED_NET:=172.16.0.0/12}"
: "${MAILR_RESOLVER_IP:=10.253.53.2}"
# Password sign-in over unencrypted IMAP (port 143): off, as on a real server. Only the development
# stack's test "upstream provider" turns it on (deploy/dev/docker-compose.yml), because the tests
# connect to it without TLS. Read on a blank store only.
case "${MAILR_IMAP_PLAIN_TEXT_AUTH:=false}" in true|false) ;; *) echo "[mailr-init] MAILR_IMAP_PLAIN_TEXT_AUTH must be true or false" >&2; exit 1 ;; esac

wait_up() {
  i=0
  until curl -fsS -H 'X-Forwarded-For: 127.0.0.1' "$API/healthz/live" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -gt 180 ]; then echo "[mailr-init] stalwart did not come up" >&2; return 1; fi
    sleep 1
  done
}

jmap() {
  curl -fsS -u "$STALWART_RECOVERY_ADMIN" -H 'Content-Type: application/json' --data-binary "$1" "$API/jmap/"
}

# The auto-ban allow-list entry provisioning created ("mailr backend ..."), kept equal to
# MAILR_TRUSTED_NET on every start, so narrowing the setting reaches an existing store.
reconcile_allowlist() {
  resp=$(jmap '{"using":["urn:ietf:params:jmap:core","urn:stalwart:jmap"],"methodCalls":[["x:AllowedIp/query",{},"q"],["x:AllowedIp/get",{"#ids":{"resultOf":"q","name":"x:AllowedIp/query","path":"/ids"}},"g"]]}') || return 0
  entry=$(echo "$resp" | grep -o '{[^{}]*"reason":"mailr backend[^{}]*}' | head -n 1)
  [ -n "$entry" ] || return 0
  addr=$(echo "$entry" | sed -n 's/.*"address":"\([^"]*\)".*/\1/p')
  id=$(echo "$entry" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
  want=$(echo "$MAILR_TRUSTED_NET" | sed -e 's|/32$||' -e 's|/128$||')
  [ -n "$id" ] || return 0
  [ "$addr" = "$want" ] && return 0
  echo "[mailr-init] auto-ban allow-list: $addr -> $MAILR_TRUSTED_NET"
  r=$(jmap "{\"using\":[\"urn:ietf:params:jmap:core\",\"urn:stalwart:jmap\"],\"methodCalls\":[[\"x:AllowedIp/set\",{\"create\":{\"app\":{\"address\":\"$MAILR_TRUSTED_NET\",\"reason\":\"mailr backend (app and worker): never auto-ban\"}},\"destroy\":[\"$id\"]},\"s\"]]}") || true
  if echo "$r" | grep -Eq '"(notCreated|notDestroyed)"'; then
    echo "[mailr-init] could not update the auto-ban allow-list: $r" >&2
  fi
}

"$BIN" --config "$CONF" &
pid=$!
wait_up

probe=$(jmap "{\"using\":[\"urn:ietf:params:jmap:core\",\"urn:stalwart:jmap\"],\"methodCalls\":[[\"x:Domain/query\",{\"filter\":{\"name\":\"$MAILR_DOMAIN\"}},\"q\"]]}")
if echo "$probe" | grep -q '"ids":\[\]'; then
  echo "[mailr-init] blank store: provisioning $MAILR_DOMAIN"
  body=$(sed -e "s|@MAILR_DOMAIN@|$MAILR_DOMAIN|g" \
             -e "s|@STALWART_HOSTNAME@|$STALWART_HOSTNAME|g" \
             -e "s|@MAILR_SVC_USER@|$MAILR_SVC_USER|g" \
             -e "s|@MAILR_SVC_PASSWORD@|$MAILR_SVC_PASSWORD|g" \
             -e "s|@MAILR_TRUSTED_NET@|$MAILR_TRUSTED_NET|g" \
             -e "s|@MAILR_RESOLVER_IP@|$MAILR_RESOLVER_IP|g" \
             -e "s|\"allowPlainTextAuth\": false|\"allowPlainTextAuth\": $MAILR_IMAP_PLAIN_TEXT_AUTH|" "$PLAN")
  resp=$(jmap "$body")
  if echo "$resp" | grep -Eq '"(notCreated|notUpdated|notDestroyed)"|\["error"'; then
    echo "[mailr-init] provisioning FAILED: $resp" >&2
    kill -TERM "$pid"; wait "$pid" || true
    exit 1
  fi
  if [ -f "$PLAN2" ]; then
    resp=$(jmap "$(cat "$PLAN2")") || resp="request failed"
    if echo "$resp" | grep -Eq '"(notCreated|notUpdated|notDestroyed)"|\["error"|request failed'; then
      echo "[mailr-init] provisioning FAILED (groupware settings): $resp" >&2
      kill -TERM "$pid"; wait "$pid" || true
      exit 1
    fi
  fi
  echo "[mailr-init] provisioned; restarting to apply listeners"
  kill -TERM "$pid"; wait "$pid" || true
  exec "$BIN" --config "$CONF"
elif echo "$probe" | grep -q '"ids":\["'; then
  echo "[mailr-init] already provisioned"
  reconcile_allowlist
  # keep the already-running process; forward docker stop signals to it
  trap 'kill -TERM "$pid" 2>/dev/null' TERM INT
  wait "$pid" || true
  wait "$pid" 2>/dev/null || true
else
  echo "[mailr-init] unexpected probe response: $probe" >&2
  kill -TERM "$pid"; wait "$pid" || true
  exit 1
fi
