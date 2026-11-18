#!/bin/bash
# zimmon-update — update / upgrade / rollback ZIMMON
#   zimmon-update /root/zimmon.tar.gz   update dari file paket
#   zimmon-update --git [URL]           update dari repo GitHub (URL disimpan untuk berikutnya)
#   zimmon-update                       update dari repo GitHub yang tersimpan
#   zimmon-update --rollback            kembali ke versi sebelumnya
#   zimmon-update --status              versi sekarang + daftar backup
set -e
DIR=/opt/zimmon
BAK=/opt/zimmon-backups
CONF=/etc/zimmon/config.json
[ "$(id -u)" = 0 ] || { echo "Jalankan sebagai root"; exit 1; }
[ -f "$CONF" ] || { echo "ZIMMON belum terpasang. Jalankan install.sh dulu."; exit 1; }

cfg() { python3 -c "import json,sys;print(json.load(open('$CONF')).get(sys.argv[1],''))" "$1"; }
set_cfg() { python3 - "$1" "$2" <<'PY'
import json, sys
p = "/etc/zimmon/config.json"; c = json.load(open(p)); c[sys.argv[1]] = sys.argv[2]
json.dump(c, open(p, "w"), indent=2)
PY
}
PORT=$(cfg port); PORT=${PORT:-9443}
cur() { cat "$DIR/VERSION" 2>/dev/null || echo "?"; }

health() {
  for _ in $(seq 1 20); do
    sleep 1
    curl -sk --max-time 3 "https://127.0.0.1:$PORT/api/session" | grep -q '"version"' && return 0
  done
  return 1
}

backup() {
  mkdir -p "$BAK"
  local f="$BAK/zimmon-$(cur)-$(date +%Y%m%d-%H%M%S).tar.gz"
  tar czf "$f" -C /opt zimmon
  ls -1t "$BAK"/zimmon-*.tar.gz | tail -n +6 | xargs -r rm -f   # simpan 5 backup terakhir
  echo "$f"
}

restore() {
  local tmp; tmp=$(mktemp -d)
  tar xzf "$1" -C "$tmp"
  rm -rf "$DIR"; mv "$tmp/zimmon" "$DIR"; rm -rf "$tmp"
  systemctl restart zimmon
}

install_from() {
  local src="$1"
  [ -f "$src/server.py" ] && [ -f "$src/VERSION" ] && [ -d "$src/static" ] || { echo "Paket tidak valid (server.py/VERSION/static tidak ada)"; exit 1; }
  python3 -m py_compile "$src/server.py" || { echo "server.py versi baru error, update dibatalkan"; exit 1; }
  local old new b
  old=$(cur); new=$(cat "$src/VERSION")
  echo "== Update ZIMMON: v$old → v$new =="
  b=$(backup); echo "Backup: $b"
  rm -rf "$DIR.new"; mkdir -p "$DIR.new"
  # copy every file of the release (except git metadata) so new modules are never missed
  tar -C "$src" --exclude=.git -cf - . | tar -C "$DIR.new" -xf -
  rm -rf "$DIR"; mv "$DIR.new" "$DIR"; chmod 700 "$DIR"
  systemctl restart zimmon
  if health; then
    install -m 755 "$DIR/update.sh" /usr/local/bin/zimmon-update
    echo "BERHASIL: ZIMMON sekarang v$new"
    [ -f "$DIR/CHANGELOG.md" ] && { echo; sed -n '3,12p' "$DIR/CHANGELOG.md"; }
    echo; echo "Di HP: buka aplikasi, ketuk banner 'UPDATE TERSEDIA' (atau tutup-buka aplikasinya)."
  else
    echo "GAGAL: v$new tidak mau jalan. Rollback otomatis ke v$old..."
    restore "$b"
    health && echo "Rollback berhasil, kembali ke v$old." || echo "Rollback juga gagal. Cek: journalctl -u zimmon -n 50"
    journalctl -u zimmon -n 20 --no-pager
    exit 1
  fi
}

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

case "${1:-}" in
  --status)
    echo "Versi terpasang: v$(cur)"
    echo "Repo update: $(cfg repo || true)"
    systemctl is-active zimmon >/dev/null && echo "Service: AKTIF" || echo "Service: MATI"
    echo "Backup:"; ls -1t "$BAK"/zimmon-*.tar.gz 2>/dev/null || echo "  (belum ada)"
    ;;
  --rollback)
    # backup terbaru yang versinya berbeda dari versi yang sedang jalan
    last=$(ls -1t "$BAK"/zimmon-*.tar.gz 2>/dev/null | grep -v "/zimmon-$(cur)-" | head -1)
    [ -n "$last" ] || { echo "Tidak ada backup."; exit 1; }
    echo "Rollback dari v$(cur) ke $last"
    restore "$last"
    health && echo "BERHASIL: sekarang v$(cur)" || { echo "GAGAL start"; journalctl -u zimmon -n 20 --no-pager; exit 1; }
    ;;
  --git|"")
    url="${2:-$(cfg repo)}"
    [ -n "$url" ] || { sed -n '2,7p' "$0"; exit 1; }
    command -v git >/dev/null || apt-get install -y git
    git clone --depth 1 "$url" "$TMP/zimmon"
    [ "$1" = "--git" ] && [ -n "$2" ] && set_cfg repo "$2"
    install_from "$TMP/zimmon"
    ;;
  *.tar.gz)
    [ -f "$1" ] || { echo "File $1 tidak ditemukan"; exit 1; }
    tar xzf "$1" -C "$TMP"
    if [ -f "$TMP/zimmon/server.py" ]; then install_from "$TMP/zimmon"; else install_from "$TMP"; fi
    ;;
  *)
    sed -n '2,7p' "$0"; exit 1 ;;
esac
