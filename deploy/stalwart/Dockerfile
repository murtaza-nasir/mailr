# Stalwart with mailr's provisioning baked in (no bind mounts needed).
# The default stays 0.16.27 until the cut-over to 1.x (docs/design/stalwart-1.0-cutover.md). A 1.x
# image for the rehearsal and the cut-over:
#   docker build --build-arg STALWART_IMAGE=stalwartlabs/stalwart:v1.0.1@sha256:b9da4df8b4efc06bc926dd6e990bdeb0ad7b9261a7c3893d74b0461a00bb389b .
# A 1.x server does not open a 0.16 store: the store must go through stalwart-migrate first.
ARG STALWART_IMAGE=stalwartlabs/stalwart:v1.0.1@sha256:b9da4df8b4efc06bc926dd6e990bdeb0ad7b9261a7c3893d74b0461a00bb389b
FROM ${STALWART_IMAGE}
COPY config.json /etc/stalwart/config.json
COPY provision.json provision.v1.json provision-groupware.json provision-groupware.v1.json /opt/mailr/
COPY entrypoint.sh /opt/mailr/entrypoint.sh
ENTRYPOINT ["/bin/sh", "/opt/mailr/entrypoint.sh"]
