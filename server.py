#!/usr/bin/env python3
"""ZIMMON - read-only monitoring dashboard for a Zimbra VPS.

Runs as root (needs `su - zimbra` for zmcontrol/postqueue and read access to logs).
Every command is a fixed string; no user input ever reaches a shell.
"""
import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
import calendar

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")
CONF = json.load(open(os.environ.get("ZIMMON_CONF", "/etc/zimmon/config.json")))
try:
    VERSION = open(os.path.join(BASE, "VERSION")).read().strip()
except Exception:
    VERSION = "dev"
HOST = CONF.get("host", "mail.ksolrs-tal.com")
PORT = int(CONF.get("port", 9443))
SESSION_TTL = 12 * 3600
MAX_FAILS, FAIL_WINDOW = 5, 900

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.json": ("manifest.json", "application/manifest+json"),
    "/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}

PORTS = [
    (25, "SMTP"), (465, "SMTPS"), (587, "SUBMIT"), (143, "IMAP"), (993, "IMAPS"),
    (110, "POP3"), (995, "POP3S"), (443, "HTTPS"), (7071, "ADMIN"), (8080, "MAILBOXD"),
    (389, "LDAP"), (7306, "MYSQL"), (3310, "CLAMD"), (10024, "AMAVIS"), (11211, "MEMCACHE"),
]

LOCK = threading.Lock()
STATE = {
    "host": HOST, "version": VERSION, "services": [], "svc_ts": 0, "queue": None, "accounts": None,
    "cert": None, "ports": [], "alerts": [], "mail": {}, "procs": [], "hist": [],
}
SESSIONS = {}
FAILS = {}


# ---------------------------------------------------------------- helpers
def run(cmd, timeout=90):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def zrun(command, timeout=120):
    return run(["su", "-", "zimbra", "-c", command], timeout)


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def tail_bytes(path, n=3_000_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace").splitlines()[1:]
    except Exception:
        return []


def cpu_times():
    vals = list(map(int, read("/proc/stat").splitlines()[0].split()[1:]))
    return vals[3] + vals[4], sum(vals)


def meminfo():
    m = {}
    for line in read("/proc/meminfo").splitlines():
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0]) * 1024
    return m


def net_bytes():
    rx = tx = 0
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, data = line.split(":", 1)
        if name.strip() == "lo":
            continue
        f = data.split()
        rx += int(f[0])
        tx += int(f[8])
    return rx, tx


def port_open(port):
    for host in ("127.0.0.1", HOST):
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except Exception:
            continue
    return False


def cert_files():
    if CONF.get("cert") and CONF.get("key"):
        return CONF["cert"], CONF["key"]
    le = f"/etc/letsencrypt/live/{HOST}"
    if os.path.exists(le + "/fullchain.pem"):
        return le + "/fullchain.pem", le + "/privkey.pem"
    z = "/opt/zimbra/ssl/zimbra/commercial"
    os.makedirs("/run/zimmon", exist_ok=True)
    full = "/run/zimmon/fullchain.pem"
    with open(full, "w") as out:
        out.write(read(z + "/commercial.crt").strip() + "\n" + read(z + "/commercial_ca.crt"))
    os.chmod(full, 0o600)
    return full, z + "/commercial.key"


