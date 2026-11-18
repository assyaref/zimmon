#!/usr/bin/env python3
"""ZIMMON two-factor setup (TOTP, RFC 6238). Run via install.sh:

    bash /opt/zimmon/install.sh --2fa       enable / pair a new phone
    bash /opt/zimmon/install.sh --2fa-off   disable
    python3 /opt/zimmon/twofa.py status
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse

CONF = os.environ.get("ZIMMON_CONF", "/etc/zimmon/config.json")


def totp_code(secret, step):  # same algorithm as server.py
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000)


def verify(secret, code):
    now = int(time.time() // 30)
    return any(hmac.compare_digest(totp_code(secret, s), code) for s in (now - 1, now, now + 1))


def load():
    with open(CONF) as f:
        return json.load(f)


def save(conf):
    tmp = CONF + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(conf, f, indent=2)
    os.replace(tmp, CONF)


def on():
    conf = load()
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    label = urllib.parse.quote("ZIMMON:%s@%s" % (conf.get("user", "admin"), conf.get("host", "zimmon")))
    uri = "otpauth://totp/%s?secret=%s&issuer=ZIMMON&digits=6&period=30" % (label, secret)
    print("\n== Aktifkan 2FA ZIMMON ==")
    print("Buka Google Authenticator / Microsoft Authenticator / Authy di HP, lalu tambah akun:\n")
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", uri])
        print("Scan QR di atas, atau masukkan kunci secara manual:")
    else:
        print("(Untuk menampilkan QR code: apt install qrencode, lalu jalankan lagi.)")
        print("Pilih 'Masukkan kunci penyiapan' / 'Enter a setup key':")
    print("  Nama akun : ZIMMON")
    print("  Kunci     : " + " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)))
    print("  Jenis     : Berbasis waktu (time-based)\n")
    print("Jam server (UTC): " + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + "  — harus sama dengan jam HP (selisih < 30 detik)\n")
    for _ in range(3):
        code = input("Masukkan kode 6 digit dari aplikasi untuk konfirmasi: ").replace(" ", "").strip()
        if len(code) == 6 and code.isdigit() and verify(secret, code):
            conf["totp_secret"] = secret
            save(conf)
            print("\n2FA AKTIF. Login berikutnya butuh password + kode dari aplikasi.")
            print("Simpan kunci di atas di tempat aman: itu satu-satunya cara memasang ulang di HP baru")
            print("tanpa akses ke VPS. Kalau HP hilang: bash /opt/zimmon/install.sh --2fa-off")
            return 0
        print("Kode salah. Cek jam HP dan pastikan kunci dimasukkan dengan benar.")
    print("\nGagal 3 kali: 2FA TIDAK diaktifkan, konfigurasi tidak diubah.")
    return 1


def off():
    conf = load()
    if conf.pop("totp_secret", None):
        save(conf)
        print("2FA dimatikan. Login cukup dengan password.")
    else:
        print("2FA memang tidak aktif.")
    return 0


def status():
    print("2FA: " + ("AKTIF" if load().get("totp_secret") else "TIDAK AKTIF"))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"on": on, "off": off, "status": status}.get(cmd, status)())
