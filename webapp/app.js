"use strict";

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
try { tg?.disableVerticalSwipes?.(); } catch (_) {}

const $ = (id) => document.getElementById(id);
const state = { me: null, pack: null, inboxSelect: false, inboxSelected: new Set() };

// ---------- helpers ----------
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c);
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
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2600);
}

function confirmBox(text) {
  return new Promise((resolve) => {
    if (tg?.showConfirm && tg.isVersionAtLeast?.("6.2")) tg.showConfirm(text, resolve);
    else resolve(window.confirm(text));
  });
}

function haptic(kind = "light") { tg?.HapticFeedback?.impactOccurred(kind); }

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

async function loadLottie(el) {
  try {
    if (!window.lottie || typeof DecompressionStream !== "function") throw new Error("no lottie");
    const res = await fetch(el.dataset.src);
    const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
    const json = JSON.parse(await new Response(stream).text());
    el.innerHTML = "";
    lottie.loadAnimation({ container: el, renderer: "svg", loop: true, autoplay: true, animationData: json });
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

// ---------- sheet ----------
function openSheet(...content) {
  $("sheetBody").replaceChildren(...content.flat(Infinity).filter((c) => c != null));
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

// ---------- home ----------
async function loadHome() {
  state.me = await api("GET", "/api/me");
  renderHome();
}

function renderHome() {
  const { packs, inbox, pending_title } = state.me;
  const pending = $("pending");
  if (pending_title) {
    pending.textContent = `Бот ждёт первый стикер для набора «${pending_title}».`;
    pending.classList.remove("hidden");
  } else pending.classList.add("hidden");

  $("packs").replaceChildren(...(packs.length ? packs.map(packCard) : [
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
      h("div", { class: "muted small", style: "margin:0" },
        `${p.count} ${plural(p.count, "стикер", "стикера", "стикеров")}`)),
    p.active ? h("span", { class: "badge" }, "активный") : null);
}

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
  const packs = state.me.packs;
  openSheet(
    h("h3", {}, `Куда добавить ${ids.length} шт.?`),
    h("div", { class: "list" },
      packs.map((p) => packCard(p, async () => {
        closeSheet();
        await withBusy("Добавляю стикеры…", () => api("POST", `/api/packs/${p.name}/stickers`, { inbox_ids: ids }));
        state.inboxSelected.clear();
        state.inboxSelect = false;
        tg?.HapticFeedback?.notificationOccurred("success");
        toast("Добавлено");
        await loadHome();
      }))),
    h("button", { class: "btn primary wide", onclick: () => newPackSheet(new Set(ids)) }, "＋ Новый набор из них"),
  );
});

$("newPackBtn").addEventListener("click", () => newPackSheet(new Set()));

function newPackSheet(selected) {
  const inbox = state.me.inbox;
  const title = h("input", { class: "field text", placeholder: "Название набора", maxlength: "64" });
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
    tg?.HapticFeedback?.notificationOccurred("success");
    showPack(pack);
  });
  refresh();
  openSheet(
    h("h3", {}, "Новый набор"),
    title,
    inbox.length
      ? [h("p", { class: "muted small" }, "Выбери стикеры из входящих (нужен хотя бы один):"),
         inboxPicker(inbox, selected, refresh), create]
      : h("p", { class: "muted" },
          "Входящие пустые. Кинь боту стикеры без активного набора (или /new Название прямо в боте) — и они появятся тут."),
  );
  title.focus();
}

// ---------- pack ----------
let sortable = null;
let dragging = false;

async function openPack(name) {
  const pack = await withBusy("Загружаю…", () => api("GET", `/api/packs/${name}`));
  showPack(pack);
}

function showPack(pack) {
  state.pack = pack;
  $("home").classList.add("hidden");
  $("pack").classList.remove("hidden");
  tg?.BackButton?.show();
  window.scrollTo(0, 0);
  renderPack();
}

async function goHome() {
  closeSheet();
  state.pack = null;
  $("pack").classList.add("hidden");
  $("home").classList.remove("hidden");
  tg?.BackButton?.hide();
  await withBusy("", loadHome).catch(() => {});
}
tg?.BackButton?.onClick(goHome);

function renderPack() {
  const p = state.pack;
  $("packTitle").value = p.title;
  $("packCount").textContent = `${p.count} ${plural(p.count, "стикер", "стикера", "стикеров")} из 120`;
  $("packActive").checked = p.active;
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
  const save = h("button", { class: "btn primary" }, "Сохранить");
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

  const del = h("button", { class: "btn danger" }, "Удалить");
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
  const go = h("button", { class: "btn primary wide" }, `Загрузить (${files.length})`);
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

// ---------- start ----------
if (!tg?.initData) {
  document.body.replaceChildren(h("div", { class: "view" },
    h("h1", {}, "Открой через бота"),
    h("p", { class: "muted" }, "Эта страница работает только как мини-апп внутри Telegram.")));
} else {
  withBusy("Загружаю…", loadHome).catch(() => {});
}
