# Stalwart with mailr's provisioning baked in (no bind mounts needed).
FROM stalwartlabs/stalwart:v0.16.27@sha256:c429b311c93db5606598abc1c68225a85a6953183848ae01bee9c6a98885836c
COPY config.json /etc/stalwart/config.json
COPY provision.json /opt/mailr/provision.json
COPY provision-groupware.json /opt/mailr/provision-groupware.json
COPY entrypoint.sh /opt/mailr/entrypoint.sh
ENTRYPOINT ["/bin/sh", "/opt/mailr/entrypoint.sh"]
