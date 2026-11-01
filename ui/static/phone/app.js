/* another_ap phone — global: search, themes, help modal, keyboard nav, slide-out menu. no deps. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  /* ---------- dynamic viewport height (dvh fallback) ---------- */
  function setVh() {
    let vh = window.innerHeight * 0.01;
    try { document.documentElement.style.setProperty("--vh", vh + "px"); } catch (_) {}
  }
  setVh();
  window.addEventListener("resize", setVh);
  window.addEventListener("orientationchange", setVh);

  /* ---------- prevent double-tap zoom on interactive elements ---------- */
  let lastTouchEnd = 0;
  document.addEventListener("touchend", (e) => {
    const now = Date.now();
    if (now - lastTouchEnd <= 300) e.preventDefault();
    lastTouchEnd = now;
  }, { passive: false });

  const toastEl = $("#toast");
  let toastT = null;
  function toast(msg) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toastT);
    toastT = setTimeout(() => { toastEl.hidden = true; }, 2600);
  }
  window.apToast = toast;

  window.addEventListener("error", (ev) => {
    try { toast("JS error: " + ((ev && ev.message) || "unknown") + " — press F12"); } catch (_) {}
  });

  async function api(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.detail || msg; } catch (_) { /* text */ }
      throw new Error(msg || ("http " + r.status));
    }
    return r.json();
  }
  window.apApi = api;

  /* ---------- slide-out menu ---------- */
  const hamb = $("#hamburger");
  const menuEl = $("#menu");
  const menuBackdrop = $("#menu-backdrop");
  function toggleMenu() { menuEl.classList.toggle("open"); menuBackdrop && menuBackdrop.classList.toggle("open"); }
  function closeMenu() { menuEl.classList.remove("open"); menuBackdrop && menuBackdrop.classList.remove("open"); }
  if (hamb && menuEl) {
    hamb.addEventListener("click", toggleMenu);
    menuBackdrop && menuBackdrop.addEventListener("click", closeMenu);
    menuEl.addEventListener("click", (e) => { if (e.target === menuEl) closeMenu(); });
    $$(".m-item", menuEl).forEach((a) => { a.addEventListener("click", closeMenu); });
    /* swipe right on menu edge to close */
    let startX = 0;
    menuEl.addEventListener("touchstart", (e) => { startX = e.changedTouches[0].clientX; }, { passive: true });
    menuEl.addEventListener("touchend", (e) => {
      const dx = e.changedTouches[0].clientX - startX;
      if (dx > 80) closeMenu();
    }, { passive: true });
  }
  const themeSelMenu = $("#theme-select-menu");
  const themeManageMenu = $("#theme-manage-menu");
  if (themeSelMenu) {
    themeSelMenu.addEventListener("change", async () => {
      try {
        await api("/api/settings/theme", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: themeSelMenu.value }) });
        const sel = $("#theme-select");
        if (sel) sel.value = themeSelMenu.value;
        const d = await api("/api/themes");
        const t = d.themes.find((x) => x.name === themeSelMenu.value);
        if (t) applyTheme(t);
        menuEl.classList.remove("open");
      } catch (e) { toast("theme error: " + e.message); }
    });
  }
  if (themeManageMenu) themeManageMenu.addEventListener("click", () => { refreshThemes(); openModal("#theme-modal"); menuEl.classList.remove("open"); });

  /* ---------- global search ---------- */
  const gs = $("#global-search");
  function goSearch(q) {
    const onAudios = location.pathname.startsWith("/phone/a/") || location.pathname.startsWith("/phone/audios");
    const onPlaylists = location.pathname.startsWith("/phone/p/") || location.pathname.startsWith("/phone/playlists");
    let target = "/phone/";
    if (onAudios) target = "/phone/audios";
    else if (onPlaylists) target = "/phone/playlists";
    else {
      target = "/phone/audios";
      if (/@(songs|artis|tags|title|desc|hash)/i.test(q)) target = "/phone/playlists";
    }
    location.href = target + "?q=" + encodeURIComponent(q);
  }
  if (gs) {
    gs.addEventListener("keydown", (e) => {
      if (e.key === "Enter") goSearch(gs.value.trim());
      else if (e.key === "Escape") gs.blur();
      e.stopPropagation();
    });
    $$("[data-search]").forEach((inp) => {
      if (inp === gs) return;
      inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          const base = inp.getAttribute("data-search") || location.pathname;
          location.href = base + "?q=" + encodeURIComponent(inp.value.trim());
        } else if (e.key === "Escape") inp.blur();
        e.stopPropagation();
      });
    });
  }

  /* ---------- themes ---------- */
  const sel = $("#theme-select");
  function applyTheme(t) {
    const root = document.documentElement;
    ["bg", "panel", "fg", "muted", "accent", "warn", "danger", "border", "sel"].forEach((k) => {
      if (t[k]) root.style.setProperty("--" + k, t[k]);
    });
    document.documentElement.dataset.theme = t.name;
    try { localStorage.setItem("another_ap_theme", t.name); } catch (_) {}
    if (sel) sel.value = t.name;
    if (themeSelMenu) themeSelMenu.value = t.name;
    /* keep theme-color meta in sync */
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && t.accent) meta.setAttribute("content", t.accent);
  }
  window.apApplyTheme = applyTheme;
  async function refreshThemes() {
    try {
      const d = await api("/api/themes");
      if (sel) {
        sel.innerHTML = "";
        d.themes.forEach((t) => {
          const o = document.createElement("option");
          o.value = t.name; o.textContent = t.name + (t.builtin ? " *" : "");
          if (t.name === d.active) o.selected = true;
          sel.appendChild(o);
        });
      }
      if (themeSelMenu) {
        themeSelMenu.innerHTML = "";
        d.themes.forEach((t) => {
          const o = document.createElement("option");
          o.value = t.name; o.textContent = t.name + (t.builtin ? " *" : "");
          if (t.name === d.active) o.selected = true;
          themeSelMenu.appendChild(o);
        });
      }
      renderThemeList(d);
      const active = d.themes.find((t) => t.name === d.active);
      if (active) applyTheme(active);
    } catch (e) { /* offline-safe */ }
  }
  function renderThemeList(d) {
    const box = $("#theme-list");
    if (!box || !d) return;
    box.innerHTML = "";
    d.themes.forEach((t) => {
      const row = document.createElement("div");
      row.className = "theme-row";
      const sw = document.createElement("span");
      sw.className = "swatches";
      ["bg", "accent", "fg"].forEach((k) => {
        const i = document.createElement("i");
        i.style.background = t[k]; sw.appendChild(i);
      });
      const nm = document.createElement("span");
      nm.textContent = (t.name === d.active ? "> " : "  ") + t.name + (t.builtin ? " [builtin]" : "");
      row.appendChild(sw); row.appendChild(nm);
      const use = document.createElement("button");
      use.className = "btn"; use.textContent = "use";
      use.onclick = async () => {
        await api("/api/settings/theme", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: t.name }) });
        applyTheme(t); refreshThemes(); toast("theme: " + t.name);
        const m = $("#theme-modal"); if (m) m.hidden = true;
      };
      row.appendChild(use);
      if (!t.builtin) {
        const del = document.createElement("button");
        del.className = "btn danger"; del.textContent = "del";
        del.onclick = async () => {
          if (!confirm("delete theme '" + t.name + "'?")) return;
          await api("/api/themes/" + encodeURIComponent(t.name), { method: "DELETE" });
          refreshThemes(); toast("theme deleted");
        };
        row.appendChild(del);
      }
      box.appendChild(row);
    });
  }
  if (sel) {
    sel.addEventListener("change", async () => {
      try {
        await api("/api/settings/theme", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: sel.value }) });
        const d = await api("/api/themes");
        const t = d.themes.find((x) => x.name === sel.value);
        if (t) applyTheme(t);
      } catch (e) { toast("theme error: " + e.message); }
    });
  }
  const tf = $("#theme-form");
  if (tf) {
    tf.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(tf);
      const body = {};
      fd.forEach((v, k) => { body[k] = String(v); });
      try {
        const t = await api("/api/themes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        await api("/api/settings/theme", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ active: t.name }) });
        applyTheme(t); refreshThemes(); toast("theme saved: " + t.name);
      } catch (err) { toast("theme error: " + err.message); }
    });
  }

  /* ---------- modals ---------- */
  function openModal(id) { const m = $(id); if (m) { m.hidden = false; } }
  function closeModals() { $$(".modal").forEach((m) => { m.hidden = true; }); }
  window.apOpenModal = openModal; window.apCloseModals = closeModals;
  $$(".modal").forEach((m) => {
    m.addEventListener("click", (e) => { if (e.target === m) m.hidden = true; });
    $$("[data-close]", m).forEach((b) => b.addEventListener("click", () => { m.hidden = true; }));
  });
  const tmBtn = $("#theme-manage");
  if (tmBtn) tmBtn.addEventListener("click", () => { refreshThemes(); openModal("#theme-modal"); });

  /* ---------- keyboard ---------- */
  let pendingG = false;
  document.addEventListener("keydown", (e) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (document.activeElement && document.activeElement.isContentEditable);
    if (e.key === "Escape") { closeModals(); closeMenu(); if (document.activeElement) document.activeElement.blur(); pendingG = false; return; }
    if (typing) return;
    if (e.key === "?") { e.preventDefault(); openModal("#help-modal"); return; }
    if (e.key === "/") { e.preventDefault(); if (gs) gs.focus(); return; }
    if (pendingG) {
      pendingG = false;
      const map = { h: "/phone/", p: "/phone/playlists", a: "/phone/audios", d: "/phone/download" };
      if (map[e.key]) { location.href = map[e.key]; }
      return;
    }
    if (e.key === "g") { pendingG = true; setTimeout(() => { pendingG = false; }, 900); return; }
    if (e.key === "t" && !e.metaKey && !e.ctrlKey) {
      if (sel && sel.options.length) {
        sel.selectedIndex = (sel.selectedIndex + 1) % sel.options.length;
        sel.dispatchEvent(new Event("change"));
      }
    }
  });

  /* nav highlight */
  const path = location.pathname;
  $$(".nav a, .m-item").forEach((a) => {
    const href = a.getAttribute("href");
    if (href && (href === path || (href !== "/" && path.startsWith(href)))) a.classList.add("on");
    else if (href === "/" && path === "/") a.classList.add("on");
  });

  /* card arrow navigation on listing grids */
  document.addEventListener("keydown", (e) => {
    if (!/^Arrow/.test(e.key)) return;
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    if (window.apPlayerActive) return;
    const cards = $$(".card a.card-title a, .trow .tt a, .q-item");
    if (!cards.length) return;
    e.preventDefault();
    let i = cards.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowRight") i = Math.min(cards.length - 1, i + 1);
    else i = Math.max(0, i - 1);
    cards[Math.max(0, i)].focus();
  });

  /* ---------- full-card tap on listing rows ---------- */
  $$(".card, .card.trow").forEach((card) => {
    const link = card.querySelector("a.card-title a, .tt a, .card-title a");
    if (!link) return;
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, input, select, textarea, a, [role=button]")) return;
      location.href = link.href;
    });
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); link.click(); }
    });
  });

  try {
    const last = localStorage.getItem("another_ap_theme");
    if (last && sel) sel.value = last;
  } catch (_) {}
  refreshThemes();
})();
