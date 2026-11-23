# xray-proxy-autoupdate

A Docker image with xray and the `update-sub.py` supervisor. The supervisor keeps a VLESS+Reality tunnel
up and updates the config from a subscription on its own, rolling back if the new server doesn't work.

## How it works

Every `UPDATE_INTERVAL` seconds it:

1. downloads the subscription (`SUB_URL`), accepting both plain-text lists and base64;
2. picks the first `vless://` entry with `security=reality` and `type=tcp`;
3. fills the parameters into `config.tmpl.json` → `xray run -test`;
4. if the config has changed, replaces `config.json` (old one → `config.json.prev`) and restarts xray;
5. checks the tunnel: a `CONNECT` through its own http inbound and a TLS handshake with `CHECK_HOST:443`;
6. if the check fails, restores the previous config.

On startup it brings xray up immediately using the existing `config.json` from the volume, without waiting for the subscription.
If xray crashes on its own, the supervisor exits and docker restarts the container.
`update-sub check` is a healthcheck that runs the same tunnel check.

No Docker socket needed: everything runs in a single container.

## Running

```bash
cp subscription.env.example subscription.env   # set SUB_URL
docker compose up -d --build
docker compose logs -f xray-proxy
curl -x socks5h://127.0.0.1:1080 https://api.telegram.org -I
```

## Settings

| Variable          | Default            | Purpose                                 |
|-------------------|--------------------|-----------------------------------------|
| `UPDATE_INTERVAL` | `3600`             | subscription update interval, seconds   |
| `CHECK_HOST`      | `api.telegram.org` | host used to check the tunnel (:443)    |

Files in `/etc/xray`:

- `subscription.env`: `SUB_URL=...`, mounted read-only;
- `config.tmpl.json`: a template with the placeholders `{{ADDRESS}}`, `{{PORT}}`, `{{UUID}}`, `{{FLOW}}`,
  `{{SNI}}`, `{{FP}}`, `{{PBK}}`, `{{SID}}`, `{{SPX}}`; the http inbound must stay on port 3128;
- `config.json`, `config.json.prev`: generated, stored in the volume.

## Logs

Lines are in logfmt format, easy to filter by `component=sub-update`:

```
level=info component=sub-update msg="config updated" server="NL-1"
level=error component=sub-update msg="tunnel check failed with new config, rolled back" server="DE-2" rollback_ok="True"
```

## License

This repository is licensed under the [MIT License](LICENSE).

### Third-party software

The Docker image built from this repository bundles third-party software under its own licenses:

| Component | License | Source |
|-----------|---------|--------|
| [Xray-core](https://github.com/XTLS/Xray-core) (`xray` binary, unmodified) | MPL-2.0 | https://github.com/XTLS/Xray-core |
| [Alpine Linux](https://alpinelinux.org) base image | various (MIT for musl) | https://gitlab.alpinelinux.org/alpine/aports |
| Python 3 (Alpine package) | PSF-2.0 | https://www.python.org/downloads/source/ |
| ca-certificates (Alpine package) | MPL-2.0 AND MIT | https://gitlab.alpinelinux.org/alpine/ca-certificates |

`update-sub.py` runs `xray` as a separate process and does not include or modify any Xray-core code.
