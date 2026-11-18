#!/bin/bash
# ZIMMON installer — jalankan sebagai root di VPS Zimbra
set -e
[ "$(id -u)" = 0 ] || { echo "Jalankan sebagai root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/zimmon
PORT=9443
HOST=$(su - zimbra -c "zmhostname" 2>/dev/null || hostname -f)

setup_fail2ban() {
  echo "== Memasang & mengatur fail2ban =="
  if ! command -v fail2ban-client >/dev/null; then
    if   command -v apt-get >/dev/null; then apt-get update -qq && apt-get install -y fail2ban
    elif command -v dnf     >/dev/null; then dnf install -y fail2ban
    elif command -v yum     >/dev/null; then yum install -y epel-release 2>/dev/null; yum install -y fail2ban
    else echo "Tidak tahu package manager; pasang fail2ban manual lalu jalankan lagi."; return 1
    fi
  fi
  # empty filter: a jail that never matches a log, only takes manual bans from ZIMMON
  cat > /etc/fail2ban/filter.d/zimmon-empty.conf <<'F'
[Definition]
failregex =
ignoreregex =
F
  # Zimbra SMTP AUTH brute force (pola sama dengan deteksi ZIMMON) — tak bergantung versi fail2ban
  cat > /etc/fail2ban/filter.d/zimmon-smtp.conf <<'F'
[Definition]
failregex = \[<HOST>\]: SASL (?:LOGIN|PLAIN|(?:CRAM|DIGEST)-MD5) authentication failed
ignoreregex =
F
  : > /var/log/zimmon-manual.log
  cat > /etc/fail2ban/jail.d/zimmon.local <<'J'
# Dikelola oleh ZIMMON. Jangan diedit manual; ubah lalu jalankan: bash /opt/zimmon/install.sh --fail2ban
[DEFAULT]
# jangan pernah blokir diri sendiri / jaringan lokal
ignoreip = 127.0.0.1/8 ::1

# Dipakai tombol BLOKIR di aplikasi ZIMMON. Ban permanen, semua port.
[zimmon-manual]
enabled   = true
filter    = zimmon-empty
backend   = polling
logpath   = /var/log/zimmon-manual.log
maxretry  = 100000
findtime  = 1
bantime   = -1
banaction = %(banaction_allports)s

# Brute force SSH
[sshd]
enabled  = true
maxretry = 5
bantime  = 3600

# Brute force SMTP AUTH ke Zimbra (log Postfix Zimbra)
[zimbra-smtp]
enabled  = true
filter   = zimmon-smtp
logpath  = /var/log/zimbra.log
port     = smtp,465,submission,imap,imaps,pop3,pop3s
maxretry = 5
findtime = 600
bantime  = 3600
J
  systemctl enable fail2ban >/dev/null 2>&1
  systemctl restart fail2ban
  sleep 2
  if fail2ban-client ping >/dev/null 2>&1; then
    echo "fail2ban aktif. Jail: $(fail2ban-client status 2>/dev/null | grep 'Jail list' | cut -d: -f2)"
  else
    echo "fail2ban gagal start. Cek: fail2ban-client -t  dan  journalctl -u fail2ban -n 30"
    return 1
  fi
}

if [ "$1" = "--fail2ban" ]; then setup_fail2ban; exit $?; fi

echo "== Memasang ZIMMON ke $DIR (host: $HOST, port: $PORT) =="
mkdir -p "$DIR" /etc/zimmon
tar -C "$SRC" --exclude=.git -cf - . | tar -C "$DIR" -xf -
chmod 700 "$DIR"
install -m 755 "$SRC/update.sh" /usr/local/bin/zimmon-update

if [ ! -f /etc/zimmon/config.json ] || [ "$1" = "--reset-password" ]; then
  read -rp "Username login [assyaref.dev]: " U; U=${U:-assyaref.dev}
  read -rsp "Password login: " P; echo
  read -rsp "Ulangi password: " P2; echo
  [ -n "$P" ] && [ "$P" = "$P2" ] || { echo "Password kosong atau tidak sama"; exit 1; }
  # keep any other settings (terminal, asn_lookup, totp_secret, ...) already in the config
  ZU="$U" ZP="$P" ZH="$HOST" ZPORT="$PORT" python3 - <<'PY'
import os, json, hashlib, secrets
path = "/etc/zimmon/config.json"
try:
    conf = json.load(open(path))
except Exception:
    conf = {}
salt = secrets.token_bytes(16); it = 310000
conf.update({
  "user": os.environ["ZU"], "salt": salt.hex(), "iter": it,
  "hash": hashlib.pbkdf2_hmac("sha256", os.environ["ZP"].encode(), salt, it).hex(),
})
conf.setdefault("host", os.environ["ZH"]); conf.setdefault("port", int(os.environ["ZPORT"]))
fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with open(fd, "w") as f:
    json.dump(conf, f, indent=2)
os.replace(path + ".tmp", path)
PY
  unset P P2
  chmod 600 /etc/zimmon/config.json
  echo "Password disimpan sebagai hash (PBKDF2), bukan teks asli."
fi

# pasang fail2ban (idempotent) supaya tombol BLOKIR di aplikasi langsung berfungsi
setup_fail2ban || echo "Lewati fail2ban untuk sekarang; bisa diulang: bash $DIR/install.sh --fail2ban"

cat > /etc/systemd/system/zimmon.service <<EOF
[Unit]
Description=ZIMMON Zimbra monitoring dashboard
After=network-online.target zimbra.service

[Service]
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
RestartSec=5
# restart harian supaya sertifikat SSL yang diperpanjang ikut terbaca
RuntimeMaxSec=1d

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now zimmon
systemctl restart zimmon

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow $PORT/tcp >/dev/null && echo "Firewall ufw: port $PORT dibuka"
fi

sleep 3
if systemctl is-active --quiet zimmon; then
  echo
  echo "=============================================="
  echo " ZIMMON v$(cat $DIR/VERSION) AKTIF"
  echo " Buka di HP:  https://$HOST:$PORT"
  echo " Update nanti: zimmon-update <file.tar.gz>"
  echo "=============================================="
else
  echo "ZIMMON gagal start. Lihat log:"; journalctl -u zimmon -n 30 --no-pager
fi
