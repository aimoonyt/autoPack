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

// ---------- иконки (премиум-эмодзи) ----------
function ic(slot) {
  const s = state.icons.slots[slot];
  if (s?.icon) return h("span", { class: "ic" }, media(s.icon));
  return h("span", { class: "ic ic-text" }, s?.fallback || "");
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
  for (const v of ["home", "pack", "admin"]) $(v).classList.toggle("hidden", v !== id);
  if (id === "home") tg?.BackButton?.hide(); else tg?.BackButton?.show();
  window.scrollTo(0, 0);
}

async function goHome() {
  closeSheet();
  state.pack = null;
  showView("home");
  await withBusy("", loadHome).catch(() => {});
}
tg?.BackButton?.onClick(goHome);

// ---------- главная ----------
async function loadHome() {
  state.me = await api("GET", "/api/me");
  renderHome();
}

function renderHome() {
  const { packs, inbox, pending_title, is_admin } = state.me;
  $("adminBtn").classList.toggle("hidden", !is_admin);
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
    p.active ? h("span", { class: "badge" }, "активный") : null);
}

$("adminBtn").addEventListener("click", openAdmin);

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
  const title = h("input", { class: "field text", placeholder: "Название набора", maxlength: String(max_title) });
  const create = h("button", { class: "btn primary wide" });
  const refresh = () => {
    create.disabled = !selected.size;
    create.textContent = selected.size ? `Создать (${selected.size})` : "Выбери стикеры";
  };
  create.addEventListener("click", async () => {
    const t = title.value.trim();
    if (!t) { toast("Введи название"); title.focus(); return; }
    closeSheet();
    const pack = await withBusy("Создаю набор…", () =>
      api("POST", "/api/packs", { title: t, inbox_ids: [...selected] }));
    state.inboxSelected.clear();
    state.inboxSelect = false;
    success();
    showPack(pack);
  });
  refresh();
  openSheet(
    h("h3", {}, "Новый набор"),
    title,
    max_title < 64
      ? h("p", { class: "muted small" }, `До ${max_title} символов: к названию добавится «${suffix.trim()}». Убрать — ${price} ⭐ за набор.`)
      : null,
    inbox.length
      ? [h("p", { class: "muted small" }, "Выбери стикеры из входящих (нужен хотя бы один):"),
         inboxPicker(inbox, selected, refresh), create]
      : h("p", { class: "muted" },
          "Входящие пустые. Кинь боту стикеры без активного набора (или /new Название прямо в боте) — и они появятся тут."),
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
    $("buyBtn").replaceChildren(ic("star"), ` Купить за ${me.price ?? "?"} ⭐`);
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
    toast(`Смена ссылки — после покупки доступа (${state.me?.price ?? ""} ⭐)`);
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
  const input = h("input", { class: "field", value: s.emojis.join(""), placeholder: "😀🔥", inputmode: "text" });
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
      h("button", { class: "btn", disabled: idx === 0, onclick: () => move(0) }, "⇤ В начало"),
      h("button", { class: "btn", disabled: idx === p.stickers.length - 1, onclick: () => move(p.stickers.length - 1) }, "В конец ⇥")),
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
}

async function loadAdmin() {
  state.admin = await api("GET", "/api/admin");
  // в админке нужен полный список иконок для выбора
  if (!state.icons.all) await loadIcons();
}

document.querySelectorAll("#adminTabs .tab").forEach((t) => t.addEventListener("click", () => {
  state.adminTab = t.dataset.tab;
  haptic();
  renderAdmin();
}));

function renderAdmin() {
  const a = state.admin;
  document.querySelectorAll("#adminTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === state.adminTab));
  document.querySelectorAll("#admin .pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== state.adminTab));

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
        h("div", { class: "t" }, `${p.amount} ⭐ · ${p.pack || "набор удалён"}`),
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
  $("iconPack").value = a.settings.icon_pack;
  $("botCustomEmoji").checked = a.settings.bot_custom_emoji;
  const slots = state.icons.slots || {};
  $("slotList").replaceChildren(...Object.entries(slots).map(([key, s]) =>
    h("div", { class: "slot", onclick: () => pickIcon(key) },
      ic(key),
      h("span", { class: "name" }, s.label),
      h("span", { class: "muted small", style: "margin:0" }, a.settings.icon_map[key] ? "вручную" : (s.icon ? "авто" : "обычный")))));
  if (!(state.icons.all || []).length) {
    $("slotList").prepend(h("div", { class: "note" }, "Набор эмодзи не загрузился — проверь имя набора выше."));
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
  if (!(await confirmBox(`Вернуть ${p.amount} ⭐ пользователю ${p.user_id}? Подпись вернётся в название его набора.`))) return;
  state.admin = await withBusy("Возвращаю звёзды…", () => api("POST", "/api/admin/refund", { id: p.id }));
  toast("Звёзды возвращены");
  renderAdmin();
}

async function saveLook(body, text) {
  state.admin = await withBusy(text, () => api("PUT", "/api/admin/settings", body));
  await loadIcons();
  renderAdmin();
}
$("saveIconPack").addEventListener("click", () => saveLook({ icon_pack: $("iconPack").value }, "Загружаю набор эмодзи…").then(() => toast("Применено")).catch(() => {}));
$("botCustomEmoji").addEventListener("change", (e) => saveLook({ bot_custom_emoji: e.target.checked }, "").catch(() => {}));

function pickIcon(slot) {
  const map = { ...state.admin.settings.icon_map };
  const choose = async (id) => {
    if (id) map[slot] = id; else delete map[slot];
    closeSheet();
    await saveLook({ icon_map: map }, "Сохраняю…").catch(() => {});
  };
  const all = state.icons.all || [];
  openSheet(
    h("h3", {}, `Иконка: ${state.icons.slots[slot].label}`),
    h("div", { class: "icon-grid" },
      h("div", { class: "tile auto", onclick: () => choose(null) }, "Авто"),
      h("div", { class: "tile", onclick: () => choose("none") }, h("span", { style: "font-size:26px" }, state.icons.slots[slot].fallback)),
      all.map((i) => h("div", { class: "tile" + (map[slot] === i.id ? " selected" : ""), onclick: () => choose(i.id) }, media(i)))),
  );
}

// ---------- старт ----------
if (!tg?.initData) {
  document.body.replaceChildren(h("div", { class: "view" },
    h("h1", {}, "Открой через бота"),
    h("p", { class: "muted" }, "Эта страница работает только как мини-апп внутри Telegram.")));
} else {
  fillIcons();
  withBusy("Загружаю…", () => Promise.all([loadIcons(), loadHome()]))
    .then(() => { if (location.hash === "#admin" && state.me.is_admin) openAdmin(); })
    .catch(() => {});
}
