"use strict";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
try { tg?.disableVerticalSwipes?.(); } catch (_) {}

const $ = (id) => document.getElementById(id);
const state = {
  me: null, pack: null, icons: { slots: {}, all: [] }, admin: null, adminTab: "stats",
  inboxSelect: false, inboxSelected: new Set(),
};

// ---------- helpers ----------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c != null && c !== false) el.append(c);
  return el;
}

async function api(method, path, body) {
  const opts = { method, headers: { "X-Init-Data": tg?.initData || "" } };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (_) {}
  if (!res.ok) throw new Error(data.error || `Ошибка ${res.status}`);
  return data;
}

let busyCount = 0;
async function withBusy(text, fn) {
  busyCount++;
  $("busyText").textContent = text || "";
  $("busy").classList.remove("hidden");
  try {
    return await fn();
  } catch (e) {
    toast(e.message || String(e));
    tg?.HapticFeedback?.notificationOccurred("error");
    throw e;
  } finally {
    if (--busyCount === 0) $("busy").classList.add("hidden");
  }
}

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2800);
}

function confirmBox(text) {
  return new Promise((resolve) => {
    if (tg?.showConfirm && tg.isVersionAtLeast?.("6.2")) tg.showConfirm(text, resolve);
    else resolve(window.confirm(text));
  });
}

function haptic(kind = "light") { tg?.HapticFeedback?.impactOccurred(kind); }
function success() { tg?.HapticFeedback?.notificationOccurred("success"); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseEmojis(str) {
  const seg = typeof Intl.Segmenter === "function"
    ? [...new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(str)].map((s) => s.segment)
    : Array.from(str);
  const out = [];
  for (const g of seg) {
    if (/\p{Extended_Pictographic}|\p{Regional_Indicator}/u.test(g) && !out.includes(g)) out.push(g);
  }
  return out.slice(0, 20);
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}
const stickersWord = (n) => `${n} ${plural(n, "стикер", "стикера", "стикеров")}`;
const fmtDate = (ts, timeZone) => {
  const o = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
  try { return new Date(ts * 1000).toLocaleString("ru-RU", timeZone ? { ...o, timeZone } : o); }
  catch (_) { return new Date(ts * 1000).toLocaleString("ru-RU", o); }
};

// ---------- sticker rendering ----------
const lottieObserver = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        lottieObserver.unobserve(e.target);
        loadLottie(e.target);
      }
    }, { rootMargin: "200px" })
  : null;

const lottieCache = new Map();
async function loadLottie(el) {
  try {
    if (!window.lottie || typeof DecompressionStream !== "function") throw new Error("no lottie");
    const src = el.dataset.src;
    if (!lottieCache.has(src)) {
      lottieCache.set(src, fetch(src).then(async (res) => {
        const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
        return JSON.parse(await new Response(stream).text());
      }));
    }
    const json = await lottieCache.get(src);
    el.innerHTML = "";
    lottie.loadAnimation({ container: el, renderer: "svg", loop: true, autoplay: true, animationData: structuredClone(json) });
  } catch (_) {
    if (el.dataset.thumb) el.replaceChildren(h("img", { class: "media", src: el.dataset.thumb }));
  }
}

function media(item) {
  if (item.format === "static") {
    return h("img", { class: "media", src: item.file, loading: "lazy", draggable: "false" });
  }
  if (item.format === "video") {
    const v = h("video", {
      class: "media", src: item.file, poster: item.thumb, autoplay: true, loop: true,
      muted: true, playsinline: true, "webkit-playsinline": true,
    });
    v.muted = true;
    v.addEventListener("error", () => {
      if (item.thumb) v.replaceWith(h("img", { class: "media", src: item.thumb }));
    });
    return v;
  }
  const box = h("div", { class: "media", "data-src": item.file, "data-thumb": item.thumb || "" });
  if (item.thumb) box.append(h("img", { class: "media", src: item.thumb }));
  if (lottieObserver) lottieObserver.observe(box); else loadLottie(box);
  return box;
}

// ---------- иконки ----------
// Сначала иконка из набора мини-апки (iOS-стиль), иначе встроенная монохромная SVG. Эмодзи не используем.
const SVG_PATHS = {
  pack: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  add: '<path d="M12 5v14M5 12h14"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
  star: '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  unlock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  settings: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  chart: '<path d="M18 20V10M12 20V4M6 20v-6"/>',
  megaphone: '<path d="M3 11l18-5v12L3 14v-3z"/><path d="M11.6 16.8a3 3 0 1 1-5.8-1.6"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  money: '<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  eyes: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  warning: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
  palette: '<circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 0 0 20z" fill="currentColor"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  order: '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  chevron: '<path d="M9 18l6-6-6-6"/>',
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
  emoji: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>',
  video: '<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
  down: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
  fire: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/>',
  send: '<path d="M22 2L11 13M22 2l-7 20-4-9-9-4z"/>',
  clip: '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
};

