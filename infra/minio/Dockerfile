FROM golang:1.24-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac AS builder
ARG MINIO_REVISION=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
ENV GOTOOLCHAIN=local CGO_ENABLED=0
WORKDIR /src
RUN git init . && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin "$MINIO_REVISION" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$MINIO_REVISION"
RUN go build -trimpath -o /out/minio .

FROM debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 minio && useradd --uid 10001 --gid minio --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=builder /out/minio /usr/local/bin/minio
COPY --from=builder /src/LICENSE /usr/share/doc/minio/LICENSE
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
USER minio
EXPOSE 9000
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
