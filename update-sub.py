#!/usr/bin/python3 -I
"""xray supervisor with config auto-update from a VLESS subscription.

Every UPDATE_INTERVAL seconds: subscription → first vless:// with security=reality →
config.tmpl.json → xray -test → config.json → restart the child xray →
tunnel check (HTTPS to CHECK_HOST via the local HTTP inbound),
rolling back to the previous config on failure.
If xray crashes on its own, we exit and docker restarts the container.
`update-sub check` is a healthcheck that runs the same tunnel check.
"""
import base64
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

DIR = "/etc/xray"
CONFIG = os.path.join(DIR, "config.json")
TMPL = os.path.join(DIR, "config.tmpl.json")
NEW = os.path.join(DIR, "config.new.json")  # xray -test detects the format from the file extension
PREV = os.path.join(DIR, "config.json.prev")
SUBENV = os.path.join(DIR, "subscription.env")
INTERVAL = int(os.environ.get("UPDATE_INTERVAL", "3600"))
CHECK_HOST = os.environ.get("CHECK_HOST", "api.telegram.org")
PROXY_PORT = 3128  # http-in from config.tmpl.json


def log(level, msg, **kv):
    extra = "".join(f' {k}="{v}"' for k, v in kv.items())
    print(f'level={level} component=sub-update msg="{msg}"{extra}', flush=True)


class UpdateError(Exception):
    def __init__(self, msg, **kv):
        super().__init__(msg)
        self.kv = kv


def clean(s):
    return str(s).replace('"', "'").replace("\n", " ")[-300:]


def fetch_links(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Happ/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode().strip()
    except Exception as e:
        raise UpdateError("subscription fetch failed", error=clean(e))
    if "://" not in body:  # some providers serve the list in base64
        body = base64.b64decode(body + "=" * (-len(body) % 4)).decode()
    return body.split()


def pick(links):
    for link in links:
        p = urllib.parse.urlsplit(link)
        q = dict(urllib.parse.parse_qsl(p.query))
        if p.scheme == "vless" and q.get("security") == "reality" and q.get("type", "tcp") == "tcp":
            return p, q
    raise UpdateError("no vless+reality+tcp server in subscription", links=len(links))


def render(p, q):
    values = {
        "ADDRESS": p.hostname, "PORT": str(p.port or 443), "UUID": p.username,
        "FLOW": q.get("flow", ""), "SNI": q["sni"], "FP": q.get("fp", "chrome"),
        "PBK": q["pbk"], "SID": q.get("sid", ""), "SPX": q.get("spx", "/"),
    }
    with open(TMPL) as f:
        text = f.read()
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    if "{{" in text:
        raise UpdateError("unfilled placeholder in template")
    # fragment like <account id>@provider#Name; only the name goes to the log
    return text, urllib.parse.unquote(p.fragment).rsplit("#", 1)[-1]


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write(path, text):
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.replace(path + ".tmp", path)


def tunnel_ok(timeout=10):
    # CONNECT via our own http-in; xray returns 200 immediately, only the TLS handshake proves the tunnel
    try:
        with socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=timeout) as raw:
            raw.sendall(f"CONNECT {CHECK_HOST}:443 HTTP/1.1\r\nHost: {CHECK_HOST}:443\r\n\r\n".encode())
            if raw.recv(1024).split(b"\r\n", 1)[0].split()[1:2] != [b"200"]:
                return False
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(raw, server_hostname=CHECK_HOST) as s:
                s.sendall(f"HEAD / HTTP/1.1\r\nHost: {CHECK_HOST}\r\nConnection: close\r\n\r\n".encode())
                return s.recv(9).startswith(b"HTTP/")
    except OSError:
        return False


def tunnel_ok_retry(attempts=5):
    for _ in range(attempts):
        if tunnel_ok():
            return True
        time.sleep(2)
    return False


class Xray:
    def __init__(self):
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(["xray", "run", "-config", CONFIG])

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    def restart(self):
        self.stop()
        self.start()


def update(xr):
    env = dict(l.strip().split("=", 1) for l in open(SUBENV) if "=" in l)
    text, name = render(*pick(fetch_links(env["SUB_URL"])))
    current = read(CONFIG)
    if text == current:
        if xr.proc is None:
            xr.start()
        if not tunnel_ok_retry():
            # same server in the subscription but the tunnel is down; nothing to fix, just report it
            raise UpdateError("config unchanged but tunnel check failed", server=name, check=CHECK_HOST)
        log("info", "config unchanged", server=name)
        return

    write(NEW, text)
    r = subprocess.run(["xray", "run", "-test", "-config", NEW], capture_output=True, text=True)
    if r.returncode != 0:
        raise UpdateError("xray -test failed", server=name, error=clean(r.stdout + r.stderr))
    if current:
        write(PREV, current)
    os.replace(NEW, CONFIG)
    xr.restart()
    if tunnel_ok_retry():
        log("info", "config updated", server=name)
        return
    if not current:
        raise UpdateError("tunnel check failed with new config, nothing to roll back to", server=name)
    write(CONFIG, current)
    xr.restart()
    raise UpdateError("tunnel check failed with new config, rolled back",
                      server=name, rollback_ok=tunnel_ok_retry())


def main():
    if sys.argv[1:] == ["check"]:
        sys.exit(0 if tunnel_ok() else 1)

    os.umask(0o077)
    xr = Xray()

    def shutdown(signum, _frame):
        xr.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if read(CONFIG):  # bring the tunnel up right away on the previous config, without waiting for the subscription
        xr.start()
    next_update = time.monotonic()
    while True:
        if time.monotonic() >= next_update:
            try:
                update(xr)
            except UpdateError as e:
                log("error", str(e), **e.kv)
            except Exception as e:
                log("error", "update crashed", error=clean(repr(e)))
            next_update = time.monotonic() + INTERVAL
        if xr.proc is not None and xr.proc.poll() is not None:
            log("error", "xray exited", code=xr.proc.returncode)
            sys.exit(1)
        time.sleep(1)


if __name__ == "__main__":
    main()
