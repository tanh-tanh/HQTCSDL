import { CanvasTexture, LinearFilter, LinearMipmapLinearFilter, NoColorSpace, Texture } from "three";

export interface ImageEntry {
  texture: Texture;
  aspect: number;
}

interface Slot {
  url: string;
  priority: number;
  state: "queued" | "loading" | "ready" | "missing";
  entry: ImageEntry | null;
  waiters: ((entry: ImageEntry | null) => void)[];
  lastUsed: number;
  refs: number;
}

/**
 * Keyframe textures: fetched with the session cookie (the /img route checks
 * search.run), decoded off the main thread with createImageBitmap, loaded in
 * priority order a few at a time, and disposed when no card uses them.
 */
export class ImageLoader {
  private slots = new Map<string, Slot>();
  private active = 0;
  private clock = 0;

  constructor(private maxAnisotropy = 4, private concurrency = 6, private capacity = 160) {}

  /** Ask for a texture; higher priority loads first.  Re-calling updates priority. */
  want(url: string, priority: number, onReady?: (entry: ImageEntry | null) => void): void {
    let slot = this.slots.get(url);
    if (!slot) {
      slot = { url, priority, state: "queued", entry: null, waiters: [], lastUsed: 0, refs: 0 };
      this.slots.set(url, slot);
    }
    slot.priority = Math.max(slot.priority, priority);
    slot.lastUsed = ++this.clock;
    if (onReady) {
      if (slot.state === "ready" || slot.state === "missing") onReady(slot.entry);
      else slot.waiters.push(onReady);
    }
    this.pump();
  }

  retain(url: string): void {
    const slot = this.slots.get(url);
    if (slot) slot.refs++;
  }

  release(url: string): void {
    const slot = this.slots.get(url);
    if (slot) slot.refs = Math.max(0, slot.refs - 1);
    this.evict();
  }

  isSettled(url: string): boolean {
    const state = this.slots.get(url)?.state;
    return state === "ready" || state === "missing";
  }

  private pump(): void {
    while (this.active < this.concurrency) {
      let best: Slot | null = null;
      for (const slot of this.slots.values()) {
        if (slot.state === "queued" && (!best || slot.priority > best.priority)) best = slot;
      }
      if (!best) return;
      this.load(best);
    }
  }

  private async load(slot: Slot): Promise<void> {
    slot.state = "loading";
    this.active++;
    let entry: ImageEntry | null = null;
    try {
      const response = await fetch(slot.url, { credentials: "same-origin" });
      const type = response.headers.get("content-type") ?? "";
      // /img falls back to missing.svg when the keyframe file is not on disk.
      if (response.ok && type.startsWith("image/") && !type.includes("svg")) {
        const blob = await response.blob();
        const bitmap = await createImageBitmap(blob, { resizeWidth: 768, resizeQuality: "high" });
        const texture = new Texture(bitmap);
        texture.flipY = false;
        texture.colorSpace = NoColorSpace;
        texture.generateMipmaps = true;
        texture.minFilter = LinearMipmapLinearFilter;
        texture.magFilter = LinearFilter;
        texture.anisotropy = this.maxAnisotropy;
        texture.needsUpdate = true;
        entry = { texture, aspect: bitmap.width / bitmap.height };
      }
    } catch {
      entry = null;
    }
    this.active--;
    slot.entry = entry;
    slot.state = entry ? "ready" : "missing";
    for (const waiter of slot.waiters.splice(0)) waiter(entry);
    this.evict();
    this.pump();
  }

  private evict(): void {
    if (this.slots.size <= this.capacity) return;
    const idle = [...this.slots.values()]
      .filter((s) => s.refs === 0 && (s.state === "ready" || s.state === "missing"))
      .sort((a, b) => a.lastUsed - b.lastUsed);
    for (const slot of idle) {
      if (this.slots.size <= this.capacity) break;
      slot.entry?.texture.dispose();
      const image = slot.entry?.texture.image as ImageBitmap | undefined;
      image?.close?.();
      this.slots.delete(slot.url);
    }
  }
}

/** Small "07:16" timecode labels, drawn once per distinct text. */
export class BadgeFactory {
  private cache = new Map<string, { texture: CanvasTexture; aspect: number }>();

  get(text: string): { texture: CanvasTexture; aspect: number } {
    const cached = this.cache.get(text);
    if (cached) return cached;
    const height = 96;
    const font = `600 52px "JetBrains Mono", ui-monospace, monospace`;
    const probe = document.createElement("canvas").getContext("2d")!;
    probe.font = font;
    const width = Math.ceil(probe.measureText(text).width + 44);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "rgba(12, 12, 12, 0.86)";
    ctx.beginPath();
    ctx.roundRect(0, 0, width, height, 18);
    ctx.fill();
    ctx.fillStyle = "#fbfaf6";
    ctx.font = font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, width / 2, height / 2 + 3);
    const texture = new CanvasTexture(canvas);
    texture.flipY = false;
    texture.colorSpace = NoColorSpace;
    texture.minFilter = LinearMipmapLinearFilter;
    texture.magFilter = LinearFilter;
    const badge = { texture, aspect: width / height };
    this.cache.set(text, badge);
    return badge;
  }

  clear(): void {
    for (const { texture } of this.cache.values()) texture.dispose();
    this.cache.clear();
  }
}