# ---------------------------------------------------------------- collectors
def fast_loop():
    prev_cpu = cpu_times()
    prev_net = (net_bytes(), time.time())
    while True:
        time.sleep(5)
        try:
            idle, total = cpu_times()
            d_total = total - prev_cpu[1]
            cpu = 100.0 * (1 - (idle - prev_cpu[0]) / d_total) if d_total else 0.0
            prev_cpu = (idle, total)

            m = meminfo()
            mem_used = m["MemTotal"] - m.get("MemAvailable", m["MemFree"])
            swap_used = m["SwapTotal"] - m["SwapFree"]
            st = os.statvfs("/")
            d_size = st.f_blocks * st.f_frsize
            d_avail = st.f_bavail * st.f_frsize
            d_used = (st.f_blocks - st.f_bfree) * st.f_frsize

            (rx, tx), now = net_bytes(), time.time()
            (prx, ptx), pt = prev_net
            dt = max(now - pt, 1e-3)
            prev_net = ((rx, tx), now)

            load = read("/proc/loadavg").split()[:3]
            uptime = float(read("/proc/uptime").split()[0])
            ports = [{"port": p, "name": n, "up": port_open(p)} for p, n in PORTS]

            sample = {
                "t": int(now),
                "cpu": round(cpu, 1),
                "mem": round(100 * mem_used / m["MemTotal"], 1),
                "disk": round(100 * d_used / (d_used + d_avail), 1) if d_used + d_avail else 0,
            }
            with LOCK:
                STATE.update({
                    "cpu": sample["cpu"], "cores": os.cpu_count(),
                    "mem": {"used": mem_used, "total": m["MemTotal"], "pct": sample["mem"]},
                    "swap": {"used": swap_used, "total": m["SwapTotal"],
                             "pct": round(100 * swap_used / m["SwapTotal"], 1) if m["SwapTotal"] else 0},
                    "disk": {"used": d_used, "avail": d_avail, "size": d_size, "pct": sample["disk"]},
                    "net": {"rx": (rx - prx) / dt, "tx": (tx - ptx) / dt},
                    "load": load, "uptime": uptime, "ports": ports, "ts": int(now),
                })
                STATE["hist"] = (STATE["hist"] + [sample])[-120:]
        except Exception as e:  # keep the loop alive no matter what
            print("fast_loop:", e, flush=True)


SVC_RE = re.compile(r"^\s+(.+?)\s{2,}(Running|Stopped|Not Running|\w+)\s*$")


def slow_loop():
    n = 0
    while True:
        try:
            services = []
            for line in zrun("zmcontrol status", 180).splitlines():
                mm = SVC_RE.match(line)
                if mm and not line.startswith("Host"):
                    services.append({"name": mm.group(1).strip(), "up": mm.group(2) == "Running"})

            q = zrun("/opt/zimbra/common/sbin/postqueue -p | tail -n1").strip()
            mq = re.search(r"in (\d+) Request", q)
            queue = 0 if "empty" in q else (int(mq.group(1)) if mq else None)

            cert = None
            try:
                crt, _ = cert_files()
                end = run(["openssl", "x509", "-enddate", "-noout", "-in", crt]).strip().split("=", 1)[1]
                exp = calendar.timegm(time.strptime(end, "%b %d %H:%M:%S %Y %Z"))
                cert = {"expires": end, "days": int((exp - time.time()) // 86400)}
            except Exception:
                pass

            lines = tail_bytes("/var/log/zimbra.log")
            alert_re = re.compile(r"crit|fatal|panic|error|disk warning|no space", re.I)
            alerts = [l[:220] for l in lines if alert_re.search(l)][-12:]
            mail = {
                "sent": sum("status=sent" in l for l in lines),
                "deferred": sum("status=deferred" in l for l in lines),
                "bounced": sum("status=bounced" in l for l in lines),
                "authfail": sum("authentication failed" in l for l in lines),
                "rejected": sum("NOQUEUE: reject" in l for l in lines),
                "window": (lines[0][:15] + " → " + lines[-1][:15]) if lines else "",
            }

            procs = []
            for line in run(["ps", "-eo", "pid,user,pcpu,pmem,comm", "--sort=-pcpu"]).splitlines()[1:9]:
                f = line.split(None, 4)
                if len(f) == 5:
                    procs.append({"pid": f[0], "user": f[1], "cpu": f[2], "mem": f[3], "cmd": f[4][:24]})

            accounts = None
            if n % 10 == 0:
                out = zrun("zmprov -l gaa", 180)
                accounts = len([l for l in out.splitlines() if "@" in l]) or None

            with LOCK:
                STATE.update({"services": services, "svc_ts": int(time.time()), "queue": queue,
                              "cert": cert, "alerts": alerts, "mail": mail, "procs": procs})
                if accounts is not None:
                    STATE["accounts"] = accounts
        except Exception as e:
            print("slow_loop:", e, flush=True)
        n += 1
        time.sleep(60)


# ---------------------------------------------------------------- auth
def check_login(user, password):
    salt = bytes.fromhex(CONF["salt"])
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, CONF["iter"]).hex()
    ok_user = hmac.compare_digest(user.encode(), CONF["user"].encode())
    ok_pass = hmac.compare_digest(dk, CONF["hash"])
    return ok_user and ok_pass


def locked(ip):
    now = time.time()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < FAIL_WINDOW]
    return len(FAILS[ip]) >= MAX_FAILS


