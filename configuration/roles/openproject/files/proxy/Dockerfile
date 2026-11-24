ARG CADDY_BASE_IMAGE
FROM ${CADDY_BASE_IMAGE}
COPY ./Caddyfile.template /etc/caddy/Caddyfile.template
ARG APP_HOST=web
RUN sed 's|${APP_HOST}|'"$APP_HOST"'|g' /etc/caddy/Caddyfile.template > /etc/caddy/Caddyfile
ENTRYPOINT ["caddy", "run", "--config", "/etc/caddy/Caddyfile"]
