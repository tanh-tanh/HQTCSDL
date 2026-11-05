import "./styles.css";
import { ArchiveEngine, webglAvailable } from "./engine/engine.ts";
import type { CardItem } from "./engine/engine.ts";
import { formatTime } from "./engine/math.ts";
import { ALL_SOURCES, Store, pageUrl, sameSearch, stateFromUrl } from "./store.ts";
import type { State } from "./store.ts";
import type { ArchiveResult, Boot, ViewName } from "./types.ts";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (text: string) => text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const ICONS = {
  sources: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h10M4 18h13"/><circle cx="19" cy="12" r="1.6"/></svg>',
  topic: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.4 1.4 0 0 1 0 2l-6.7 6.7a1.4 1.4 0 0 1-2 0z"/><circle cx="8" cy="8" r="1.4"/></svg>',
  collection: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l9 4.5-9 4.5-9-4.5z"/><path d="M3 12l9 4.5 9-4.5"/><path d="M3 16.5L12 21l9-4.5"/></svg>',
  caret: '<svg class="caret" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5L6 8l3.5-3.5"/></svg>',
};

const HINTS: Record<ViewName, [string, string, string]> = {
  ring: ["Drag to spin", "Click a result to open", "Scroll to explore ↓"],
  corridor: ["Scroll to travel", "Click a result to open", "Drag to move ↓"],
  constellation: ["Drag to orbit", "Click a result to open", "Scroll to zoom"],
};

