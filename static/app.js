(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const gb = (b) => (b / 1073741824).toFixed(1) + "G";
  const rate = (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB/s" : (b / 1024).toFixed(0) + " KB/s");
  let timer = null;
  let loadedVersion = null; // version the page was loaded with

  function checkVersion(v) {
    if (!v) return;
    if (loadedVersion === null) loadedVersion = v;
    $("ver").textContent = "v" + loadedVersion;
    if (v !== loadedVersion) $("updBanner").classList.remove("hidden");
  }
  $("updBanner").addEventListener("click", async () => {
    try {
      const regs = await navigator.serviceWorker?.getRegistrations?.() || [];
      await Promise.all(regs.map((r) => r.update()));
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch {}
    location.reload();
  });

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  // ---------------- boot sequence ----------------
  const BOOT = [
    "> INITIALIZING SECURE CHANNEL ........ ",
    "> TLS 1.3 HANDSHAKE .................. ",
    "> LOCATING NODE MAIL-KSOLRS .......... ",
    "> LOADING SIGINT MODULES ............. ",
    "> AWAITING AGENT CREDENTIALS_",
  ];
  async function boot() {
    const el = $("boot");
    for (let i = 0; i < BOOT.length; i++) {
      for (const ch of BOOT[i]) { el.textContent += ch; await sleep(8); }
      if (i < BOOT.length - 1) {
        await sleep(120);
        const ok = document.createElement("span"); ok.className = "ok"; ok.textContent = "[OK]";
        el.appendChild(ok); el.appendChild(document.createTextNode("\n"));
      }
    }
    $("loginForm").classList.remove("hidden");
    $("u").focus();
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------------- themed modal + toast ----------------
  function uiConfirm({ title, body, ok = "OK", danger = false }) {
    return new Promise((resolve) => {
      $("modalTitle").textContent = title;
      $("modalBody").innerHTML = body;   // body is built from app strings only
      const okb = $("modalOk"), cancel = $("modalCancel"), modal = $("modal");
      okb.textContent = ok;
      okb.className = "mbtn" + (danger ? " danger" : "");
      modal.classList.remove("hidden");
      okb.focus();
      const done = (v) => {
        modal.classList.add("hidden");
        okb.onclick = cancel.onclick = modal.onclick = null;
        document.removeEventListener("keydown", onKey);
        resolve(v);
      };
      const onKey = (e) => { if (e.key === "Escape") done(false); if (e.key === "Enter") done(true); };
      okb.onclick = () => done(true);
      cancel.onclick = () => done(false);
      modal.onclick = (e) => { if (e.target === modal) done(false); };
      document.addEventListener("keydown", onKey);
    });
  }

  function toast(msg, type = "") {
    const t = document.createElement("div");
    t.className = "toast" + (type ? " " + type : "");
    t.textContent = msg;
    $("toasts").appendChild(t);
    requestAnimationFrame(() => t.classList.add("show"));
    setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 300); }, 3600);
  }

  // ---------------- auth ----------------
  $("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("loginMsg"), btn = $("loginBtn");
    btn.disabled = true; msg.className = "msg"; msg.textContent = "VERIFYING CLEARANCE…";
    try {
      const r = await fetch("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user: $("u").value.trim(), pass: $("p").value }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        msg.className = "msg good"; msg.textContent = "ACCESS GRANTED — WELCOME, AGENT";
        $("p").value = "";
        await sleep(700);
        showDash();
      } else if (r.status === 429) {
        msg.className = "msg bad"; msg.textContent = "TERMINAL LOCKED — TOO MANY ATTEMPTS. RETRY IN 15 MIN.";
      } else {
        msg.className = "msg bad";
        msg.textContent = "ACCESS DENIED" + (j.left !== undefined ? ` — ${j.left} ATTEMPT(S) LEFT` : "");
      }
    } catch {
      msg.className = "msg bad"; msg.textContent = "UPLINK FAILURE — CHECK CONNECTION";
    }
    btn.disabled = false;
  });

  $("logout").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" }).catch(() => {});
    showLogin();
  });

  function showLogin() {
    clearInterval(timer); timer = null;
    $("dash").classList.add("hidden"); $("login").classList.remove("hidden");
    if (!$("boot").textContent) boot(); else $("loginForm").classList.remove("hidden");
  }
  function showDash() {
    $("login").classList.add("hidden"); $("dash").classList.remove("hidden");
    poll(); clearInterval(timer); timer = setInterval(poll, 10000);
  }

  // ---------------- clocks ----------------
  setInterval(() => {
    const d = new Date();
    $("clkLocal").textContent = d.toLocaleTimeString("en-GB", { timeZone: "Asia/Jakarta", hour12: false });
  }, 1000);

  // ---------------- data ----------------
  async function poll() {
    try {
      const r = await fetch("/api/status", { cache: "no-store" });
      if (r.status === 401) return showLogin();
      render(await r.json());
      $("link").className = "link ok"; $("link").textContent = "● UPLINK";
    } catch {
      $("link").className = "link bad"; $("link").textContent = "● UPLINK LOST";
    }
  }

  function level(pct, warn, crit) { return pct >= crit ? "crit" : pct >= warn ? "warn" : ""; }

  function gauge(id, pct, label) {
    const g = $(id);
    g.className = "gauge " + level(pct, 80, 90);
    g.querySelector("i").style.width = Math.min(pct, 100) + "%";
    g.querySelector("b").textContent = label;
  }

  function stat(label, value, cls = "") {
    return `<div class="stat ${cls}"><span>${esc(label)}</span><b>${esc(value)}</b></div>`;
  }

  function fmtUptime(s) {
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}d ${h}h` : `${h}h ${m}m`;
  }

  function render(s) {
    if (s.cpu === undefined) { $("threatVal").textContent = "ACQUIRING…"; return; }
    $("host").textContent = s.host;
    checkVersion(s.version);
    $("uptime").textContent = fmtUptime(s.uptime);
    const atk = s.attacks || {};
    $("sentToday").textContent = atk.sent_today !== undefined ? `${atk.sent_today} ✉` : "—";
    renderFirewall(s.firewall || {});
    renderAttacks(atk);

    gauge("gCpu", s.cpu, `${s.cpu}% ·${s.cores}c`);
    gauge("gMem", s.mem.pct, `${gb(s.mem.used)}/${gb(s.mem.total)}`);
    gauge("gDisk", s.disk.pct, `${gb(s.disk.avail)} free`);
    gauge("gSwap", s.swap.pct, `${gb(s.swap.used)}/${gb(s.swap.total)}`);
    spark(s.hist || []);

    // threat assessment
    const crit = [], warn = [];
    const down = (s.services || []).filter((x) => !x.up);
    const portsDown = (s.ports || []).filter((x) => !x.up);
    if (down.length) crit.push(`${down.length} SERVICE DOWN`);
    if (s.disk.pct >= 90) crit.push(`DISK ${s.disk.pct}%`); else if (s.disk.pct >= 80) warn.push(`DISK ${s.disk.pct}%`);
    if (s.mem.pct >= 95) crit.push(`RAM ${s.mem.pct}%`); else if (s.mem.pct >= 85) warn.push(`RAM ${s.mem.pct}%`);
    if (portsDown.length) warn.push(`${portsDown.length} PORT CLOSED`);
    if (s.queue !== null && s.queue >= 100) crit.push(`QUEUE ${s.queue}`); else if (s.queue >= 20) warn.push(`QUEUE ${s.queue}`);
    if (s.cert && s.cert.days < 7) crit.push(`CERT ${s.cert.days}D`); else if (s.cert && s.cert.days < 21) warn.push(`CERT ${s.cert.days}D`);
    if (atk.breaches && atk.breaches.length) crit.push("AKUN MUNGKIN DIBOBOL");
    if (atk.suspicious && atk.suspicious.length) crit.push("AKUN KIRIM SPAM");
    if (atk.active && atk.active.length) warn.push(`${atk.active.length} SERANGAN AKTIF`);
    const t = $("threat");
    if (crit.length) { t.className = "threat crit"; $("threatVal").textContent = "CRITICAL"; }
    else if (warn.length) { t.className = "threat elev"; $("threatVal").textContent = "ELEVATED"; }
    else { t.className = "threat"; $("threatVal").textContent = "NOMINAL"; }
    $("threatWhy").textContent = [...crit, ...warn].join(" · ") || "ALL SYSTEMS OPERATIONAL";

    // radar blips
    const radar = $("radar");
    radar.querySelectorAll(".blip").forEach((b) => b.remove());
    const svcs = s.services || [];
    svcs.forEach((x, i) => {
      const a = (i / Math.max(svcs.length, 1)) * Math.PI * 2 + 0.3;
      const rr = 22 + (i % 3) * 11;
      const b = document.createElement("div");
      b.className = "blip" + (x.up ? "" : " bad");
      b.style.left = 50 + rr * Math.cos(a) + "%";
      b.style.top = 50 + rr * Math.sin(a) + "%";
      if (!x.up) { const em = document.createElement("em"); em.textContent = x.name.toUpperCase(); b.appendChild(em); }
      radar.appendChild(b);
    });

    $("stats").innerHTML =
      stat("LOAD AVG", (s.load || []).join(" ")) +
      stat("NET IN", rate(s.net.rx)) +
      stat("NET OUT", rate(s.net.tx)) +
      stat("MAIL QUEUE", s.queue ?? "—", s.queue >= 100 ? "crit" : s.queue >= 20 ? "warn" : "") +
      stat("ACCOUNTS", s.accounts ?? "—") +
      stat("SSL CERT", s.cert ? `${s.cert.days} DAYS` : "—", s.cert && s.cert.days < 7 ? "crit" : s.cert && s.cert.days < 21 ? "warn" : "") +
      stat("DISK USED", `${gb(s.disk.used)}/${gb(s.disk.size)}`, level(s.disk.pct, 80, 90)) +
      stat("SERVICES", `${svcs.length - down.length}/${svcs.length}`, down.length ? "crit" : "");

    $("svcCount").textContent = svcs.length ? `(${svcs.length - down.length}/${svcs.length} ACTIVE)` : "(SCANNING…)";
    $("services").innerHTML = svcs.map((x) => `<span class="chip ${x.up ? "" : "bad"}">${esc(x.name.toUpperCase())}</span>`).join("") || '<span class="dim">SCANNING…</span>';
    $("ports").innerHTML = (s.ports || []).map((x) => `<span class="chip ${x.up ? "" : "bad"}">${x.port} ${esc(x.name)}</span>`).join("");

    const m = s.mail || {};
    $("mailWin").textContent = m.window ? `(${m.window})` : "";
    $("mail").innerHTML =
      stat("DELIVERED", m.sent ?? "—") +
      stat("DEFERRED", m.deferred ?? "—", m.deferred > 50 ? "warn" : "") +
      stat("BOUNCED", m.bounced ?? "—", m.bounced > 20 ? "warn" : "") +
      stat("REJECTED", m.rejected ?? "—") +
      stat("AUTH FAILS", m.authfail ?? "—", m.authfail >= 50 ? "warn" : "");

    $("alerts").textContent = (s.alerts || []).length ? s.alerts.join("\n") : "NO HOSTILE TRAFFIC DETECTED";
    $("procs").innerHTML = (s.procs || []).map((p) =>
      `<tr><td>${esc(p.pid)}</td><td>${esc(p.user)}</td><td>${esc(p.cpu)}</td><td>${esc(p.mem)}</td><td>${esc(p.cmd)}</td></tr>`).join("");
    $("last").textContent = "LAST INTERCEPT: " + new Date(s.ts * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Jakarta" });
  }

  const ago = (ts) => {
    const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
    return m < 60 ? `${m} mnt lalu` : `${Math.floor(m / 60)} jam lalu`;
  };

  // "AS15169 GOOGLE · US" (country code as flag emoji)
  const flag = (cc) => /^[A-Z]{2}$/.test(cc || "") ? String.fromCodePoint(...[...cc].map((c) => 0x1f1a5 + c.charCodeAt(0))) + " " : "";
  const asnText = (x) => x ? `${flag(x.cc)}AS${x.asn} ${x.name}` : "";

  let fw = {};
  function renderFirewall(f) {
    fw = f || {};
    const installed = !!fw.on, active = !!fw.active;
    $("fwState").textContent = !installed ? "— tidak terpasang di server"
      : !active ? "— MATI"
      : (fw.blocked && fw.blocked.length ? `AKTIF · ${fw.blocked.length} IP diblokir · jail ${fw.jail}` : "AKTIF · belum ada blokir");
    $("fwToggleWrap").innerHTML = !installed ? ""
      : active ? `<button class="fw-btn open" data-fwtoggle="disable">MATIKAN</button>`
               : `<button class="fw-btn" data-fwtoggle="enable">AKTIFKAN</button>`;
    $("fwList").innerHTML = !active ? ""
      : ((fw.blocked || []).map((ip) =>
          `<div class="atk-row"><a href="https://www.abuseipdb.com/check/${encodeURIComponent(ip)}" target="_blank" rel="noopener noreferrer">${esc(ip)}</a>` +
          `<button class="fw-btn open" data-unblock="${esc(ip)}">BUKA BLOKIR</button></div>`
        ).join("") || '<span class="dim">Belum ada IP yang diblokir.</span>');
  }

  async function fwToggle(action) {
    const r = await fetch("/api/fw-" + action, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return toast("Gagal: " + (j.error || r.status), "err");
    renderFirewall({ ...fw, active: j.active, blocked: j.blocked || [] });
    toast(action === "enable" ? "fail2ban diaktifkan" : "fail2ban dimatikan");
    poll();  // tombol BLOKIR per-IP ikut muncul/hilang sesuai status
  }

  async function fwAction(ip, unblock) {
    const r = await fetch(unblock ? "/api/unblock" : "/api/block", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ip }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      renderFirewall({ ...fw, blocked: j.blocked || [] });
      toast(unblock ? `Blokir dibuka: ${ip}` : `IP diblokir: ${ip}`);
    } else {
      toast((unblock ? "Gagal buka blokir: " : "Gagal blokir: ") + (j.error || r.status), "err");
    }
  }

  // one delegated handler for every block / unblock button
  $("atkPanel").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-block]");
    if (b) {
      if (await uiConfirm({ title: "BLOKIR IP", body: `Blokir <b>${esc(b.dataset.block)}</b> di firewall server?<br>Semua koneksi dari IP ini akan ditolak.`, ok: "BLOKIR", danger: true }))
        fwAction(b.dataset.block, false);
      return;
    }
    const u = e.target.closest("button[data-unblock]");
    if (u) {
      if (await uiConfirm({ title: "BUKA BLOKIR", body: `Buka blokir untuk <b>${esc(u.dataset.unblock)}</b>?<br>IP ini bisa terhubung lagi ke server.`, ok: "BUKA BLOKIR" }))
        fwAction(u.dataset.unblock, true);
      return;
    }
    const t = e.target.closest("button[data-fwtoggle]");
    if (t) {
      const off = t.dataset.fwtoggle === "disable";
      if (await uiConfirm({
        title: off ? "MATIKAN FAIL2BAN" : "AKTIFKAN FAIL2BAN",
        body: off ? "Matikan fail2ban? Semua IP yang sedang diblokir akan <b>dibuka</b>, dan brute force tidak lagi diblokir otomatis."
                  : "Aktifkan fail2ban? Brute force SSH & SMTP akan diblokir otomatis.",
        ok: off ? "MATIKAN" : "AKTIFKAN", danger: off,
      })) fwToggle(t.dataset.fwtoggle);
    }
  });

  function renderAttacks(a) {
    if (!a.level) { $("atkLevel").textContent = "MEMINDAI…"; return; }
    $("atkLevel").className = "atk-level " + a.level;
    $("atkLevel").textContent = { CLEAR: "AMAN", LOW: "RENDAH", ELEVATED: "WASPADA", HIGH: "TINGGI", CRITICAL: "KRITIS" }[a.level] || a.level;

    const alerts = [];
    (a.breaches || []).forEach((b) => alerts.push(
      `<div class="atk-alert"><b>⚠ KEMUNGKINAN AKUN DIBOBOL</b>IP ${esc(b.ip)}${b.asn ? " (" + esc(asnText(b.asn)) + ")" : ""} gagal login ${b.fails}× lalu BERHASIL masuk sebagai <b>${esc(b.account)}</b> (${ago(b.ts)}). Segera ganti password akun ini.</div>`));
    (a.suspicious || []).forEach((x) => alerts.push(
      `<div class="atk-alert"><b>⚠ AKUN MENGIRIM EMAIL MASSAL</b><b>${esc(x.account)}</b> mengirim ${x.per_hour} email dalam 1 jam terakhir. Kemungkinan dipakai spammer: cek akun dan ganti passwordnya.</div>`));
    (a.active || []).forEach((x) => alerts.push(
      `<div class="atk-alert warn"><b>● SERANGAN AKTIF</b>IP ${esc(x.ip)}${x.asn ? " (" + esc(asnText(x.asn)) + ")" : ""}: ${x.recent} percobaan dalam 15 menit (${esc(x.cats.join(", "))})${x.who.length ? " · target: " + esc(x.who.join(", ")) : ""}</div>`));
    $("atkAlerts").innerHTML = alerts.join("");

    $("atkCats").innerHTML = (a.categories || []).map((c) =>
      `<div class="atk-cat ${c.h1 >= 20 ? "fire" : c.h24 ? "hot" : ""}"><span>${esc(c.label)}</span><b>${c.h24}</b> <i>· ${c.h1}/jam</i></div>`).join("");

    const tl = a.timeline || [], max = Math.max(1, ...tl);
    $("atkTimeline").innerHTML = tl.map((v) => `<i class="${v ? "" : "zero"}" title="${v}"></i>`).join("");
    $("atkTimeline").querySelectorAll("i").forEach((el, i) => { el.style.height = Math.max(2, (tl[i] / max) * 100) + "%"; });
    $("atkTotals").textContent = `${a.total24} percobaan · ${a.unique_ips} IP`;

    const bset = new Set(fw.blocked || []);
    $("atkTop").innerHTML = (a.top || []).map((x) =>
      `<div class="atk-row"><a href="https://www.abuseipdb.com/check/${encodeURIComponent(x.ip)}" target="_blank" rel="noopener noreferrer">${esc(x.ip)}</a><span class="n">${x.n}×</span>` +
      (fw.active ? (bset.has(x.ip)
        ? `<button class="fw-btn done" disabled>DIBLOKIR</button>`
        : `<button class="fw-btn" data-block="${esc(x.ip)}">BLOKIR</button>`) : "") +
      `<span class="meta">${x.asn ? `<span class="asn">${esc(asnText(x.asn))}</span>` : ""}${x.recent >= 10 ? '<span class="live">● AKTIF · </span>' : ""}${esc(x.cats.join(", "))} · ${ago(x.last)}${x.who.length ? " · " + esc(x.who.join(", ")) : ""}</span></div>`
    ).join("") || '<span class="dim">Tidak ada percobaan serangan terdeteksi.</span>';

    $("atkAsn").innerHTML = (a.asns || []).map((x) =>
      `<div class="atk-row"><a href="https://bgp.he.net/AS${x.asn}" target="_blank" rel="noopener noreferrer">${esc(flag(x.cc))}AS${x.asn}</a><span class="n">${x.n}×</span>` +
      `<span class="meta">${esc(x.name)} · ${x.ips} IP${x.recent ? ` · <span class="live">${x.recent} dalam 15 mnt</span>` : ""}</span></div>`
    ).join("") || `<span class="dim">${a.asn_on === false ? "Lookup ASN dimatikan di config." : a.unique_ips ? "Mencari data ASN…" : "Belum ada data."}</span>`;

    $("atkTargets").innerHTML = (a.targets || []).map((t) => `<span class="chip bad">${esc(t.account)} · ${t.n}×</span>`).join("") ||
      '<span class="dim">Belum ada akun yang diincar.</span>';
  }

  function spark(hist) {
    const c = $("spark"), dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = 90;
    c.width = w * dpr; c.height = h * dpr;
    const x = c.getContext("2d"); x.scale(dpr, dpr); x.clearRect(0, 0, w, h);
    x.strokeStyle = "rgba(15,59,44,.8)"; x.lineWidth = 1;
    [25, 50, 75].forEach((p) => { x.beginPath(); x.moveTo(0, h - (p / 100) * h); x.lineTo(w, h - (p / 100) * h); x.stroke(); });
    if (hist.length < 2) return;
    [["cpu", "#33ff99"], ["mem", "#33ccff"], ["disk", "#ffb000"]].forEach(([k, col]) => {
      x.strokeStyle = col; x.lineWidth = 1.6; x.beginPath();
      hist.forEach((p, i) => {
        const px = (i / (hist.length - 1)) * w, py = h - 2 - (Math.min(p[k], 100) / 100) * (h - 4);
        i ? x.lineTo(px, py) : x.moveTo(px, py);
      });
      x.stroke();
    });
  }

  // ---------------- terminal ----------------
  let term = null, fit = null, ws = null, ctrlOn = false;
  const enc = new TextEncoder();

  function loadXterm() {
    if (window.XTermLib) return Promise.resolve();
    return new Promise((ok, fail) => {
      const css = document.createElement("link");
      css.rel = "stylesheet"; css.href = "/vendor/xterm.css"; document.head.appendChild(css);
      const s = document.createElement("script");
      s.src = "/vendor/xterm.js"; s.onload = ok; s.onerror = fail; document.head.appendChild(s);
    });
  }

  function setState(on, text) {
    $("termState").className = "term-state" + (on ? " on" : "");
    $("termState").textContent = text;
    $("termReconnect").classList.toggle("hidden", on);
  }

  function send(kind, str) {
    if (ws && ws.readyState === 1) ws.send(enc.encode(kind + str));
  }

  function sendResize() {
    if (term) send("r", JSON.stringify({ c: term.cols, r: term.rows }));
  }

  function input(str) {
    if (ctrlOn && str.length === 1) {
      const c = str.toUpperCase().charCodeAt(0);
      if (c >= 64 && c <= 95) str = String.fromCharCode(c - 64);
      ctrlOn = false; $("ctrlKey").classList.remove("on");
    }
    send("i", str);
  }

  function connect() {
    if (ws) { try { ws.close(); } catch {} }
    setState(false, "CONNECTING…");
    ws = new WebSocket(`wss://${location.host}/api/term`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { setState(true, "● LIVE"); fit.fit(); sendResize(); term.focus(); };
    ws.onmessage = (e) => term.write(new Uint8Array(e.data));
    ws.onclose = () => { setState(false, "DISCONNECTED"); term.write("\r\n\x1b[31m[ZIMMON] Koneksi terminal terputus. Ketuk ↻ untuk sambung lagi.\x1b[0m\r\n"); };
  }

  async function openTerm() {
    try { await loadXterm(); } catch { toast("Gagal memuat terminal", "err"); return; }
    $("term").classList.remove("hidden");
    $("termHost").textContent = $("host").textContent.toUpperCase();
    if (!term) {
      const { Terminal, FitAddon } = window.XTermLib;
      term = new Terminal({
        cursorBlink: true, fontSize: 13, scrollback: 3000,
        fontFamily: 'ui-monospace, "Roboto Mono", "Courier New", monospace',
        theme: { background: "#000000", foreground: "#c9f7e1", cursor: "#33ff99", selectionBackground: "#1fb87466",
                 green: "#33ff99", brightGreen: "#7dffbf", red: "#ff3b3b", yellow: "#ffb000", blue: "#33ccff" },
      });
      fit = new FitAddon(); term.loadAddon(fit);
      term.open($("termBox"));
      term.onData(input);
      term.onResize(sendResize);
      window.addEventListener("resize", () => { if (!$("term").classList.contains("hidden")) fit.fit(); });
    }
    setTimeout(() => fit.fit(), 50);
    if (!ws || ws.readyState > 1) connect();
    term.focus();
  }

  const KEYS = { esc: "\x1b", tab: "\t", up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D", ctrlc: "\x03" };
  $("keys").addEventListener("click", async (e) => {
    const k = e.target.dataset && e.target.dataset.k;
    if (!k) return;
    if (k === "ctrl") { ctrlOn = !ctrlOn; e.target.classList.toggle("on", ctrlOn); }
    else if (k === "paste") { try { send("i", await navigator.clipboard.readText()); } catch {} }
    else send("i", KEYS[k] || k);
    term && term.focus();
  });
  $("openTerm").addEventListener("click", openTerm);
  $("closeTerm").addEventListener("click", () => {
    if (ws) { try { ws.close(); } catch {} ws = null; }
    if (term) term.reset();
    $("term").classList.add("hidden");
  });
  $("termReconnect").addEventListener("click", () => { term && term.reset(); connect(); });

  // ---------------- start ----------------
  fetch("/api/session").then((r) => r.json()).then((j) => {
    checkVersion(j.version);
    $("openTerm").classList.toggle("hidden", !j.terminal);
    j.ok ? showDash() : showLogin();
  }).catch(showLogin);
})();
