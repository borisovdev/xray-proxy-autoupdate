# AGENTS.md

This file provides guidance to ai agents when working with code in this repository.

## What this is

A single-container xray proxy (SOCKS on 1080, HTTP on 3128) whose outbound VLESS+Reality server is updated automatically from a subscription URL. All logic lives in `update-sub.py`, a stdlib-only Python script that is the container's ENTRYPOINT and runs as a supervisor of a child `xray` process. There is no Docker socket and no separate updater container.

## Commands

There is no test suite, linter config or build system besides Docker.

```bash
cp subscription.env.example subscription.env       # set SUB_URL (gitignored, it's a credential)
docker compose up -d --build                       # build image + run
docker compose logs -f xray-proxy                  # logfmt output, filter by component=sub-update
docker compose exec xray-proxy update-sub check    # run the tunnel healthcheck by hand
curl -x socks5h://127.0.0.1:1080 https://api.telegram.org -I
python3 -m py_compile update-sub.py                # quick syntax check
```

### Release (CI)

`.github/workflows/release.yml` builds and pushes the image to GHCR. It is triggered only by pushing a semver tag `vX.Y.Z`, and the tagged commit must be on `main` (the job fails otherwise):

```bash
git tag v1.2.3 && git push origin v1.2.3   # -> ghcr.io/<owner>/<repo>:1.2.3, :1.2, :latest
```

Auth uses the built-in `GITHUB_TOKEN`; no secrets to configure.

`update-sub.py` hardcodes `/etc/xray` paths and expects the `xray` binary on PATH, so it is meant to run inside the container, not on the host.

## Architecture / invariants that span files

- **Update loop** (`update()` in `update-sub.py`): fetch subscription (plain or base64) → pick the first `vless://` with `security=reality` and `type=tcp` → substitute `{{PLACEHOLDERS}}` in `config.tmpl.json` → `xray run -test` on `config.new.json` → swap into `config.json` (old one saved to `config.json.prev`) → restart child xray → tunnel check → roll back to the previous config on failure. Errors are raised as `UpdateError(msg, **kv)` and logged by the main loop; they never kill the supervisor.
- **Tunnel check** (`tunnel_ok`): sends `CONNECT CHECK_HOST:443` to its own http inbound on port 3128 and completes a TLS handshake + HEAD. xray answers `200` to CONNECT before the upstream works, so only the TLS step proves the tunnel. Because of this, the `http-in` inbound in `config.tmpl.json` must stay on port 3128 (`PROXY_PORT` constant). The same function backs the Docker healthcheck (`update-sub check` in `compose.yaml`).
- **Template** `config.tmpl.json` is JSON with `//` comments (xray accepts this) and an unquoted `{{PORT}}`, so it isn't valid JSON until rendered. Placeholders: `ADDRESS PORT UUID FLOW SNI FP PBK SID SPX`; adding a new one requires updating `render()`, and any leftover `{{` aborts the update. The rendered temp file must keep the `.json` extension because `xray -test` detects the format from it.
- **Startup / lifecycle**: if `config.json` already exists in the volume, xray starts at once without waiting for the subscription. If the child xray dies on its own, the supervisor exits with code 1 and relies on Docker `restart: always`. `compose.yaml` uses `init: true` for signal handling; SIGTERM stops xray cleanly.
- **Volumes**: `/etc/xray` is a named volume holding the generated `config.json`/`.prev`; `config.tmpl.json` and `subscription.env` are bind-mounted read-only over it. The script sets `umask 077` since configs contain the UUID.
- **Privacy in logs**: only the server name (last `#fragment` part) is logged, never the URL, UUID or keys. `clean()` truncates and de-quotes error text for logfmt.

## Configuration

Env vars: `UPDATE_INTERVAL` (seconds, default 3600) and `CHECK_HOST` (default `api.telegram.org`). `SUB_URL` comes from `subscription.env`, not the environment. Base images are pinned (`ghcr.io/xtls/xray-core`, `alpine`) in `Dockerfile`. `compose.yaml` is marked as a local/personal example, not for production.
