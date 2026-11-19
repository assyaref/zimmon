# ZIMMON changelog

## 1.6.3 — 2026-11-19
- Tombol **CLEAR** di panel Deteksi Serangan: mengosongkan tampilan deteksi (event di memori ZIMMON) dengan konfirmasi. Data lalu dihitung ulang dari aktivitas log baru; **file log sistem di server tidak dihapus**, dan hitungan email terkirim tetap. Setelah ZIMMON restart, data 24 jam terbentuk lagi dari log

## 1.6.2 — 2026-11-19
- **Perbaikan**: ZIMMON kini mengenali fail2ban yang dipasang *setelah* ZIMMON berjalan, tanpa perlu restart. Sebelumnya, kalau `install.sh --fail2ban` dijalankan saat ZIMMON sudah jalan, tombol BLOKIR tetap error "fail2ban belum terpasang" sampai `systemctl restart zimmon`

## 1.6.1 — 2026-11-19
- Dialog konfirmasi dan notifikasi tidak lagi memakai pop-up bawaan browser. Konfirmasi blokir/buka blokir/aktif-matikan fail2ban kini modal bergaya ZIMMON, dan hasilnya muncul sebagai toast (hijau untuk sukses, merah untuk gagal)

## 1.6.0 — 2026-11-18
- **Tombol BLOKIR IP**: setiap IP penyerang bisa diblokir dari aplikasi (lewat fail2ban), dengan konfirmasi; ada daftar **IP DIBLOKIR** untuk membukanya lagi
  - `install.sh` kini memasang & mengatur fail2ban otomatis (jail manual `zimmon-manual` + brute force SSH + SMTP AUTH Zimbra). Atur ulang kapan saja: `bash install.sh --fail2ban`
  - Pengaman: IP server sendiri, perangkat pengakses, `127.0.0.1`, IP privat, dan IP tidak valid ditolak. Blokir memakai `fail2ban-client` (argumen tetap, bukan shell)
  - Status fail2ban dan daftar IP yang diblokir ikut ditampilkan di aplikasi
  - Tombol **AKTIFKAN / MATIKAN** fail2ban dari aplikasi (start+enable / stop+disable service); saat MATI tombol blokir per-IP ikut disembunyikan

## 1.5.0 — 2026-11-18
- `install.sh --reset-password` tidak lagi menghapus pengaturan lain di `/etc/zimmon/config.json` (terminal, asn_lookup, dll.); sebelumnya seluruh file ditulis ulang

## 1.4.0 — 2026-11-18
- **ASN penyerang**: setiap IP penyerang kini menampilkan jaringan asalnya (nomor AS, nama operator, bendera negara), termasuk di peringatan serangan aktif dan akun dibobol
- Panel baru **JARINGAN (ASN) PENYERANG**: 10 ASN dengan percobaan serangan terbanyak (ketuk untuk detail di bgp.he.net)
- Data dari Team Cymru (`whois.cymru.com`, port TCP 43), disimpan di memori 7 hari; IP privat tidak dicari. Matikan dengan `"asn_lookup": false` di config

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