# ---------------------------------------------------------------- http
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ZIMMON"
    sys_version = ""
    timeout = 20

    def setup(self):
        super().setup()  # applies self.timeout to the socket
        self.connection.do_handshake()  # TLS handshake in the worker thread, not in accept()

    def log_message(self, fmt, *args):
        pass

    def headers_common(self):
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                         "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")

    def send_json(self, code, obj, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.headers_common()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def session(self):
        raw = self.headers.get("Cookie", "")
        mm = re.search(r"zm_sid=([A-Za-z0-9_\-]+)", raw)
        if not mm:
            return None
        sid = mm.group(1)
        exp = SESSIONS.get(sid)
        if exp and exp > time.time():
            return sid
        SESSIONS.pop(sid, None)
        return None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/status":
            if not self.session():
                return self.send_json(401, {"error": "unauthorized"})
            with LOCK:
                data = json.loads(json.dumps(STATE))
            return self.send_json(200, data)
        if path == "/api/session":
            return self.send_json(200, {"ok": bool(self.session()), "version": VERSION})
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            try:
                with open(os.path.join(STATIC, name), "rb") as f:
                    body = f.read()
                if name == "sw.js":  # new version => new service worker => old cache purged
                    body = body.replace(b"__VERSION__", VERSION.encode())
            except Exception:
                return self.send_json(404, {"error": "not found"})
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.headers_common()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        ip = self.client_address[0]
        if path == "/api/login":
            if locked(ip):
                return self.send_json(429, {"error": "locked"})
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 4096)
                req = json.loads(self.rfile.read(n) or b"{}")
                user, pw = str(req.get("user", ""))[:128], str(req.get("pass", ""))[:256]
            except Exception:
                return self.send_json(400, {"error": "bad request"})
            if check_login(user, pw):
                FAILS.pop(ip, None)
                sid = secrets.token_urlsafe(32)
                SESSIONS[sid] = time.time() + SESSION_TTL
                for k in [k for k, v in SESSIONS.items() if v < time.time()]:
                    SESSIONS.pop(k, None)
                cookie = f"zm_sid={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; Secure; SameSite=Strict"
                return self.send_json(200, {"ok": True}, cookie)
            FAILS.setdefault(ip, []).append(time.time())
            time.sleep(1)
            left = max(0, MAX_FAILS - len(FAILS[ip]))
            return self.send_json(401, {"error": "denied", "left": left})
        if path == "/api/logout":
            sid = self.session()
            if sid:
                SESSIONS.pop(sid, None)
            return self.send_json(200, {"ok": True}, "zm_sid=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict")
        self.send_json(404, {"error": "not found"})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # TLS handshake noise from scanners


def main():
    threading.Thread(target=fast_loop, daemon=True).start()
    threading.Thread(target=slow_loop, daemon=True).start()
    crt, key = cert_files()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(crt, key)
    srv = Server(("0.0.0.0", PORT), Handler)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
    print(f"ZIMMON listening on https://{HOST}:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
