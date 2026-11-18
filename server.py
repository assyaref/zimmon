#!/usr/bin/env python3
"""ZIMMON - read-only monitoring dashboard for a Zimbra VPS.

Runs as root (needs `su - zimbra` for zmcontrol/postqueue and read access to logs).
Every command is a fixed string; no user input ever reaches a shell.
"""
import base64
import fcntl
import hashlib
import hmac
import pty
import select
import signal
import struct
import termios
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
import collections
import ipaddress

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
    "/vendor/xterm.js": ("vendor/xterm.js", "application/javascript; charset=utf-8"),
    "/vendor/xterm.css": ("vendor/xterm.css", "text/css; charset=utf-8"),
}

# Web terminal (root shell). Disable with "terminal": false in /etc/zimmon/config.json
TERMINAL_ON = CONF.get("terminal", True) is not False
TERM_IDLE = int(CONF.get("terminal_idle_min", 30)) * 60
TERM_MAX = 3
TERM_LOG = "/var/log/zimmon-terminal.log"
TERM_COUNT = [0]
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

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


# ---------------------------------------------------------------- attack detection
WINDOW = 24 * 3600
ACTIVE_WINDOW = 15 * 60
ACTIVE_MIN = 10          # failures from one IP within 15 min = active brute force
BREACH_MIN_FAILS = 5     # an IP with this many failures that then logs in = possible breach
MAX_EVENTS = 300_000

IP = r"([0-9a-fA-F:.]+)"
SSHD = r"sshd(?:-session)?\[\d+\]: "
RULES = [
    # (log key, category, regex, group of ip, group of target account or None)
    ("zimbra", "smtp", re.compile(r"\[" + IP + r"\]: SASL \S+ authentication failed"), 1, None),
    ("zimbra", "relay", re.compile(r"NOQUEUE: reject: RCPT from \S*\[" + IP + r"\].*Relay access denied"), 1, None),
    ("zimbra", "bot", re.compile(r"postscreen\[\d+\]: (?:PREGREET|DNSBL rank|BARE NEWLINE|COMMAND PIPELINING|NON-SMTP COMMAND)\b.*?\[" + IP + r"\]"), 1, None),
    ("auth", "ssh", re.compile(SSHD + r"Failed (?:password|publickey) for (?:invalid user )?(\S+) from " + IP), 2, 1),
    ("auth", "ssh", re.compile(SSHD + r"Invalid user (\S*) from " + IP), 2, 1),
]
SUBMIT_RE = re.compile(r"client=\S*\[" + IP + r"\], sasl_method=\S+, sasl_username=(\S+)")
AUDIT_RE = re.compile(r"cmd=(?:Admin)?Auth; account=([^;]*);(?:.*?protocol=(\w+);)?")
AUDIT_IP_RE = re.compile(r"oip=" + IP + r"[;\]]")
AUDIT_IP2_RE = re.compile(r"\[ip=" + IP + r"[;\]]")
SSH_OK_RE = re.compile(SSHD + r"Accepted \S+ for (\S+) from " + IP)

CATEGORIES = {
    "smtp": "SMTP AUTH", "mailauth": "WEBMAIL/IMAP/POP", "ssh": "SSH",
    "relay": "RELAY SPAM", "bot": "BOT/SPAMMER", "zimmon": "ZIMMON LOGIN",
}