function boot(): void {
  const raw = document.getElementById("archive-boot")?.textContent;
  if (!raw) return;
  const data = JSON.parse(raw) as Boot;
  const store = new Store(data);
  const anonymous = !data.user;
  const body = document.body;
  const input = $<HTMLInputElement>("search-input");
  const form = $<HTMLFormElement>("search");
  const meta = $<HTMLSpanElement>("search-meta");
  const message = $<HTMLDivElement>("archive-message");
  const list = $<HTMLOListElement>("result-list");
  const nvTitle = $<HTMLParagraphElement>("nv-title");
  const nvMeta = $<HTMLDListElement>("nv-meta");
  const nowViewing = $<HTMLElement>("now-viewing");
  const progressBar = $<HTMLDivElement>("progress");
  const progressFill = $<HTMLSpanElement>("progress-fill");
  const hints = $<HTMLDivElement>("hints");

  // ---------------------------------------------------------------- engine
  let engine: ArchiveEngine | null = null;
  if (webglAvailable()) {
    try {
      engine = new ArchiveEngine($("stage"), store.state.view, {
        open: (result) => openResult(result),
        active: (index) => showActive(index),
        progress: (value) => {
          progressFill.style.width = `${(value * 100).toFixed(2)}%`;
          progressBar.setAttribute("aria-valuenow", String(Math.round(value * 100)));
        },
        hover: () => undefined,
      });
    } catch (error) {
      console.warn("WebGL unavailable, using the list view", error);
      engine = null;
    }
  }
  if (!engine) body.classList.add("no-webgl");
  (window as unknown as { __archive: unknown }).__archive = { store, engine };

  const results = (): ArchiveResult[] => store.state.payload?.results ?? [];

  function items(): CardItem[] {
    if (anonymous) return Array.from({ length: 26 }, (_, i) => ({ kind: "plate" as const, id: `plate-${i}` }));
    return results().map((result) => ({ kind: "result" as const, result }));
  }

  function detailHref(result: ArchiveResult): string {
    const s = store.state;
    const p = new URLSearchParams();
    if (s.requestId) p.set("request_id", s.requestId);
    if (s.query.trim()) p.set("q", s.query.trim());
    p.set("view", s.view);
    return `${result.detail_url}?${p}`;
  }

  function openResult(result: ArchiveResult): void {
    // Remember where we were so "back" returns to the same state.
    history.replaceState({ archive: true }, "", pageUrl(store.state));
    window.setTimeout(() => { window.location.href = detailHref(result); }, 140);
  }

  // ------------------------------------------------------------ header nav
  for (const link of document.querySelectorAll<HTMLAnchorElement>(".view-link")) {
    link.addEventListener("click", (event) => {
      const view = link.dataset.view as ViewName;
      if (event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      setView(view, true);
    });
  }

  function setView(view: ViewName, push: boolean): void {
    if (view === store.state.view) return;
    store.set({ view });
    if (push) history.pushState({ archive: true }, "", pageUrl(store.state));
  }

  function renderView(view: ViewName): void {
    body.classList.remove("view-ring", "view-corridor", "view-constellation");
    body.classList.add(`view-${view}`);
    for (const link of document.querySelectorAll<HTMLAnchorElement>(".view-link")) {
      const active = link.dataset.view === view;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
      const p = new URLSearchParams();
      p.set("view", link.dataset.view!);
      if (store.state.query.trim()) p.set("q", store.state.query.trim());
      link.href = `/?${p}`;
    }
    const [a, b, c] = HINTS[view];
    hints.querySelector(".hint-a")!.textContent = a;
    hints.querySelector(".hint-b")!.textContent = anonymous ? "Đăng nhập để mở tư liệu" : b;
    hints.querySelector(".hint-c")!.textContent = c;
    (form.elements.namedItem("view") as HTMLInputElement).value = view;
    engine?.setView(view);
  }

  // ------------------------------------------------------------ search box
  let debounce = 0;
  input.addEventListener("input", () => {
    window.clearTimeout(debounce);
    if (anonymous) return;
    debounce = window.setTimeout(() => runSearch(input.value, false), 850);
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    window.clearTimeout(debounce);
    if (anonymous) {
      const next = `/?q=${encodeURIComponent(input.value.trim())}`;
      window.location.href = `/login?next=${encodeURIComponent(next)}`;
      return;
    }
    runSearch(input.value, true);
  });

  function runSearch(text: string, push: boolean, extra: Partial<State> = {}): void {
    const query = text.trim();
    store.set({ query, ...extra });
    if (push) history.pushState({ archive: true }, "", pageUrl(store.state));
    else history.replaceState({ archive: true }, "", pageUrl(store.state));
    void store.search();
  }

  // ---------------------------------------------------------------- filters
  const filters = $<HTMLDivElement>("filters");
  const optionsToggle = $<HTMLButtonElement>("options-toggle");
  const optionsPanel = $<HTMLDivElement>("options-panel");
  const popovers: { button: HTMLButtonElement; panel: HTMLElement }[] = [];

  function pill(name: string, icon: string, label: string): { wrap: HTMLDivElement; button: HTMLButtonElement; panel: HTMLDivElement } {
    const wrap = document.createElement("div");
    wrap.className = "pill-wrap";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pill";
    button.dataset.filter = name;
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-haspopup", "true");
    button.innerHTML = `${icon}<span class="pill-label">${esc(label)}</span><span class="pill-count"></span>${ICONS.caret}`;
    const panel = document.createElement("div");
    panel.className = "popover";
    panel.hidden = true;
    panel.id = `filter-${name}`;
    button.setAttribute("aria-controls", panel.id);
    wrap.append(button, panel);
    filters.append(wrap);
    popovers.push({ button, panel });
    button.addEventListener("click", () => toggle(button, panel));
    if (anonymous) button.disabled = true;
    return { wrap, button, panel };
  }

  function toggle(button: HTMLButtonElement, panel: HTMLElement, force?: boolean): void {
    const open = force ?? panel.hidden;
    for (const other of popovers) {
      if (other.panel !== panel) {
        other.panel.hidden = true;
        other.button.setAttribute("aria-expanded", "false");
      }
    }
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) panel.querySelector<HTMLElement>("input, select, button")?.focus({ preventScroll: true });
  }

  popovers.push({ button: optionsToggle, panel: optionsPanel });
  optionsToggle.addEventListener("click", () => toggle(optionsToggle, optionsPanel));
  document.addEventListener("click", (event) => {
    const target = event.target as Node;
    for (const { button, panel } of popovers) {
      if (!panel.hidden && !panel.contains(target) && !button.contains(target)) toggle(button, panel, false);
    }
    const menu = document.querySelector<HTMLDetailsElement>(".user-menu[open]");
    if (menu && !menu.contains(target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const { button, panel } of popovers) if (!panel.hidden) { toggle(button, panel, false); button.focus(); }
  });

  const sourcesPill = pill("sources", ICONS.sources, "Nguồn");
  const topicPill = pill("topic", ICONS.topic, "Chủ đề");
  const collectionPill = pill("collection", ICONS.collection, "Bộ sưu tập");

  function renderFilterPanels(): void {
    const s = store.state;
    sourcesPill.panel.innerHTML = `<h3>Kênh tìm kiếm</h3>` + data.sources.map((src) => `
      <label class="opt"><input type="checkbox" value="${src.code}" ${s.sources.includes(src.code) ? "checked" : ""}> ${esc(src.label)}</label>`).join("") +
      `<p class="note">Kênh hình ảnh: ${esc(data.visual)}</p>`;
    sourcesPill.panel.querySelectorAll<HTMLInputElement>("input").forEach((box) => box.addEventListener("change", () => {
      const chosen = [...sourcesPill.panel.querySelectorAll<HTMLInputElement>("input:checked")].map((b) => b.value);
      if (!chosen.length) { box.checked = true; return; }
      runSearch(store.state.query, true, { sources: ALL_SOURCES.filter((c) => chosen.includes(c)) });
    }));

    const topics = data.options.topics;
    topicPill.panel.innerHTML = `<h3>Chủ đề</h3><label class="opt"><input type="radio" name="topic" value="" ${s.topic === null ? "checked" : ""}> Tất cả chủ đề</label>` +
      topics.map((t) => `<label class="opt tree-${Math.min(3, t.depth)}"><input type="radio" name="topic" value="${t.id}" ${s.topic === t.id ? "checked" : ""}> ${esc(t.name)}</label>`).join("");
    topicPill.panel.querySelectorAll<HTMLInputElement>("input").forEach((radio) => radio.addEventListener("change", () => {
      runSearch(store.state.query, true, { topic: radio.value ? Number(radio.value) : null });
    }));

    collectionPill.panel.innerHTML = `<h3>Bộ sưu tập</h3><label class="opt"><input type="radio" name="collection" value="" ${s.collection === null ? "checked" : ""}> Tất cả bộ sưu tập</label>` +
      data.options.collections.map((c) => `<label class="opt${c.videos ? "" : " is-disabled"}"><input type="radio" name="collection" value="${c.id}" ${s.collection === c.id ? "checked" : ""}> ${esc(c.title)}<small>${c.videos} video</small></label>`).join("");
    collectionPill.panel.querySelectorAll<HTMLInputElement>("input").forEach((radio) => radio.addEventListener("change", () => {
      runSearch(store.state.query, true, { collection: radio.value ? Number(radio.value) : null });
    }));

    const topicName = topics.find((t) => t.id === s.topic)?.name;
    const collectionName = data.options.collections.find((c) => c.id === s.collection)?.title;
    setPill(sourcesPill.button, "Nguồn", s.sources.length === ALL_SOURCES.length ? "" : `${s.sources.length}/3`);
    setPill(topicPill.button, topicName ?? "Chủ đề", "");
    setPill(collectionPill.button, collectionName ?? "Bộ sưu tập", "");
    sourcesPill.button.classList.toggle("is-set", s.sources.length !== ALL_SOURCES.length);
    topicPill.button.classList.toggle("is-set", s.topic !== null);
    collectionPill.button.classList.toggle("is-set", s.collection !== null);

    optionsPanel.innerHTML = `
      <div class="group"><h3>Số kết quả (top-K)</h3><div class="chips">${data.top_k_choices.map((k) =>
        `<button type="button" class="chip" data-k="${k}" aria-pressed="${s.k === k}">${k}</button>`).join("")}</div></div>
      ${data.requests.length ? `<div class="group"><h3>Yêu cầu khai thác</h3><div style="padding:0 10px 6px">
        <select id="request-select" aria-label="Chọn yêu cầu khai thác"><option value="">— tự nhập mô tả —</option>${data.requests.map((r) =>
          `<option value="${esc(r.id)}" ${r.id === s.requestId ? "selected" : ""}>${esc(r.id)}${r.verified ? " ✓" : ""} · ${esc(r.status)}</option>`).join("")}</select></div>
        <p class="note">Điền mô tả của yêu cầu vào ô tìm kiếm; trang chi tiết sẽ chọn sẵn yêu cầu này.</p></div>` : ""}
      <div class="group"><p class="note">Kênh hình ảnh: ${esc(data.visual)}</p>
        ${data.links.list ? `<p class="note"><a href="/list?${new URLSearchParams(s.query ? { q: s.query } : {})}">Mở dạng danh sách</a></p>` : ""}</div>`;
    optionsPanel.querySelectorAll<HTMLButtonElement>(".chip").forEach((chip) => chip.addEventListener("click", () => {
      runSearch(store.state.query, true, { k: Number(chip.dataset.k) });
      toggle(optionsToggle, optionsPanel, false);
    }));
    optionsPanel.querySelector<HTMLSelectElement>("#request-select")?.addEventListener("change", (event) => {
      const id = (event.target as HTMLSelectElement).value;
      const request = data.requests.find((r) => r.id === id);
      if (request) input.value = request.text;
      runSearch(request ? request.text : store.state.query, true, { requestId: id });
      toggle(optionsToggle, optionsPanel, false);
    });
  }

  function setPill(button: HTMLButtonElement, label: string, count: string): void {
    button.querySelector(".pill-label")!.textContent = label;
    button.querySelector(".pill-count")!.textContent = count;
  }

  // ------------------------------------------------------- status + results
  function renderStatus(): void {
    const s = store.state;
    body.classList.toggle("is-loading", s.status === "loading");
    const payload = s.payload;
    if (s.status === "loading") meta.textContent = "Đang tìm";
    else if (s.status === "anonymous") meta.textContent = "";
    else if (s.status === "error") meta.textContent = "Lỗi";
    else if (payload?.mode === "browse") meta.textContent = `Toàn kho · ${payload.results.length}`;
    else if (payload) meta.textContent = `${payload.results.length} · ${payload.latency_ms ?? 0} ms`;
    if (payload?.notes?.length) meta.title = payload.notes.join("\n");
    else meta.removeAttribute("title");

    let html = "";
    if (s.status === "anonymous") {
      html = `<p>Đăng nhập để tra cứu kho tư liệu.</p><p class="sub"><a href="${esc(data.links.login ?? "/login")}">Đăng nhập</a>${data.links.register ? ` / <a href="${esc(data.links.register)}">Tạo tài khoản</a>` : ""}</p>`;
    } else if (s.status === "error") {
      html = `<p>${esc(s.error ?? "Đã có lỗi.")}</p><p class="sub">Thử lại sau giây lát</p>`;
    } else if (s.status === "empty") {
      html = payload?.mode === "browse"
        ? `<p>Bộ lọc này chưa có video nào.</p><p class="sub">Bỏ bớt bộ lọc để xem toàn kho</p>`
        : `<p>Không tìm thấy khung hình phù hợp.</p><p class="sub">Thử mô tả khác hoặc bỏ bớt bộ lọc</p>`;
    }
    message.innerHTML = html;
  }

  function renderList(): void {
    const withImages = body.classList.contains("no-webgl");
    list.innerHTML = results().map((r, i) => `
      <li><a href="${esc(detailHref(r))}" data-index="${i}">
        ${withImages ? `<img src="${esc(r.image_url)}" loading="lazy" alt="">` : ""}
        <span class="r-rank">#${r.rank}</span>
        <span class="r-title">${esc(r.title ?? r.video_id)}</span>
        <span class="r-meta">${esc(r.video_id)} · ${formatTime(r.pts_time)}${r.evidence ? ` · ${esc(r.evidence)}` : ""}</span>
      </a></li>`).join("");
  }
  // Keyboard users: focusing a result in the list brings its print forward.
  list.addEventListener("focusin", (event) => {
    const index = Number((event.target as HTMLElement).closest("a")?.dataset.index);
    if (Number.isFinite(index)) engine?.focus(index);
  });
  list.addEventListener("focusout", () => engine?.focus(null));

  let lastActive = -1;
  function showActive(index: number): void {
    const result = results()[index];
    if (index === lastActive) return;
    lastActive = index;
    nowViewing.classList.add("is-swapping");
    window.setTimeout(() => {
      if (!result) {
        nvTitle.textContent = anonymous ? "Kho tư liệu" : "—";
        nvMeta.innerHTML = "";
      } else {
        nvTitle.textContent = result.title ?? result.video_id;
        const rows: [string, string | null][] = [
          ["Bộ sưu tập", result.collection],
          ["Chủ đề", result.topics.length ? result.topics.join(", ") : null],
          ["Thời điểm", `${formatTime(result.pts_time)} · ${result.video_id} · #${result.rank}`],
        ];
        nvMeta.innerHTML = rows.filter(([, v]) => v).map(([k, v]) => `<dt>${esc(k)}</dt><dd title="${esc(v!)}">${esc(v!)}</dd>`).join("");
      }
      nowViewing.classList.remove("is-swapping");
    }, 180);
  }

  // ---------------------------------------------------------------- wiring
  store.subscribe((s, prev) => {
    if (s.view !== prev.view) renderView(s.view);
    if (s.generation !== prev.generation) {
      engine?.setItems(items(), true);
      lastActive = -2;
      renderList();
    }
    if (s.status !== prev.status || s.payload !== prev.payload || s.error !== prev.error) renderStatus();
    if (s.sources !== prev.sources || s.topic !== prev.topic || s.collection !== prev.collection ||
        s.k !== prev.k || s.requestId !== prev.requestId) renderFilterPanels();
  });

  window.addEventListener("popstate", () => {
    const next = stateFromUrl(location.search, store.state);
    const searchChanged = !sameSearch(store.key(), {
      query: next.query ?? "", k: next.k ?? 50, sources: next.sources ?? ALL_SOURCES,
      collection: next.collection ?? null, topic: next.topic ?? null,
    });
    input.value = next.query ?? "";
    store.set(next);
    if (searchChanged) void store.search();
  });

  window.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable]")) return;
    if (event.key === "/") {
      event.preventDefault();
      input.focus();
      return;
    }
    if (engine?.key(event.code)) event.preventDefault();
  });

  // ---------------------------------------------------------------- start
  renderView(store.state.view);
  renderFilterPanels();
  renderStatus();
  renderList();
  if (engine) {
    engine.setItems(items(), true);
    engine.start();
  }
  if (!anonymous && !store.state.payload && !store.state.error) void store.search(true);
  if (anonymous) showActive(-1);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
