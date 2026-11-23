# xray + config auto-update from a VLESS subscription in a single container:
# no docker.sock, xray is restarted by the supervisor itself (update-sub.py).
FROM ghcr.io/xtls/xray-core:26.3.27 AS xray

FROM alpine:3.24.3
RUN apk add --no-cache python3 ca-certificates
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray
COPY update-sub.py /usr/local/bin/update-sub
ENTRYPOINT ["/usr/local/bin/update-sub"]