def _parse_ts(line, now):
    try:
        if line[:4].isdigit():  # 2026-11-19 01:00:00 / 2026-11-19T01:00:00
            return time.mktime(time.strptime(line[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S"))
        t = time.mktime(time.strptime(time.strftime("%Y") + " " + line[:15], "%Y %b %d %H:%M:%S"))
        if t > now + 86400:  # December log read in January
            t = time.mktime(time.strptime(str(int(time.strftime("%Y")) - 1) + " " + line[:15], "%Y %b %d %H:%M:%S"))
        return t
    except Exception:
        return None


class LogTail:
    """Returns only lines appended since the previous call; survives rotation."""

    def __init__(self, path, bootstrap=150_000_000):
        self.path, self.boot, self.ino, self.pos = path, bootstrap, None, 0

    def read(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        lines = []
        skip_first = False
        if self.ino is None:  # first run: also read the previous rotated file
            self.ino = st.st_ino
            self.pos = max(0, st.st_size - self.boot)
            skip_first = self.pos > 0
            if self.pos == 0 and os.path.exists(self.path + ".1"):
                lines += self._chunk(self.path + ".1", max(0, os.path.getsize(self.path + ".1") - self.boot), True)[0]
        elif st.st_ino != self.ino or st.st_size < self.pos:  # rotated
            self.ino, self.pos = st.st_ino, 0
        if st.st_size > self.pos:
            new, used = self._chunk(self.path, self.pos, skip_first)
            self.pos += used
            lines += new
        return lines

    @staticmethod
    def _chunk(path, pos, skip_first):
        with open(path, "rb") as f:
            f.seek(pos)
            data = f.read(300_000_000)
        cut = data.rfind(b"\n") + 1
        lines = data[:cut].decode("utf-8", "replace").splitlines()
        return (lines[1:] if skip_first else lines), cut


class AsnLookup:
    """IP -> ASN via Team Cymru's bulk whois (whois.cymru.com:43), cached in memory.

    Disable with "asn_lookup": false in /etc/zimmon/config.json.
    """

    HOST, PORT = "whois.cymru.com", 43
    TTL, RETRY = 7 * 86400, 3600   # keep answers a week; retry failures after an hour
    BATCH, MAX_CACHE = 1000, 50_000

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.cache = {}   # ip -> (expires, info dict or None)

    @staticmethod
    def _public(ip):
        try:
            return ipaddress.ip_address(ip).is_global
        except ValueError:
            return False

    def get(self, ip):
        hit = self.cache.get(ip)
        return hit[1] if hit else None

    def refresh(self, ips):
        """Look up IPs not yet cached (or expired). Returns True if anything new was learned."""
        if not self.enabled:
            return False
        now = time.time()
        todo = [ip for ip in ips if self._public(ip) and (ip not in self.cache or self.cache[ip][0] < now)]
        if not todo:
            return False
        todo = todo[:self.BATCH]
        try:
            found = self._query(todo)
        except Exception as e:
            print("asn:", e, flush=True)
            found = {}
        for ip in todo:
            info = found.get(ip)
            self.cache[ip] = (now + (self.TTL if info else self.RETRY), info)
        if len(self.cache) > self.MAX_CACHE:
            for ip in sorted(self.cache, key=lambda k: self.cache[k][0])[:len(self.cache) - self.MAX_CACHE]:
                del self.cache[ip]
        return bool(found)

    def _query(self, ips):
        req = ("begin\nverbose\n" + "\n".join(ips) + "\nend\n").encode()
        with socket.create_connection((self.HOST, self.PORT), timeout=20) as s:
            s.settimeout(30)
            s.sendall(req)
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > 5_000_000:
                    break
        out = {}
        # AS | IP | BGP Prefix | CC | Registry | Allocated | AS Name
        for line in data.decode("utf-8", "replace").splitlines():
            f = [x.strip() for x in line.split("|")]
            if len(f) < 7 or not f[0].isdigit():
                continue
            out[f[1]] = {"asn": int(f[0]), "prefix": f[2], "cc": f[3].upper()[:2], "name": f[6][:80]}
        return out


class Detector:
    def __init__(self, host, suspect_send_per_hour=150, asn_lookup=True):
        self.host = host
        self.asn = AsnLookup(asn_lookup)
        self.suspect = suspect_send_per_hour
        self.lock = threading.Lock()
        self.events = collections.deque(maxlen=MAX_EVENTS)  # (ts, cat, ip, who)
        self.sent_by_day = collections.Counter()
        self.tails = {
            "zimbra": LogTail("/var/log/zimbra.log"),
            "audit": LogTail("/opt/zimbra/log/audit.log"),
            "auth": LogTail("/var/log/auth.log"),
        }
        self.own = {"127.0.0.1", "::1"}
        try:
            self.own.update(subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split())
            self.own.add(socket.gethostbyname(host))
        except Exception:
            pass
        self.result = {}

    def record(self, cat, ip, who=""):
        """Called by the web server for ZIMMON's own failed logins."""
        with self.lock:
            self.events.append((time.time(), cat, ip, who))

    # ------------------------------------------------------------ scanning
    def scan(self):
        now = time.time()
        new = []
        for key, tail in self.tails.items():
            for line in tail.read():
                self._line(key, line, now, new)
        with self.lock:
            self.events.extend(new)
            while self.events and self.events[0][0] < now - WINDOW:
                self.events.popleft()
            self.result = self._analyze(now)
            ips = [r["ip"] for r in self.result["active"]] + [r["ip"] for r in self.result["top"]] + \
                  [b["ip"] for b in self.result["breaches"]] + self._ips_by_count()
        if self.asn.refresh(list(dict.fromkeys(ips))):  # network call: done without holding the lock
            with self.lock:
                self.result = self._analyze(now)

    def _ips_by_count(self):
        c = collections.Counter(ip for _, cat, ip, _ in self.events if cat not in ("ok", "submit") and ip not in self.own)
        return [ip for ip, _ in c.most_common()]

    def _line(self, key, line, now, out):
        if key == "zimbra":
            if "status=sent" in line:
                ts = _parse_ts(line, now)
                if ts:
                    self.sent_by_day[time.strftime("%Y-%m-%d", time.localtime(ts))] += 1
                return
            if "sasl_username=" in line:
                m = SUBMIT_RE.search(line)
                ts = m and _parse_ts(line, now)
                if ts:
                    out.append((ts, "submit", m.group(1), m.group(2)))
                return
        if key == "audit":
            if "cmd=Auth" not in line and "cmd=AdminAuth" not in line:
                return
            m = AUDIT_RE.search(line)
            ipm = AUDIT_IP_RE.search(line) or AUDIT_IP2_RE.search(line)
            ts = _parse_ts(line, now)
            if m and ipm and ts:
                cat = "mailauth" if "error=" in line else "ok"
                out.append((ts, cat, ipm.group(1), (m.group(1) or "?") + ("/" + m.group(2) if m.group(2) else "")))
            return
        if key == "auth" and "Accepted" in line:
            m = SSH_OK_RE.search(line)
            ts = m and _parse_ts(line, now)
            if ts:
                out.append((ts, "ok", m.group(2), m.group(1) + "/ssh"))
            return
        for log, cat, rx, ip_g, who_g in RULES:
            if log != key:
                continue
            m = rx.search(line)
            if m:
                ts = _parse_ts(line, now)
                if ts:
                    out.append((ts, cat, m.group(ip_g), m.group(who_g) if who_g else ""))
                return

    # ------------------------------------------------------------ analysis
    def _analyze(self, now):
        c24 = collections.Counter()
        c1 = collections.Counter()
        timeline = [0] * 24
        ips = {}
        targets = collections.Counter()
        submits = collections.Counter()
        oks = []
        for ts, cat, ip, who in self.events:
            if cat == "submit":
                if ts >= now - 3600:
                    submits[who] += 1
                if ip not in self.own:
                    oks.append((ts, ip, who + "/smtp"))
                continue
            if cat == "ok":
                if ip not in self.own:
                    oks.append((ts, ip, who))
                continue
            if ip in self.own:
                continue
            c24[cat] += 1
            if ts >= now - 3600:
                c1[cat] += 1
            h = int((now - ts) // 3600)
            if 0 <= h < 24:
                timeline[23 - h] += 1
            d = ips.setdefault(ip, {"ip": ip, "n": 0, "recent": 0, "cats": collections.Counter(), "last": 0, "who": collections.Counter()})
            d["n"] += 1
            d["cats"][cat] += 1
            d["last"] = max(d["last"], ts)
            if ts >= now - ACTIVE_WINDOW:
                d["recent"] += 1
            if who:
                d["who"][who] += 1
                targets[who.split("/")[0]] += 1

        active = sorted((d for d in ips.values() if d["recent"] >= ACTIVE_MIN), key=lambda d: -d["recent"])
        breaches = []
        seen = set()
        for ts, ip, who in oks:
            d = ips.get(ip)
            if d and d["n"] >= BREACH_MIN_FAILS and (ip, who) not in seen:
                seen.add((ip, who))
                breaches.append({"ip": ip, "account": who, "fails": d["n"], "ts": int(ts)})
        suspicious = [{"account": u, "per_hour": n} for u, n in submits.most_common() if n >= self.suspect]

        total24, total1 = sum(c24.values()), sum(c1.values())
        if breaches or suspicious:
            level = "CRITICAL"
        elif active:
            level = "HIGH"
        elif total1 >= 50:
            level = "ELEVATED"
        elif total24:
            level = "LOW"
        else:
            level = "CLEAR"

        def ip_row(d):
            return {"ip": d["ip"], "n": d["n"], "recent": d["recent"], "last": int(d["last"]),
                    "cats": [CATEGORIES.get(c, c) for c, _ in d["cats"].most_common()],
                    "who": [w for w, _ in d["who"].most_common(3)], "asn": self.asn.get(d["ip"])}

        asns = {}
        for d in ips.values():
            info = self.asn.get(d["ip"])
            if not info:
                continue
            a = asns.setdefault(info["asn"], {"asn": info["asn"], "name": info["name"], "cc": info["cc"], "n": 0, "ips": 0, "recent": 0})
            a["n"] += d["n"]
            a["ips"] += 1
            a["recent"] += d["recent"]
        for b in breaches:
            b["asn"] = self.asn.get(b["ip"])

        return {
            "level": level,
            "categories": [{"key": k, "label": v, "h24": c24[k], "h1": c1[k]} for k, v in CATEGORIES.items()],
            "total24": total24, "total1": total1, "unique_ips": len(ips),
            "timeline": timeline,
            "active": [ip_row(d) for d in active[:10]],
            "top": [ip_row(d) for d in sorted(ips.values(), key=lambda d: -d["n"])[:15]],
            "targets": [{"account": a, "n": n} for a, n in targets.most_common(8)],
            "asns": sorted(asns.values(), key=lambda a: -a["n"])[:10],
            "asn_on": self.asn.enabled,
            "breaches": breaches[:10],
            "suspicious": suspicious[:10],
            "sent_today": self.sent_by_day[time.strftime("%Y-%m-%d")],
            "ts": int(now),
        }

    def loop(self, interval=30):
        while True:
            try:
                self.scan()
            except Exception as e:
                print("attacks:", e, flush=True)
            time.sleep(interval)

    def snapshot(self):
        with self.lock:
            return self.result


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


# ---------------------------------------------------------------- websocket terminal
def term_log(msg):
    try:
        with open(TERM_LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def ws_recv(sock):
    b1, b2 = recv_exact(sock, 2)
    op, ln = b1 & 0x0F, b2 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", recv_exact(sock, 2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", recv_exact(sock, 8))[0]
    if ln > 1 << 20:
        raise ConnectionError("frame too large")
    mask = recv_exact(sock, 4) if b2 & 0x80 else b""
    data = recv_exact(sock, ln) if ln else b""
    if mask and data:
        m = (mask * (ln // 4 + 1))[:ln]
        data = (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(ln, "big")
    return op, data


def ws_send(sock, op, data=b""):
    ln = len(data)
    if ln < 126:
        head = struct.pack(">BB", 0x80 | op, ln)
    elif ln < 65536:
        head = struct.pack(">BBH", 0x80 | op, 126, ln)
    else:
        head = struct.pack(">BBQ", 0x80 | op, 127, ln)
    sock.sendall(head + data)


def run_terminal(sock, ip):
    """Bridge a websocket to a root login shell on a pty. Single thread (SSL objects are not thread-safe)."""
    pid, fd = pty.fork()
    if pid == 0:  # child: never return into the server code
        try:
            os.chdir("/root")
            env = {"TERM": "xterm-256color", "HOME": "/root", "USER": "root", "LOGNAME": "root",
                   "SHELL": "/bin/bash", "LANG": "C.UTF-8",
                   "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
            os.execve("/bin/bash", ["bash", "-l"], env)
        finally:
            os._exit(1)
    term_log(f"OPEN  ip={ip} pid={pid}")
    started = last = time.time()
    reason = "closed"
    try:
        while True:
            ready = select.select([sock, fd], [], [], 20)[0]
            if sock.pending() or sock in ready:
                op, data = ws_recv(sock)
                last = time.time()
                if op == 0x8:  # close
                    break
                if op == 0x9:  # ping
                    ws_send(sock, 0xA, data)
                elif op in (0x1, 0x2) and data:
                    kind, body = data[:1], data[1:]
                    if kind == b"i":
                        os.write(fd, body)
                    elif kind == b"r":
                        size = json.loads(body)
                        rows, cols = max(1, min(int(size["r"]), 500)), max(1, min(int(size["c"]), 500))
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            if fd in ready:
                try:
                    out = os.read(fd, 65536)
                except OSError:
                    out = b""
                if not out:
                    reason = "shell exited"
                    break
                ws_send(sock, 0x2, out)
            if time.time() - last > TERM_IDLE:
                ws_send(sock, 0x2, f"\r\n\x1b[33m[ZIMMON] Sesi ditutup: tidak aktif {TERM_IDLE // 60} menit.\x1b[0m\r\n".encode())
                reason = "idle timeout"
                break
    except Exception as e:
        reason = f"error {type(e).__name__}"
    finally:
        try:
            ws_send(sock, 0x8, struct.pack(">H", 1000))
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGHUP)
            time.sleep(0.2)
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            os.waitpid(pid, 0)
        except Exception:
            pass
        os.close(fd)
        term_log(f"CLOSE ip={ip} pid={pid} dur={int(time.time() - started)}s reason={reason}")


# ---------------------------------------------------------------- http
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ZIMMON"
    sys_version = ""
    protocol_version = "HTTP/1.1"  # required for the websocket 101 response
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
                         "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
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
            data["attacks"] = DET.snapshot() if DET else {}
            return self.send_json(200, data)
        if path == "/api/session":
            return self.send_json(200, {"ok": bool(self.session()), "version": VERSION, "terminal": TERMINAL_ON})
        if path == "/api/term":
            return self.open_terminal()
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

    def open_terminal(self):
        if not TERMINAL_ON:
            return self.send_json(403, {"error": "terminal disabled"})
        if not self.session():
            return self.send_json(401, {"error": "unauthorized"})
        # block cross-site websocket hijacking: the page must come from this same origin
        if self.headers.get("Origin", "") != "https://" + self.headers.get("Host", "-"):
            return self.send_json(403, {"error": "bad origin"})
        key = self.headers.get("Sec-WebSocket-Key")
        if self.headers.get("Upgrade", "").lower() != "websocket" or not key:
            return self.send_json(400, {"error": "websocket required"})
        with LOCK:
            if TERM_COUNT[0] >= TERM_MAX:
                return self.send_json(429, {"error": "too many terminals"})
            TERM_COUNT[0] += 1
        try:
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            self.wfile.flush()
            self.connection.settimeout(None)
            run_terminal(self.connection, self.client_address[0])
        finally:
            with LOCK:
                TERM_COUNT[0] -= 1
            self.close_connection = True

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
            if DET:
                DET.record("zimmon", ip, user[:64])
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


DET = None


def main():
    global DET
    DET = Detector(HOST, int(CONF.get("suspect_send_per_hour", 150)), CONF.get("asn_lookup", True) is not False)
    threading.Thread(target=DET.loop, daemon=True).start()
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
