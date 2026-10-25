# Reverse proxy

Put an HTTPS reverse proxy in front of the `app` service. TLS for `https://mail.example.com` is terminated at the proxy, and requests are forwarded to port 8025 (`MAILR_HTTP_PORT`). The port is published on `127.0.0.1` by default. If the proxy is on another machine, set `MAILR_HTTP_BIND` to this host's address on the proxy's network.

## Proxy requirements

- **The public address.** Set `MAILR_BASE_URL=https://mail.example.com`. With `MAILR_CANONICAL_REDIRECT=true` (the default), page loads that arrive on any other host name are redirected to this address, so cookies and sign-in stay on one origin.
- **Forwarded headers.** `X-Forwarded-For` and `X-Forwarded-Proto` are sent from the proxy. They are trusted only from addresses in `MAILR_TRUSTED_PROXIES`. Left empty (the default), the list is loopback plus the gateway of the stack network: `10.253.54.1` with the default `MAILR_NET_SUBNET`. Connections from a proxy on the same host come from that gateway, so the default fits it. If the proxy is elsewhere, set its address, for example `MAILR_TRUSTED_PROXIES=192.0.2.20`.
- **Rootless Docker and IPv6.** Under rootless Docker, and when IPv6 is published through Docker's userland proxy (`docker-proxy`), every client's connection reaches Mailr from the stack network's gateway, which the default list trusts. Any client could then set its own address in `X-Forwarded-For`, and the sign-in limits, the session list and the audit log would show that address. In those setups set `MAILR_TRUSTED_PROXIES` to your reverse proxy's own address and make sure clients reach Mailr only through it; set `MAILR_DAV_TRUSTED_PROXY` the same way for the calendar gateway.
  When the list is wrong, every request seems to come from one address. All users on the server then share one sign-in limit, and the wrong address appears in the session list and the audit log. A warning is logged once when forwarded headers arrive from an untrusted address, and **Settings → Admin → Diagnostics** shows the list in use and that warning.
- **No buffering for the event stream.** Live updates are pushed over server-sent events at `/api/v1/events`. Turn response buffering off for that path, or updates arrive late. WebSockets are not needed.
- **Large uploads.** Allow request bodies as large as the expected attachments and imports. Bodies over `MAILR_MAX_REQUEST_BYTES` (50 MB) are refused in Mailr itself, except mailbox imports, attachments (`MAILR_MAX_ATTACHMENT_BYTES`) and recording uploads, which have their own limits.
- **Mailr's own security headers.** `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and a Content-Security-Policy are set in Mailr. Do not add or replace these headers in the proxy.

!!! note "IMAP is not HTTP"
    Ports 993 and 143 are for IMAP from mail apps. They are plain TCP. Publish them on the host directly, or use a TCP (layer 4) proxy. They cannot go through an HTTP proxy. Port 993 is published on every address of the host (`MAILR_IMAPS_BIND`, `0.0.0.0` by default), and port 143 on `127.0.0.1` (`MAILR_IMAP_BIND`).

## Examples

=== "Caddy"

    ```caddyfile
    mail.example.com {
        reverse_proxy 127.0.0.1:8025 {
            flush_interval -1
        }
        request_body {
            max_size 1GB
        }
    }
    ```

    The example is for Caddy on the host, where the app is published on `127.0.0.1:8025`. When Caddy runs in a container on Mailr's stack network, use `reverse_proxy app:8025` instead, and set `MAILR_TRUSTED_PROXIES` to the Caddy container's address: only the network's gateway is trusted by default.

=== "nginx"

    ```nginx
    server {
        listen 443 ssl http2;
        server_name mail.example.com;
        # ssl_certificate ... ; ssl_certificate_key ... ;

        client_max_body_size 1g;

        location / {
            proxy_pass http://127.0.0.1:8025;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /api/v1/events {
            proxy_pass http://127.0.0.1:8025;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_buffering off;
            proxy_read_timeout 1h;
        }
    }
    ```

    If you use Nginx Proxy Manager, add `proxy_buffering off;` under **Advanced**.

=== "Traefik"

    Add labels to the `app` service:

    ```yaml
    services:
      app:
        labels:
          - traefik.enable=true
          - traefik.http.routers.mailr.rule=Host(`mail.example.com`)
          - traefik.http.routers.mailr.entrypoints=websecure
          - traefik.http.routers.mailr.tls.certresolver=letsencrypt
          - traefik.http.services.mailr.loadbalancer.server.port=8025
    ```

    With Traefik, server-sent events are streamed without buffering by default.

    Traefik routes only to containers that Docker reports healthy. The app's healthcheck asks only whether the database and the mail store answer ([Container healthchecks](monitoring.md#container-healthchecks)), so a backup or account problem never takes the web app off the proxy.

## A name on the local network

For a server reached only at home, without a public domain (a name such as `mailr.lan` from your router or DNS server), a public certificate cannot be issued. Caddy can then make and serve its own. With the Ubuntu or Debian package, put this in `/etc/caddy/Caddyfile` in place of the example site (`:80 { … }`) that the package installs, then run `sudo systemctl reload caddy`:

```caddyfile
mailr.lan {
    tls internal
    reverse_proxy 127.0.0.1:8025 {
        flush_interval -1
    }
    request_body {
        max_size 1GB
    }
}
```

Set `MAILR_BASE_URL=https://mailr.lan`. Browsers warn about the certificate until they trust Caddy's local authority. Until it is trusted, Mailr works, but offline use and browser notifications do not, because the browser refuses the app's background script. On the Caddy host, `sudo caddy trust` adds it to that machine. For other computers and phones, copy Caddy's root certificate and import it as a trusted authority in the system or browser settings. With the Ubuntu package, the file is readable only by Caddy, so copy it with `sudo` first:

```sh
sudo cp /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt ~/caddy-root.crt
sudo chown "$USER" ~/caddy-root.crt
```

The file is a public certificate, so it may be copied to other machines.

The Android app trusts only the phone's built-in authorities, so it does not accept this certificate. It does connect over plain HTTP to names that end in `.lan`: enter `http://mailr.lan` as the server address and add a plain HTTP site for it to the Caddyfile (`http://mailr.lan { reverse_proxy 127.0.0.1:8025 { flush_interval -1 } }`). Use this only on a network you trust.

## Check the proxy

Open `https://mail.example.com/api/v1/health`. The answer is `{"ok":true}`. Then sign in and open a conversation in a second tab: when the event stream works and you archive the conversation in one tab, the other is updated within a second.
