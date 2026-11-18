#!/bin/bash
# ZIMMON installer — jalankan sebagai root di VPS Zimbra
set -e
[ "$(id -u)" = 0 ] || { echo "Jalankan sebagai root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/zimmon
PORT=9443
HOST=$(su - zimbra -c "zmhostname" 2>/dev/null || hostname -f)

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

case "${1:-}" in
  --2fa) python3 "$DIR/twofa.py" on || exit 1 ;;
  --2fa-off) python3 "$DIR/twofa.py" off ;;
esac

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
