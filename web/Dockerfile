# Prepared locally with scripts/prepare_deploy.ps1. No Flutter SDK on server.
FROM httpd:2.4-bookworm
ARG SOURCE_REVISION=d2407fa2e31478c458eac2b015e1c1b9a7d686c3
ARG RELEASE_VERSION=qa-d2407fa2e314
LABEL org.opencontainers.image.source="https://github.com/AndyGuzmanEscalera/correspondencia-sipe-sipe" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.version="${RELEASE_VERSION}"
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp app \
    && sed -ri 's/^Listen 80$/# Listen supplied by correspondencia.conf/; s/^User .*/User app/; s/^Group .*/Group app/; s/^#(LoadModule (proxy|proxy_http|rewrite|headers|remoteip)_module .*)/\1/' /usr/local/apache2/conf/httpd.conf \
    && printf '\nInclude conf/extra/correspondencia.conf\n' >> /usr/local/apache2/conf/httpd.conf \
    && rm -rf /usr/local/apache2/htdocs/* \
    && chown 10001:10001 /usr/local/apache2/logs
COPY apache/correspondencia.conf /usr/local/apache2/conf/extra/correspondencia.conf
COPY build/ /usr/local/apache2/htdocs/
USER 10001:10001
EXPOSE 8080
CMD ["httpd-foreground"]
