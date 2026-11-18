# ZIMMON — Zimbra Ops Center

Dashboard monitoring VPS Zimbra yang bisa diinstal di HP Android sebagai aplikasi (PWA). Bergaya ruang operasi intelijen. Hanya membaca data, tidak bisa mengubah apa pun di server.

**Deteksi serangan:** brute force (SMTP, webmail/IMAP/POP, SSH, ZIMMON), relay spam, bot, serangan aktif, akun yang kemungkinan dibobol, dan akun yang tiba-tiba mengirim email massal. Hanya mendeteksi, tidak memblokir. Setiap IP penyerang ditampilkan dengan ASN/operator jaringan dan negaranya (lookup ke `whois.cymru.com` port 43; matikan dengan `"asn_lookup": false` di `/etc/zimmon/config.json`).

**Terminal:** tombol `>_ TERMINAL` membuka shell root langsung di aplikasi, dengan baris tombol ESC/TAB/CTRL/panah untuk keyboard HP. Siapa pun yang bisa login ke ZIMMON mendapat akses root, jadi pakai password yang kuat. Sesi tertutup otomatis setelah 30 menit tidak aktif, dan setiap sesi dicatat di `/var/log/zimmon-terminal.log`. Untuk mematikan terminal, tambahkan `"terminal": false` di `/etc/zimmon/config.json` lalu `systemctl restart zimmon`.

**Yang dipantau:** status service Zimbra, CPU/RAM/disk/swap (+ grafik 10 menit), port, antrian email, jumlah akun, masa berlaku SSL, lalu lintas email, login gagal (brute-force), log peringatan, proses teratas.

## Instalasi pertama (di VPS, sebagai root)

```bash
git clone https://github.com/assyaref/zimmon.git /root/zimmon
cd /root/zimmon && bash install.sh
```

Installer meminta username dan password login. Password disimpan sebagai hash PBKDF2 di `/etc/zimmon/config.json` (tidak pernah masuk ke repo ini).

**2FA (disarankan, karena ZIMMON memberi akses root lewat terminal):** `bash /opt/zimmon/install.sh --2fa`, lalu scan QR atau masukkan kuncinya di Google Authenticator. Setelah itu login butuh password + kode 6 digit. Jam VPS harus akurat (NTP).

Lalu di HP: buka `https://<host>:9443` di Chrome → menu ⋮ → **Install app**.

## Update / upgrade

```bash
zimmon-update --git https://github.com/assyaref/zimmon.git   # pertama kali (URL disimpan)
zimmon-update                                                  # berikutnya cukup ini
```

Atau dari file paket: `zimmon-update /root/zimmon.tar.gz`

Setiap update otomatis:
1. Membuat backup versi lama (5 terakhir disimpan di `/opt/zimmon-backups`)
2. Mengecek `server.py` baru tidak error
3. Restart, lalu cek kesehatan — kalau versi baru gagal jalan, **rollback otomatis**

Perintah lain:

| Perintah | Fungsi |
|---|---|
| `zimmon-update --status` | Versi terpasang, repo, daftar backup |
| `zimmon-update --rollback` | Kembali ke versi sebelumnya |
| `bash /opt/zimmon/install.sh --reset-password` | Ganti username/password |
| `bash /opt/zimmon/install.sh --2fa` | Aktifkan 2FA / pasang di HP baru |
| `bash /opt/zimmon/install.sh --2fa-off` | Matikan 2FA (misalnya HP hilang) |

Di HP, aplikasi menampilkan banner **UPDATE TERSEDIA** begitu server sudah diupdate. Ketuk untuk memuat versi baru.

## Merilis versi baru

1. Ubah kode
2. Naikkan nomor di `VERSION` (misalnya `1.1.0` → `1.2.0`) dan tulis di `CHANGELOG.md`
3. Commit & push ke `main`
4. Di VPS: `zimmon-update`

## Struktur

```
server.py      server HTTPS (Python stdlib, tanpa dependency)
static/        aplikasi web: index.html, app.js, style.css, manifest, service worker, ikon
static/vendor/ xterm.js (MIT) untuk terminal
install.sh     instalasi pertama + systemd service
update.sh      dipasang sebagai /usr/local/bin/zimmon-update
twofa.py       pengaturan 2FA (dipanggil oleh install.sh --2fa / --2fa-off)
VERSION        nomor versi
```

Service: `systemctl status zimmon` · Log: `journalctl -u zimmon -f` · Port default: 9443