function svgIcon(name) {
  const span = document.createElement("span");
  span.className = "ic ic-svg";
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${SVG_PATHS[name] || SVG_PATHS.sparkle}</svg>`;
  return span;
}

function ic(slot) {
  if (slot === "theme") return svgIcon(currentTheme() === "light" ? "moon" : "sun");
  const s = state.icons.slots[slot];
  if (s?.icon) return h("span", { class: "ic ic-pack" }, media(s.icon));
  return svgIcon(slot);
}
function fillIcons(root = document) {
  root.querySelectorAll("[data-ic]").forEach((el) => el.replaceChildren(ic(el.dataset.ic)));
}
async function loadIcons() {
  try {
    state.icons = await api("GET", "/api/icons");
  } catch (_) {}
  fillIcons();
}

// ---------- sheet ----------
function openSheet(...content) {
  $("sheetBody").replaceChildren(...content.flat(Infinity).filter((c) => c != null && c !== false));
  $("sheet").classList.remove("hidden");
}
function closeSheet() { $("sheet").classList.add("hidden"); $("sheetBody").replaceChildren(); }
document.querySelector(".sheet-backdrop").addEventListener("click", closeSheet);

function inboxPicker(items, selected, onChange) {
  return h("div", { class: "grid" }, items.map((it) => {
    const tile = h("div", { class: "tile" + (selected.has(it.id) ? " selected" : "") },
      media(it), h("span", { class: "em" }, it.emoji));
    tile.addEventListener("click", () => {
      if (selected.has(it.id)) selected.delete(it.id); else selected.add(it.id);
      tile.classList.toggle("selected");
      haptic();
      onChange?.();
    });
    return tile;
  }));
}

// ---------- навигация ----------
function showView(id) {
  for (const v of ["home", "pack", "admin", "chat"]) $(v).classList.toggle("hidden", v !== id);
  if (id !== "chat") stopChatPolling();
  if (id === "home") tg?.BackButton?.hide(); else tg?.BackButton?.show();
  window.scrollTo(0, 0);
}

async function goHome() {
  closeSheet();
  state.pack = null;
  showView("home");
  await withBusy("", loadHome).catch(() => {});
}
function goBack() {
  if (!$("chat").classList.contains("hidden")) { showView("admin"); refreshAdmin(); return; }
  goHome();
}
tg?.BackButton?.onClick(goBack);

// ---------- темы: чёрно-белая и бело-чёрная ----------
function currentTheme() { return document.documentElement.dataset.theme === "light" ? "light" : "dark"; }

function applyTheme(id, save = true) {
  id = id === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = id;
  const bg = id === "light" ? "#ffffff" : "#000000";
  try {
    if (tg?.isVersionAtLeast?.("6.1")) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); }
    if (tg?.isVersionAtLeast?.("7.10")) tg.setBottomBarColor(bg);
  } catch (_) {}
  fillIcons($("themeBtn"));
  if (!save) return;
  try { localStorage.setItem("theme", id); } catch (_) {}
  // облако Telegram — тема одна на всех устройствах юзера
  try { if (tg?.isVersionAtLeast?.("6.9")) tg.CloudStorage.setItem("theme", id); } catch (_) {}
}

function syncThemeFromCloud() {
  try {
    if (!tg?.isVersionAtLeast?.("6.9")) return;
    tg.CloudStorage.getItem("theme", (err, value) => {
      if (!err && value && value !== currentTheme()) applyTheme(value);
    });
  } catch (_) {}
}

// ---------- главная ----------
async function loadHome() {
  state.me = await api("GET", "/api/me");
  renderHome();
}

function renderHome() {
  const { packs, inbox, pending_title, is_admin } = state.me;
  $("adminBtn").classList.toggle("hidden", !is_admin);
  $("hsPacks").textContent = packs.length;
  $("hsStickers").textContent = packs.reduce((n, p) => n + p.count, 0);
  $("hsInbox").textContent = inbox.length;
  const name = tg?.initDataUnsafe?.user?.first_name;
  if (name) $("heroHello").textContent = `Привет, ${name}! Собирай стикерпаки в пару тапов`;
  const pending = $("pending");
  if (pending_title) {
    pending.textContent = `Бот ждёт первый стикер для набора «${pending_title}».`;
    pending.classList.remove("hidden");
  } else pending.classList.add("hidden");

  $("packs").replaceChildren(...(packs.length ? packs.map((p) => packCard(p)) : [
    h("div", { class: "empty" }, "Наборов пока нет. Создай тут или командой /new в боте."),
  ]));

  const ids = new Set(inbox.map((i) => i.id));
  for (const id of [...state.inboxSelected]) if (!ids.has(id)) state.inboxSelected.delete(id);
  $("inboxCount").textContent = inbox.length ? `· ${inbox.length}` : "";
  $("inboxSelectBtn").classList.toggle("hidden", !inbox.length);
  $("inboxSelectBtn").textContent = state.inboxSelect ? "Готово" : "Выбрать";
  const grid = $("inbox");
  if (!inbox.length) {
    grid.replaceChildren(h("div", { class: "empty", style: "grid-column: 1/-1" }, "Пусто"));
  } else if (state.inboxSelect) {
    grid.replaceChildren(inboxPicker(inbox, state.inboxSelected, updateInboxActions));
    grid.firstChild.style.display = "contents";
  } else {
    grid.replaceChildren(...inbox.map((it) =>
      h("div", { class: "tile", onclick: () => { state.inboxSelect = true; state.inboxSelected.add(it.id); renderHome(); } },
        media(it), h("span", { class: "em" }, it.emoji))));
  }
  updateInboxActions();
}

function updateInboxActions() {
  const n = state.inboxSelected.size;
  $("inboxActions").classList.toggle("hidden", !state.inboxSelect || n === 0);
  $("inboxToPack").textContent = `В набор (${n})`;
}

function packCard(p, onClick) {
  return h("div", { class: "pack-card", onclick: onClick || (() => openPack(p.name)) },
    h("div", { class: "cover" }, p.cover ? media(p.cover) : null),
    h("div", { class: "info" },
      h("div", { class: "t" }, p.title),
      h("div", { class: "muted small", style: "margin:0;display:flex;gap:8px;align-items:center" },
        stickersWord(p.count),
        p.access ? null : h("span", { class: "lock-badge" }, ic("lock"), "с подписью"))),
    p.active ? h("span", { class: "badge" }, "активный") : null,
    onClick ? null : h("span", { class: "chev" }, svgIcon("chevron")));
}

$("adminBtn").addEventListener("click", openAdmin);
$("themeBtn").addEventListener("click", () => { applyTheme(currentTheme() === "light" ? "dark" : "light"); haptic(); });
$("brandHome").addEventListener("click", () => { if ($("home").classList.contains("hidden")) goHome(); });

$("inboxSelectBtn").addEventListener("click", () => {
  state.inboxSelect = !state.inboxSelect;
  if (!state.inboxSelect) state.inboxSelected.clear();
  renderHome();
});

$("inboxDelete").addEventListener("click", async () => {
  const ids = [...state.inboxSelected];
  if (!(await confirmBox(`Удалить из входящих ${ids.length} шт.?`))) return;
  await withBusy("Удаляю…", async () => {
    const res = await api("POST", "/api/inbox/delete", { ids });
    state.me.inbox = res.inbox;
    state.inboxSelected.clear();
    state.inboxSelect = false;
    renderHome();
  });
});

$("inboxToPack").addEventListener("click", () => {
  const ids = [...state.inboxSelected];
  openSheet(
    h("h3", {}, `Куда добавить ${ids.length} шт.?`),
    h("div", { class: "list" },
      state.me.packs.map((p) => packCard(p, async () => {
        closeSheet();
        await withBusy("Добавляю стикеры…", () => api("POST", `/api/packs/${p.name}/stickers`, { inbox_ids: ids }));
        state.inboxSelected.clear();
        state.inboxSelect = false;
        success();
        toast("Добавлено");
        await loadHome();
      }))),
    h("button", { class: "btn primary wide", onclick: () => newPackSheet(new Set(ids)) }, ic("add"), " Новый набор из них"),
  );
});

$("newPackBtn").addEventListener("click", () => newPackSheet(new Set()));

function newPackSheet(selected) {
  const { inbox, max_title, suffix, price } = state.me;
  const title = h("input", { class: "field text", placeholder: "Название набора", maxlength: String(max_title), enterkeyhint: "done" });
  const getTitle = () => {
    const t = title.value.trim();
    if (!t) { toast("Сначала введи название"); title.focus(); haptic("medium"); }
    return t;
  };
  const done = (pack) => {
    state.inboxSelected.clear();
    state.inboxSelect = false;
    success();
    showPack(pack);
  };

  // 1) из входящих
  const create = h("button", { class: "btn primary wide" });
  const refresh = () => {
    create.disabled = !selected.size;
    create.replaceChildren(ic("check"), selected.size ? ` Создать из выбранных (${selected.size})` : " Отметь стикеры выше");
  };
  create.addEventListener("click", async () => {
    const t = getTitle();
    if (!t || !selected.size) return;
    closeSheet();
    done(await withBusy("Создаю набор…", () => api("POST", "/api/packs", { title: t, inbox_ids: [...selected] })));
  });
  refresh();

  // 2) из файла
  const file = h("input", { type: "file", accept: "image/*,.tgs,.webm", hidden: true });
  const uploadBtn = h("label", { class: "btn wide" + (inbox.length ? "" : " primary") }, ic("upload"), " Создать из картинки", file);
  uploadBtn.addEventListener("click", (e) => { if (!title.value.trim()) { e.preventDefault(); getTitle(); } });
  file.addEventListener("change", async () => {
    const f = file.files[0];
    file.value = "";
    const t = getTitle();
    if (!f || !t) return;
    const fd = new FormData();
    fd.append("title", t);
    fd.append("emojis", "🙂");
    fd.append("file", f, f.name);
    closeSheet();
    done(await withBusy("Создаю набор…", () => api("POST", "/api/packs/new/upload", fd)));
  });

  // 3) через бота
  const viaBot = h("button", { class: "btn wide" }, ic("chat"), " Кинуть стикеры боту");
  viaBot.addEventListener("click", async () => {
    const t = getTitle();
    if (!t) return;
    await withBusy("", () => api("POST", "/api/packs/new/pending", { title: t }));
    closeSheet();
    success();
    const msg = `Набор «${t}» ждёт стикеры. Кинь боту первый стикер или картинку — с него набор и создастся, остальные полетят туда же.`;
    if (tg?.showAlert) tg.showAlert(msg, () => tg.close()); else toast(msg);
  });

  title.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (selected.size) create.click(); else title.blur();
  });

  openSheet(
    h("h3", {}, "Новый набор"),
    title,
    max_title < 64
      ? h("p", { class: "muted small" }, `До ${max_title} символов: к названию добавится «${suffix.trim()}». Убрать — ${price} звёзд за набор.`)
      : null,
    h("p", { class: "muted small" }, "В наборе должен быть хотя бы один стикер. Выбери, откуда его взять:"),
    inbox.length ? [h("div", { class: "lbl" }, ic("inbox"), " Из входящих"), inboxPicker(inbox, selected, refresh), create] : null,
    uploadBtn,
    viaBot,
  );
  title.focus();
}

// ---------- набор ----------
let sortable = null;
let dragging = false;

async function openPack(name) {
  const pack = await withBusy("Загружаю…", () => api("GET", `/api/packs/${name}`));
  showPack(pack);
}

function showPack(pack) {
  state.pack = pack;
  showView("pack");
  renderPack();
}

function renderPack() {
  const p = state.pack;
  const me = state.me || {};
  $("packTitle").value = p.title;
  $("packTitle").maxLength = p.max_title;
  $("titleHint").textContent = p.access ? "" : `В Telegram: «${p.full_title}»`;
  $("packCount").textContent = `${stickersWord(p.count)} из 120`;
  $("packActive").checked = p.active;
  $("packLinkText").textContent = p.link.replace("https://", "");
  $("packLinkEdit").disabled = false;
  $("packLinkEdit").classList.toggle("locked", !p.access);

  $("accessCard").classList.toggle("hidden", p.access);
  if (!p.access) {
    $("accessText").textContent =
      `Купи доступ к этому набору: подпись «${(me.suffix || "").trim()}» уйдёт, название — до 64 символов, ссылку можно будет поменять на свою.`;
    $("buyBtn").replaceChildren(ic("star"), ` Купить за ${me.price ?? "?"} звёзд`);
  }

  const grid = $("stickers");
  grid.replaceChildren(...p.stickers.map((s) =>
    h("div", { class: "tile", "data-uid": s.uid, onclick: () => { if (!dragging) stickerSheet(s); } },
      media(s), h("span", { class: "em" }, s.emojis.join("")))));
  sortable?.destroy();
  sortable = Sortable.create(grid, {
    animation: 160,
    delay: 220,
    delayOnTouchOnly: true,
    touchStartThreshold: 6,
    ghostClass: "ghost",
    chosenClass: "chosen",
    onStart: () => { dragging = true; haptic("medium"); },
    onEnd: async (ev) => {
      setTimeout(() => (dragging = false), 50);
      if (ev.oldIndex === ev.newIndex) return;
      const order = [...grid.children].map((el) => el.dataset.uid);
      try {
        state.pack = await withBusy("Сохраняю порядок…", () =>
          api("PUT", `/api/packs/${p.name}/order`, { order }));
        toast("Порядок сохранён");
      } catch (_) {
        state.pack = await api("GET", `/api/packs/${p.name}`).catch(() => state.pack);
      }
      renderPack();
    },
  });
}

async function saveTitle() {
  const input = $("packTitle");
  const t = input.value.trim();
  const p = state.pack;
  if (!p || t === p.title) return;
  if (!t) { input.value = p.title; return; }
  try {
    state.pack = await withBusy("Переименовываю…", () => api("PATCH", `/api/packs/${p.name}`, { title: t }));
    toast("Название сохранено");
    renderPack();
  } catch (_) {
    input.value = p.title;
  }
}
$("packTitle").addEventListener("blur", saveTitle);
$("packTitle").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });

$("packOpen").addEventListener("click", () => {
  const link = state.pack.link;
  if (tg?.openTelegramLink) tg.openTelegramLink(link); else window.open(link, "_blank");
});

$("buyBtn").addEventListener("click", async () => {
  const p = state.pack;
  const { url } = await withBusy("Готовлю оплату…", () => api("POST", `/api/packs/${p.name}/invoice`));
  if (!tg?.openInvoice) { window.open(url, "_blank"); return; }
  tg.openInvoice(url, async (status) => {
    if (status !== "paid") {
      if (status === "failed") toast("Оплата не прошла");
      return;
    }
    success();
    // бот получает подтверждение оплаты чуть позже, подождём
    await withBusy("Применяю доступ…", async () => {
      for (let i = 0; i < 15; i++) {
        const fresh = await api("GET", `/api/packs/${p.name}`);
        if (fresh.access) { state.pack = fresh; break; }
        await sleep(1000);
      }
    }).catch(() => {});
    if (state.me) state.me.packs = state.me.packs.map((x) => (x.name === p.name ? { ...x, access: true } : x));
    renderPack();
    toast(state.pack.access ? "Доступ куплен — подпись убрана" : "Оплата прошла, доступ вот-вот применится");
  });
});

$("packLinkEdit").addEventListener("click", () => {
  const p = state.pack;
  if (!p.access) {
    haptic("medium");
    toast(`Смена ссылки — после покупки доступа (${state.me?.price ?? ""} звёзд)`);
    $("accessCard").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const suffix = `_by_${state.me?.bot || ""}`;
  const current = p.name.endsWith(suffix) ? p.name.slice(0, -suffix.length) : p.name;
  const input = h("input", { class: "field text", value: current, autocapitalize: "off", autocorrect: "off", spellcheck: "false" });
  const preview = h("div", { class: "link-text", style: "margin-top:8px" });
  const upd = () => { preview.textContent = `t.me/addstickers/${input.value.trim()}${suffix}`; };
  input.addEventListener("input", upd);
  upd();
  const save = h("button", { class: "btn primary wide" }, ic("check"), " Сменить ссылку");
  save.addEventListener("click", async () => {
    const slug = input.value.trim();
    if (!(await confirmBox("Telegram не умеет переименовывать ссылку, поэтому набор пересоздаётся. Старая ссылка перестанет работать, у тех, кто уже добавил набор, он пропадёт. Продолжаем?"))) return;
    closeSheet();
    state.pack = await withBusy("Переношу набор на новую ссылку…", () =>
      api("POST", `/api/packs/${p.name}/link`, { slug }));
    success();
    toast("Ссылка изменена");
    renderPack();
  });
  openSheet(
    h("h3", {}, "Новая ссылка"),
    h("p", { class: "muted small" }, `Латиница, цифры и _. Окончание ${suffix} Telegram добавляет обязательно.`),
    input, preview, save,
  );
  input.focus();
});

$("packActive").addEventListener("change", async (e) => {
  const p = state.pack;
  try {
    state.pack = await withBusy("", () => api("POST", `/api/packs/${p.name}/active`, { active: e.target.checked }));
    toast(state.pack.active ? "Теперь бот добавляет сюда" : "Бот сюда больше не добавляет");
  } catch (_) {
    e.target.checked = p.active;
  }
});

$("deletePack").addEventListener("click", async () => {
  const p = state.pack;
  if (!(await confirmBox(`Удалить набор «${p.title}» насовсем? Его не будет и у тех, кто его добавил.`))) return;
  await withBusy("Удаляю…", () => api("DELETE", `/api/packs/${p.name}`));
  toast("Набор удалён");
  await goHome();
});

function stickerSheet(s) {
  const p = state.pack;
  const input = h("input", { class: "field", value: s.emojis.join(""), placeholder: "Эмодзи стикера", inputmode: "text" });
  const chips = h("div", { class: "emoji-chips" });
  const save = h("button", { class: "btn primary" }, ic("check"), " Сохранить");
  const refresh = () => {
    const list = parseEmojis(input.value);
    chips.replaceChildren(...(list.length ? list.map((e) => h("span", { class: "chip" }, e))
      : [h("span", { class: "muted" }, "Нужен хотя бы один эмодзи")]));
    save.disabled = !list.length;
  };
  input.addEventListener("input", refresh);
  refresh();

  save.addEventListener("click", async () => {
    const emojis = parseEmojis(input.value);
    closeSheet();
    state.pack = await withBusy("Сохраняю эмодзи…", () =>
      api("PUT", `/api/packs/${p.name}/stickers/${s.uid}`, { emojis }));
    toast("Эмодзи обновлены");
    renderPack();
  });

  const idx = p.stickers.findIndex((x) => x.uid === s.uid);
  const move = async (to) => {
    const order = p.stickers.map((x) => x.uid);
    order.splice(idx, 1);
    order.splice(to, 0, s.uid);
    closeSheet();
    state.pack = await withBusy("Двигаю…", () => api("PUT", `/api/packs/${p.name}/order`, { order }));
    renderPack();
  };

  const del = h("button", { class: "btn danger" }, ic("trash"), " Удалить");
  del.addEventListener("click", async () => {
    if (!(await confirmBox("Удалить стикер из набора?"))) return;
    closeSheet();
    const res = await withBusy("Удаляю…", () => api("DELETE", `/api/packs/${p.name}/stickers/${s.uid}`));
    if (res.deleted_pack) { toast("Это был последний стикер — набор исчез"); await goHome(); return; }
    state.pack = res;
    toast("Удалён");
    renderPack();
  });

  openSheet(
    h("div", { class: "preview" }, media(s)),
    h("p", { class: "muted small" }, "Эмодзи (до 20). По ним стикер находится в подсказках:"),
    input, chips,
    h("div", { class: "row" },
      h("button", { class: "btn", disabled: idx === 0, onclick: () => move(0) }, ic("back"), " В начало"),
      h("button", { class: "btn", disabled: idx === p.stickers.length - 1, onclick: () => move(p.stickers.length - 1) }, "В конец ", ic("chevron"))),
    h("div", { class: "row" }, del, save),
  );
}

$("addFromInbox").addEventListener("click", async () => {
  const me = await withBusy("", () => api("GET", "/api/me"));
  state.me = me;
  const p = state.pack;
  if (!me.inbox.length) {
    toast("Входящие пустые — кинь боту стикеры при выключенном активном наборе");
    return;
  }
  const selected = new Set();
  const add = h("button", { class: "btn primary wide", disabled: true }, "Выбери стикеры");
  const refresh = () => {
    add.disabled = !selected.size;
    add.textContent = selected.size ? `Добавить (${selected.size})` : "Выбери стикеры";
  };
  add.addEventListener("click", async () => {
    closeSheet();
    try {
      state.pack = await withBusy("Добавляю…", () =>
        api("POST", `/api/packs/${p.name}/stickers`, { inbox_ids: [...selected] }));
      toast("Добавлено");
    } catch (_) {
      state.pack = await api("GET", `/api/packs/${p.name}`).catch(() => state.pack);
    }
    renderPack();
  });
  openSheet(h("h3", {}, "Добавить из входящих"), inboxPicker(me.inbox, selected, refresh), add);
});

$("uploadInput").addEventListener("change", (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  const p = state.pack;
  const input = h("input", { class: "field", placeholder: "🙂", value: "🙂" });
  const go = h("button", { class: "btn primary wide" }, ic("upload"), ` Загрузить (${files.length})`);
  go.addEventListener("click", async () => {
    const emojis = parseEmojis(input.value);
    if (!emojis.length) { toast("Нужен хотя бы один эмодзи"); return; }
    closeSheet();
    let done = 0;
    try {
      for (const f of files) {
        const fd = new FormData();
        fd.append("emojis", emojis.join(" "));
        fd.append("file", f, f.name);
        state.pack = await withBusy(`Загружаю ${done + 1}/${files.length}…`, () =>
          api("POST", `/api/packs/${p.name}/upload`, fd));
        done++;
      }
      toast(`Загружено: ${done}`);
    } catch (_) {
      state.pack = await api("GET", `/api/packs/${p.name}`).catch(() => state.pack);
    }
    renderPack();
  });
  openSheet(
    h("h3", {}, "Загрузка файлов"),
    h("p", { class: "muted small" },
      "Картинки ужмутся до 512px автоматически. .tgs и .webm должны уже соответствовать требованиям Telegram. Эмодзи для всех файлов:"),
    input, go,
  );
});

// ---------- админка ----------
async function openAdmin() {
  await withBusy("Загружаю админку…", loadAdmin);
  showView("admin");
  renderAdmin();
  if (state.adminTab === "chats") loadDialogs();
}

async function loadAdmin() {
  state.admin = await api("GET", "/api/admin");
  // в админке нужен полный список иконок для выбора
  if (!state.icons.all) await loadIcons();
  await loadLookIcons();
}

document.querySelectorAll("#adminTabs .tab").forEach((t) => t.addEventListener("click", () => {
  state.adminTab = t.dataset.tab;
  haptic();
  renderAdmin();
  if (state.adminTab === "chats") loadDialogs();
}));

function renderAdmin() {
  const a = state.admin;
  document.querySelectorAll("#adminTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === state.adminTab));
  document.querySelectorAll("#admin .pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== state.adminTab));

  $("unreadBadge").textContent = a.unread;
  $("unreadBadge").classList.toggle("hidden", !a.unread);
  renderScreenEditor();

  // статистика
  const st = a.stats;
  const tile = (slot, k, v) => h("div", { class: "stat" }, h("div", { class: "v" }, ic(slot), String(v)), h("div", { class: "k" }, k));
  $("statTiles").replaceChildren(
    tile("user", "пользователей", st.users),
    tile("stop", "заблокировали бота", st.blocked),
    tile("pack", "наборов", st.packs),
    tile("unlock", "с купленным доступом", st.paid_packs),
    tile("star", "звёзд заработано", st.stars),
    tile("money", "оплат", st.payments),
  );
  $("payments").replaceChildren(...(a.payments.length ? a.payments.map((p) =>
    h("div", { class: "pay" + (p.refunded ? " refunded" : "") },
      h("div", { class: "info" },
        h("div", { class: "t" }, `${p.amount} звёзд · ${p.pack || "набор удалён"}`),
        h("div", { class: "muted small", style: "margin:0" }, `${fmtDate(p.created_at)} · id ${p.user_id}${p.refunded ? " · возвращено" : ""}`)),
      p.refunded ? null : h("button", { class: "link", onclick: () => refund(p) }, "Вернуть")))
    : [h("div", { class: "empty" }, "Оплат пока нет")]));

  // продажи
  $("setPrice").value = a.settings.price;
  $("setSuffix").value = a.settings.suffix;
  updateSuffixHint();

  // рассылка
  const b = a.broadcast;
  $("bcEnabled").checked = b.enabled;
  $("bcWeekday").replaceChildren(...a.weekdays.map((d, i) => h("option", { value: String(i), selected: i === b.weekday }, d)));
  $("bcHour").replaceChildren(...Array.from({ length: 24 }, (_, i) =>
    h("option", { value: String(i), selected: i === b.hour }, `${String(i).padStart(2, "0")}:00`)));
  $("bcText").value = b.text;
  $("bcBtnText").value = b.button_text;
  $("bcBtnUrl").value = b.button_url;
  $("bcPhotoBox").replaceChildren(b.photo ? h("img", { src: b.photo }) : h("div", { class: "muted small" }, "Без картинки"));
  $("bcPhotoRemove").classList.toggle("hidden", !b.photo);
  $("bcSchedule").textContent = b.enabled
    ? (b.next_run ? `Следующая: ${fmtDate(b.next_run, b.timezone)} (${b.timezone})` : "")
    : "Автоматическая рассылка выключена";
  const r = b.last_result;
  $("bcLast").textContent = b.running
    ? `Идёт рассылка: ${b.running.done}/${b.running.total}`
    : r ? `Последняя: ${fmtDate(r.at, b.timezone)} — доставлено ${r.ok}, заблокировали ${r.blocked}, ошибок ${r.failed}` : "Ещё не рассылали";
  updateTextHint();
  if (b.running) setTimeout(() => state.adminTab === "post" && !$("admin").classList.contains("hidden") && refreshAdmin(), 3000);

  // оформление
  const missing = [...(a.heading_letters || "")].filter((ch) => !(a.letters || "").includes(ch)).join("");
  $("lettersInfo").textContent = (a.letters ? `Знаю буквы: ${a.letters}` : "Буквы ещё не заданы — заголовки будут жирным капсом")
    + (missing ? `. Для заголовков не хватает: ${missing}` : ". Для всех заголовков хватает");
  $("lettersReset").classList.toggle("hidden", !a.letters);
  const rej = a.emoji_rejected_at && a.emoji_rejected_at > Date.now() / 1000 - 86400 * 3;
  $("emojiRejected").classList.toggle("hidden", !rej);
  if (rej) $("emojiRejected").textContent = `Telegram отклонил премиум-эмодзи в сообщении бота (${fmtDate(a.emoji_rejected_at)}) — бот отправил обычные. Так бывает, если у владельца бота нет Telegram Premium.`;
  $("iconPack").value = a.settings.icon_pack;
  $("accentPack").value = a.settings.accent_pack;
  $("appIconPack").value = a.settings.app_icon_pack;
  $("botCustomEmoji").checked = a.settings.bot_custom_emoji;
  const scopeApp = state.lookScope === "app";
  const look = state.lookIcons || state.icons;
  const map = (scopeApp ? a.settings.app_icon_map : a.settings.icon_map) || {};
  const errors = scopeApp ? a.app_icon_errors : a.icon_errors;
  $("slotList").replaceChildren(...Object.entries(look.slots || {}).map(([key, s]) =>
    h("div", { class: "slot", onclick: () => pickIcon(key) },
      lookIcon(key),
      h("span", { class: "name" }, s.label),
      h("span", { class: "muted small", style: "margin:0" }, map[key] ? "вручную" : (s.icon ? "из набора" : (scopeApp ? "встроенная" : "обычный эмодзи"))))));
  if (errors?.length) {
    $("slotList").prepend(h("div", { class: "note" }, `Не загрузились наборы: ${errors.join(", ")} — проверь имена выше.`));
  } else if (!(look.all || []).length) {
    $("slotList").prepend(h("div", { class: "note" }, "Набор иконок не загрузился — проверь имя набора выше."));
  }
}

async function refreshAdmin() {
  try { state.admin = await api("GET", "/api/admin"); renderAdmin(); } catch (_) {}
}

function updateSuffixHint() {
  const len = $("setSuffix").value.length;
  $("suffixHint").textContent = `${len}/40 · без доступа на название остаётся ${64 - len} символов`;
}
$("setSuffix").addEventListener("input", updateSuffixHint);

function updateTextHint() {
  const limit = state.admin?.broadcast.photo ? 1024 : 4096;
  $("bcTextHint").textContent = `${$("bcText").value.length}/${limit}`;
}
$("bcText").addEventListener("input", updateTextHint);

$("saveSales").addEventListener("click", async () => {
  state.admin = await withBusy("Сохраняю…", () => api("PUT", "/api/admin/settings", {
    price: Number($("setPrice").value), suffix: $("setSuffix").value,
  }));
  if (state.me) { state.me.price = state.admin.settings.price; state.me.suffix = state.admin.settings.suffix; }
  success(); toast("Сохранено"); renderAdmin();
});

function broadcastBody() {
  return {
    enabled: $("bcEnabled").checked,
    weekday: Number($("bcWeekday").value),
    hour: Number($("bcHour").value),
    text: $("bcText").value,
    button_text: $("bcBtnText").value,
    button_url: $("bcBtnUrl").value,
  };
}
async function saveBroadcast(silent) {
  state.admin = await withBusy("Сохраняю…", () => api("PUT", "/api/admin/broadcast", broadcastBody()));
  if (!silent) { success(); toast("Пост сохранён"); }
  renderAdmin();
}
$("bcSave").addEventListener("click", () => saveBroadcast(false).catch(() => {}));
$("bcEnabled").addEventListener("change", () => saveBroadcast(true).catch(() => { $("bcEnabled").checked = !$("bcEnabled").checked; }));

async function capture(target, what) {
  await withBusy("", () => api("POST", "/api/admin/capture", { target }));
  tg?.showAlert
    ? tg.showAlert(`Теперь перешли или отправь боту сообщение — оно станет ${what}. Премиум-эмодзи, форматирование и картинка сохранятся.`, () => tg.close())
    : toast("Теперь перешли боту сообщение");
}
$("bcCapture").addEventListener("click", () => capture("broadcast", "постом для рассылки").catch(() => {}));

$("bcTest").addEventListener("click", async () => {
  await saveBroadcast(true);
  await withBusy("Отправляю тебе…", () => api("POST", "/api/admin/broadcast/test"));
  success(); toast("Отправил тебе в личку — глянь, как выглядит");
});

$("bcSend").addEventListener("click", async () => {
  await saveBroadcast(true);
  const n = state.admin.stats.users - state.admin.stats.blocked;
  if (!(await confirmBox(`Разослать пост прямо сейчас ${n} пользователям?`))) return;
  state.admin = await withBusy("Запускаю…", () => api("POST", "/api/admin/broadcast/send"));
  toast("Рассылка пошла");
  renderAdmin();
});

$("bcPhotoInput").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  await saveBroadcast(true).catch(() => {});
  const fd = new FormData();
  fd.append("file", f, f.name);
  state.admin = await withBusy("Загружаю картинку…", () => api("POST", "/api/admin/broadcast/photo", fd));
  renderAdmin();
});

$("bcPhotoRemove").addEventListener("click", async () => {
  state.admin = await withBusy("", () => api("PUT", "/api/admin/broadcast", { ...broadcastBody(), remove_photo: true }));
  renderAdmin();
});

async function refund(p) {
  if (!(await confirmBox(`Вернуть ${p.amount} звёзд пользователю ${p.user_id}? Подпись вернётся в название его набора.`))) return;
  state.admin = await withBusy("Возвращаю звёзды…", () => api("POST", "/api/admin/refund", { id: p.id }));
  toast("Звёзды возвращены");
  renderAdmin();
}

state.lookScope = "app";
state.lookIcons = null;

async function loadLookIcons() {
  state.lookIcons = state.lookScope === "app" ? state.icons : await api("GET", "/api/icons?scope=bot").catch(() => ({ slots: {}, all: [] }));
}

async function saveLook(body, text) {
  state.admin = await withBusy(text, () => api("PUT", "/api/admin/settings", body));
  await loadIcons();
  await loadLookIcons();
  renderAdmin();
}

document.querySelectorAll("#lookScope .chip-btn").forEach((b) => b.addEventListener("click", async () => {
  state.lookScope = b.dataset.scope;
  document.querySelectorAll("#lookScope .chip-btn").forEach((x) => x.classList.toggle("active", x === b));
  await withBusy("", loadLookIcons).catch(() => {});
  renderAdmin();
}));

function lookIcon(slot) {
  const s = state.lookIcons?.slots?.[slot];
  if (s?.icon) return h("span", { class: "ic ic-pack" }, media(s.icon));
  return state.lookScope === "app" ? svgIcon(slot) : h("span", { class: "ic ic-text" }, s?.fallback || "");
}
$("saveIconPack").addEventListener("click", () => saveLook({ icon_pack: $("iconPack").value, accent_pack: $("accentPack").value, app_icon_pack: $("appIconPack").value }, "Загружаю наборы…").then(() => toast("Применено")).catch(() => {}));
$("lettersCapture").addEventListener("click", () => capture("letters", "уроком букв: первая строка — буквы текстом, вторая — те же буквы эмодзи").catch(() => {}));
$("lettersReset").addEventListener("click", async () => {
  if (!(await confirmBox("Забыть все буквы?"))) return;
  await saveLook({ reset_letters: true }, "Сбрасываю…").catch(() => {});
});
$("botCustomEmoji").addEventListener("change", (e) => saveLook({ bot_custom_emoji: e.target.checked }, "").catch(() => {}));

function pickIcon(slot) {
  const key = state.lookScope === "app" ? "app_icon_map" : "icon_map";
  const map = { ...(state.admin.settings[key] || {}) };
  const look = state.lookIcons || state.icons;
  const choose = async (id) => {
    if (id) map[slot] = id; else delete map[slot];
    closeSheet();
    await saveLook({ [key]: map }, "Сохраняю…").catch(() => {});
  };
  openSheet(
    h("h3", {}, `Иконка: ${look.slots[slot].label}`),
    h("div", { class: "icon-grid" },
      h("div", { class: "tile auto", onclick: () => choose(null) }, "Авто"),
      h("div", { class: "tile auto", onclick: () => choose("none") }, state.lookScope === "app" ? svgIcon(slot) : "Без"),
      (look.all || []).map((i) => h("div", { class: "tile" + (map[slot] === i.id ? " selected" : ""), onclick: () => choose(i.id) }, media(i)))),
  );
}

// ---------- экраны меню ----------
state.screenKey = "menu";

function currentScreen() {
  return state.admin.screens.find((x) => x.key === state.screenKey);
}

function renderScreenEditor() {
  const a = state.admin;
  $("screenChips").replaceChildren(...a.screens.map((sc) =>
    h("button", { class: "chip-btn" + (sc.key === state.screenKey ? " active" : ""), onclick: () => { state.screenKey = sc.key; renderScreenEditor(); } },
      sc.label, sc.photo ? h("span", { class: "dot", title: "есть баннер" }) : null)));
  const sc = currentScreen();
  $("scPhotoBox").replaceChildren(sc.photo ? h("img", { src: sc.photo }) : h("div", { class: "muted small" }, "Без баннера"));
  $("scPhotoRemove").classList.toggle("hidden", !sc.photo);
  $("scPhotoRestore").classList.toggle("hidden", sc.default_photo);
  $("scPhotoNote").textContent = sc.default_photo && sc.photo ? "Стандартный баннер XMAPKET" : "";
  if (document.activeElement !== $("scText") || $("scText").dataset.key !== sc.key) {
    $("scText").value = sc.text;
    $("scText").dataset.key = sc.key;
  }
  $("scReset").classList.toggle("hidden", !sc.custom);
  $("orderLinks").classList.toggle("hidden", sc.key !== "order");
  $("olBanner").value = a.order_links.banner;
  $("olEmoji").value = a.order_links.emoji;
  $("olVideo").value = a.order_links.video;
  updateScreenHint();
  const re = a.rich_error;
  $("richError").classList.toggle("hidden", !re);
  if (re) $("richError").textContent = `Telegram не принял оформление блоками (${fmtDate(re.at)}): ${re.error}. Бот отправил обычное сообщение. Скинь этот текст разработчику.`;
}

function visibleLength(text) {
  const d = document.createElement("div");
  d.innerHTML = text.replace(/\{ic:[a-z_]+\}/g, "X").replace(/<[^>]+>/g, "");
  return d.textContent.length;
}
function updateScreenHint() {
  const sc = state.admin && currentScreen();
  if (!sc) return;
  const len = visibleLength($("scText").value);
  $("scTextHint").textContent = `${len} символов`;
}
$("scText").addEventListener("input", updateScreenHint);

async function saveScreen(extra = {}, msg = "Экран сохранён") {
  const sc = currentScreen();
  const body = { text: $("scText").value, ...extra };
  if (sc.key === "order") body.links = { banner: $("olBanner").value, emoji: $("olEmoji").value, video: $("olVideo").value };
  state.admin = await withBusy("Сохраняю…", () => api("PUT", `/api/admin/screens/${sc.key}`, body));
  if (msg) { success(); toast(msg); }
  renderAdmin();
}
$("scSave").addEventListener("click", () => saveScreen().catch(() => {}));
$("scReset").addEventListener("click", async () => {
  if (!(await confirmBox("Вернуть стандартный текст этого экрана?"))) return;
  $("scText").value = "";
  await saveScreen({}, "Вернул стандартный").catch(() => {});
});
$("scTest").addEventListener("click", async () => {
  await saveScreen({}, "").catch(() => { throw 0; });
  await withBusy("Отправляю…", () => api("POST", `/api/admin/screens/${state.screenKey}/test`));
  success(); toast("Отправил тебе в бота — глянь");
});
$("scCapture").addEventListener("click", () => capture(`screen:${state.screenKey}`, `экраном «${currentScreen().label}»`).catch(() => {}));
$("scPhotoInput").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f, f.name);
  state.admin = await withBusy("Загружаю баннер…", () => api("POST", `/api/admin/screens/${state.screenKey}/photo`, fd));
  renderAdmin();
});
$("scPhotoRemove").addEventListener("click", () => saveScreen({ remove_photo: true }, "Баннер убран").catch(() => {}));
$("scPhotoRestore").addEventListener("click", () => saveScreen({ restore_photo: true }, "Вернул стандартный баннер").catch(() => {}));

// ---------- спамблок: диалоги и чат ----------
const KIND_LABEL = { photo: "Фото", sticker: "Стикер", video: "Видео", animation: "GIF", video_note: "Кружок", voice: "Голосовое", audio: "Аудио", document: "Файл" };

function plainText(htmlText) {
  const d = document.createElement("div");
  d.innerHTML = (htmlText || "").replace(/<[^>]+>/g, "");
  return d.textContent;
}

function preview(m) {
  const t = plainText(m.text);
  if (m.kind === "text") return t;
  return `${KIND_LABEL[m.kind] || m.kind}${t ? ": " + t : ""}`;
}

// Безопасный рендер HTML от Telegram: только разрешённые теги
const ALLOWED = new Set(["B", "STRONG", "I", "EM", "U", "INS", "S", "STRIKE", "DEL", "CODE", "PRE", "BLOCKQUOTE", "A", "SPAN", "TG-EMOJI", "BR"]);
function safeHtml(htmlText) {
  const doc = new DOMParser().parseFromString(`<div>${htmlText || ""}</div>`, "text/html");
  const walk = (node) => {
    const out = document.createDocumentFragment();
    for (const ch of node.childNodes) {
      if (ch.nodeType === Node.TEXT_NODE) { out.append(ch.textContent); continue; }
      if (ch.nodeType !== Node.ELEMENT_NODE) continue;
      if (!ALLOWED.has(ch.tagName) || ch.tagName === "TG-EMOJI") { out.append(walk(ch)); continue; }
      const el = document.createElement(ch.tagName === "SPAN" ? "span" : ch.tagName.toLowerCase());
      if (ch.tagName === "A") {
        const href = ch.getAttribute("href") || "";
        if (/^(https?:|tg:)/i.test(href)) { el.href = href; el.target = "_blank"; el.rel = "noopener"; }
      }
      if (ch.tagName === "SPAN" && ch.classList.contains("tg-spoiler")) {
        el.className = "tg-spoiler";
        el.addEventListener("click", () => el.classList.add("open"));
      }
      el.append(walk(ch));
      out.append(el);
    }
    return out;
  };
  return walk(doc.body.firstChild);
}

const timeOf = (ts) => new Date(ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
const dayOf = (ts) => new Date(ts * 1000).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });

async function loadDialogs() {
  try {
    const { dialogs } = await api("GET", "/api/admin/chats");
    renderDialogs(dialogs);
  } catch (e) { toast(e.message); }
}

function renderDialogs(dialogs) {
  $("dialogs").replaceChildren(...(dialogs.length ? dialogs.map((d) =>
    h("div", { class: "dialog", onclick: () => openChat(d.user_id) },
      h("div", { class: "avatar" }, (d.name || "?").trim().charAt(0).toUpperCase()),
      h("div", { class: "info" },
        h("div", { class: "top" },
          h("span", { class: "t" }, d.name, d.username ? h("span", { class: "muted", style: "font-weight:400" }, ` @${d.username}`) : null),
          h("span", { class: "when" }, d.last.at > Date.now() / 1000 - 86400 ? timeOf(d.last.at) : dayOf(d.last.at))),
        h("div", { class: "prev" },
          h("span", {}, (d.last.out ? "Ты: " : "") + preview(d.last)),
          d.blocked ? h("span", { class: "lock-badge" }, "заблокировал") : null,
          d.unread ? h("span", { class: "count" }, String(d.unread)) : null))))
    : [h("div", { class: "empty" }, "Пока никто не писал")]));
}

const chat = { uid: null, last: 0, timer: null, lastDay: null };

function stopChatPolling() { clearInterval(chat.timer); chat.timer = null; }

async function openChat(uid) {
  chat.uid = uid; chat.last = 0; chat.lastDay = null;
  $("chatMessages").replaceChildren();
  showView("chat");
  await pollChat(true);
  stopChatPolling();
  chat.timer = setInterval(() => pollChat(false), 4000);
  $("chatText").focus();
}

async function pollChat(first) {
  if (chat.uid == null) return;
  let data;
  try {
    data = await api("GET", `/api/admin/chats/${chat.uid}?after=${chat.last}`);
  } catch (e) { if (first) toast(e.message); return; }
  const u = data.user;
  $("chatName").textContent = u.name;
  $("chatSub").textContent = [u.username ? "@" + u.username : `id ${u.user_id}`, u.blocked ? "заблокировал бота" : (u.in_chat ? "в спамблоке" : "")].filter(Boolean).join(" · ");
  appendMessages(data.messages, first);
}

function appendMessages(list, force = false) {
  if (!list.length) return;
  const box = $("chatMessages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  for (const m of list) {
    if (m.id <= chat.last) continue;
    chat.last = m.id;
    const day = dayOf(m.at);
    if (day !== chat.lastDay) { chat.lastDay = day; box.append(h("div", { class: "day-sep" }, day)); }
    box.append(messageBubble(m));
  }
  if (force || nearBottom) box.scrollTop = box.scrollHeight;
}

function messageBubble(m) {
  const parts = [];
  if (m.kind === "photo") parts.push(h("img", { class: "ph", src: m.file, onclick: () => window.open(m.file, "_blank") }));
  else if (m.kind === "sticker") parts.push(media({ format: m.format, file: m.file, thumb: m.thumb }));
  else if (m.kind === "video" || m.kind === "video_note") parts.push(h("video", { src: m.file, poster: m.thumb, controls: true, playsinline: true }));
  else if (m.kind === "animation") parts.push(Object.assign(h("video", { src: m.file, poster: m.thumb, autoplay: true, loop: true, playsinline: true }), { muted: true }));
  else if (m.kind === "voice" || m.kind === "audio") parts.push(h("audio", { src: m.file, controls: true, preload: "none" }));
  else if (m.kind === "document") parts.push(h("a", { href: m.file, target: "_blank", class: "file-link" }, ic("clip"), " ", m.file_name || "Файл"));
  if (m.text && m.kind !== "sticker") parts.push(h("div", {}, safeHtml(m.text)));
  return h("div", { class: "msg" + (m.out ? " out" : "") + (m.kind === "sticker" ? " sticker" : "") },
    parts, h("div", { class: "time" }, timeOf(m.at)));
}

async function sendChat() {
  const text = $("chatText").value.trim();
  if (!text) return;
  $("chatSend").disabled = true;
  try {
    const data = await api("POST", `/api/admin/chats/${chat.uid}/send`, { text });
    $("chatText").value = "";
    autoGrow();
    appendMessages(data.messages, true);
    haptic();
  } catch (e) {
    toast(e.message);
  } finally {
    $("chatSend").disabled = false;
  }
}
function autoGrow() {
  const t = $("chatText");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 120) + "px";
}
$("chatText").addEventListener("input", autoGrow);
$("chatText").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !/Android|iPhone|iPad/i.test(navigator.userAgent)) { e.preventDefault(); sendChat(); }
});
$("chatSend").addEventListener("click", sendChat);
$("chatPhoto").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  const fd = new FormData();
  fd.append("text", $("chatText").value.trim());
  fd.append("file", f, f.name);
  const data = await withBusy("Отправляю фото…", () => api("POST", `/api/admin/chats/${chat.uid}/send`, fd)).catch(() => null);
  if (data) { $("chatText").value = ""; autoGrow(); appendMessages(data.messages, true); }
});

// ---------- старт ----------
if (!tg?.initData) {
  document.body.replaceChildren(h("div", { class: "view" },
    h("h1", {}, "Открой через бота"),
    h("p", { class: "muted" }, "Эта страница работает только как мини-апп внутри Telegram.")));
} else {
  applyTheme(currentTheme(), false);
  syncThemeFromCloud();
  fillIcons();
  withBusy("Загружаю…", () => Promise.all([loadIcons(), loadHome()]))
    .then(async () => {
      if (!state.me.is_admin) return;
      const chat = location.hash.match(/^#chat:(\d+)$/);
      if (location.hash === "#admin") openAdmin();
      else if (chat) { state.adminTab = "chats"; await openAdmin(); openChat(Number(chat[1])); }
    })
    .catch(() => {});
}
