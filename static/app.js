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
    $("clkZulu").textContent = d.toISOString().substring(11, 19) + "Z";
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
    if (s.mail && s.mail.authfail >= 50) warn.push(`${s.mail.authfail} AUTH FAILS`);
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
    try { await loadXterm(); } catch { alert("Gagal memuat terminal"); return; }
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
