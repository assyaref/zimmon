# ZIMMON changelog

## 1.3.0 — 2026-11-19
- **Deteksi serangan** (24 jam), membaca log Zimbra, audit login webmail/IMAP/POP, SSH, dan login ZIMMON:
  - Brute force SMTP AUTH, webmail/IMAP/POP, SSH, dan ZIMMON; percobaan relay spam; bot/spammer (postscreen)
  - **Serangan aktif**: IP dengan ≥10 percobaan dalam 15 menit
  - **Kemungkinan akun dibobol**: IP yang gagal login ≥5× lalu berhasil masuk
  - **Akun kirim email massal**: akun yang mengirim ≥150 email/jam (atur dengan `"suspect_send_per_hour"` di config)
  - Penyerang teratas (ketuk IP untuk cek reputasi di AbuseIPDB), akun yang diincar, grafik per jam
- Log dibaca bertahap (hanya baris baru), jadi ringan untuk server
- Updater dan installer sekarang menyalin semua file rilis

## 1.2.1 — 2026-11-19
- Jam Zulu (UTC) diganti kotak **EMAIL HARI INI**: jumlah email terkirim sejak 00:00 WIB

## 1.2.0 — 2026-11-19
- Terminal (root shell) di dalam aplikasi: tombol `>_ TERMINAL`, baris tombol khusus untuk HP (ESC, TAB, CTRL, panah, ^C, PASTE)
- Sesi terminal otomatis ditutup setelah 30 menit tidak aktif; maksimal 3 terminal bersamaan
- Setiap buka/tutup terminal dicatat di `/var/log/zimmon-terminal.log`
- Terminal bisa dimatikan dengan `"terminal": false` di `/etc/zimmon/config.json`

## 1.1.0 — 2026-11-19
- Sistem update: `zimmon-update` (dari file .tar.gz atau GitHub), backup otomatis, rollback otomatis kalau versi baru gagal jalan
- Nomor versi tampil di aplikasi, dan muncul banner "UPDATE TERSEDIA" saat server sudah diupdate

## 1.0.0 — 2026-11-19
- Rilis pertama: dashboard monitoring Zimbra, login, PWA
