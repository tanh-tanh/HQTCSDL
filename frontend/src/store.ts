import type { ArchivePayload, Boot, SearchStatus, ViewName } from "./types.ts";

export const ALL_SOURCES = ["visual", "ocr", "asr"];

export interface State {
  view: ViewName;
  query: string;
  k: number;
  sources: string[];
  collection: number | null;
  topic: number | null;
  requestId: string;
  status: SearchStatus;
  payload: ArchivePayload | null;
  error: string | null;
  /** Bumps every time a new result set replaces the old one. */
  generation: number;
}

export type SearchKey = Pick<State, "query" | "k" | "sources" | "collection" | "topic">;

const VIEWS: ViewName[] = ["ring", "corridor", "constellation"];

export function isView(value: string | null): value is ViewName {
  return value !== null && (VIEWS as string[]).includes(value);
}

/** Query string for the archive page and the JSON API (same parameter names). */
export function searchParams(s: SearchKey & { view?: ViewName; requestId?: string }, forPage: boolean): URLSearchParams {
  const p = new URLSearchParams();
  if (s.query.trim()) p.set("q", s.query.trim());
  if (forPage && s.view && s.view !== "ring") p.set("view", s.view);
  if (s.k !== 50) p.set("k", String(s.k));
  const sources = ALL_SOURCES.filter((code) => s.sources.includes(code));
  if (sources.length !== ALL_SOURCES.length) for (const code of sources) p.append("src", code);
  if (s.collection !== null) p.set("collection", String(s.collection));
  if (s.topic !== null) p.set("topic", String(s.topic));
  if (forPage && s.requestId) p.set("request_id", s.requestId);
  return p;
}

export function pageUrl(s: State): string {
  const qs = searchParams(s, true).toString();
  return "/" + (qs ? `?${qs}` : "");
}

export function stateFromUrl(search: string, base: State): Partial<State> {
  const p = new URLSearchParams(search);
  const src = p.getAll("src").filter((code) => ALL_SOURCES.includes(code));
  const id = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);
  const view = p.get("view");
  return {
    view: isView(view) ? view : "ring",
    query: p.get("q") ?? "",
    k: Math.max(1, Math.min(200, Number(p.get("k")) || base.k || 50)),
    sources: src.length ? src : [...ALL_SOURCES],
    collection: id(p.get("collection")),
    topic: id(p.get("topic")),
    requestId: p.get("request_id") ?? "",
  };
}

export function sameSearch(a: SearchKey, b: SearchKey): boolean {
  return a.query.trim() === b.query.trim() && a.k === b.k && a.collection === b.collection &&
    a.topic === b.topic && [...a.sources].sort().join() === [...b.sources].sort().join();
}

type Listener = (state: State, previous: State) => void;

export class Store {
  state: State;
  private listeners: Listener[] = [];
  private controller: AbortController | null = null;
  private sequence = 0;
  private loaded: SearchKey | null = null;

  constructor(boot: Boot) {
    const initial = boot.initial;
    const status: SearchStatus = !boot.user ? "anonymous"
      : boot.error ? "error"
      : !initial ? "idle"
      : initial.results.length ? "success" : "empty";
    this.state = {
      view: boot.view,
      query: boot.query,
      k: boot.k,
      sources: boot.sources.filter((s) => s.checked).map((s) => s.code),
      collection: boot.filters.collection,
      topic: boot.filters.topic,
      requestId: boot.request_id,
      status,
      payload: initial,
      error: boot.error,
      generation: 0,
    };
    if (initial) this.loaded = this.key();
  }

  key(): SearchKey {
    const { query, k, sources, collection, topic } = this.state;
    return { query, k, sources: [...sources], collection, topic };
  }

  subscribe(listener: Listener): void {
    this.listeners.push(listener);
  }

  set(patch: Partial<State>): void {
    const previous = this.state;
    this.state = { ...previous, ...patch };
    for (const listener of this.listeners) listener(this.state, previous);
  }

  /** Fetch results for the current search key; stale responses are dropped. */
  async search(force = false): Promise<void> {
    if (this.state.status === "anonymous") return;
    const key = this.key();
    if (!force && this.loaded && sameSearch(key, this.loaded)) return;
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const sequence = ++this.sequence;
    this.set({ status: "loading", error: null });
    try {
      const response = await fetch(`/api/search?${searchParams(key, false)}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (response.status === 401) {
        window.location.href = `/login?next=${encodeURIComponent(pageUrl(this.state))}`;
        return;
      }
      const body = await response.json().catch(() => null);
      if (sequence !== this.sequence) return;
      if (!response.ok || !body || !Array.isArray(body.results)) {
        this.set({ status: "error", error: body?.message ?? `Lỗi máy chủ (${response.status}).` });
        return;
      }
      this.loaded = key;
      this.set({
        status: body.results.length ? "success" : "empty",
        payload: body as ArchivePayload,
        generation: this.state.generation + 1,
      });
    } catch (error) {
      if ((error as Error).name === "AbortError" || sequence !== this.sequence) return;
      this.set({ status: "error", error: "Không kết nối được máy chủ." });
    }
  }
}
