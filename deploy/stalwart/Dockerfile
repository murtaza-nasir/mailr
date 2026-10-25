# Stalwart with mailr's provisioning baked in (no bind mounts needed).
FROM stalwartlabs/stalwart:v0.16.23@sha256:be215678796691bc39bdda918ecc50d14a9032a099a1d1950e51950aec7e2592
COPY config.json /etc/stalwart/config.json
COPY provision.json /opt/mailr/provision.json
COPY provision-groupware.json /opt/mailr/provision-groupware.json
COPY entrypoint.sh /opt/mailr/entrypoint.sh
ENTRYPOINT ["/bin/sh", "/opt/mailr/entrypoint.sh"]
