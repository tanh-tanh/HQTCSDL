import {
  DataTexture, Mesh, PerspectiveCamera, PlaneGeometry, Raycaster, RGBAFormat,
  Scene, ShaderMaterial, Vector2, Vector3, WebGLRenderer,
} from "three";
import type { ArchiveResult, ViewName } from "../types.ts";
import { clamp, easeInOutCubic, easeOutCubic, formatTime, lerp } from "./math.ts";
import { createCardMaterial } from "./material.ts";
import { BadgeFactory, ImageLoader } from "./textures.ts";
import { newCameraPose, newPose } from "./layouts/layout.ts";
import type { CameraPose, Layout, Pose } from "./layouts/layout.ts";
import { RingLayout } from "./layouts/ring.ts";
import { CorridorLayout } from "./layouts/corridor.ts";
import { ConstellationLayout } from "./layouts/constellation.ts";

/** A plate is an empty print (signed-out landing); a result carries a keyframe. */
export type CardItem = { kind: "result"; result: ArchiveResult } | { kind: "plate"; id: string };

const itemKey = (item: CardItem) => (item.kind === "result" ? `k${item.result.keyframe_id}` : item.id);

interface Card {
  key: string;
  item: CardItem;
  index: number;
  mesh: Mesh;
  material: ShaderMaterial;
  url: string | null;
  requested: number;
  hasMap: boolean;
  load: number;
  hover: number;
  focus: number;
  enterAt: number;
  exitAt: number;
  from: Pose | null;
  blendStart: number;
  blendDuration: number;
  pose: Pose;
  dropX: number;
}

export interface EngineEvents {
  open(result: ArchiveResult, index: number): void;
  active(index: number): void;
  progress(value: number): void;
  hover(index: number | null): void;
}

const MARGIN = 0.16; // shadow margin around a card, in card heights
const DRAG_THRESHOLD = 6;
const CLICK_MAX_MS = 650;

export class ArchiveEngine {
  readonly renderer: WebGLRenderer;
  private scene = new Scene();
  private camera = new PerspectiveCamera(30, 1, 0.1, 400);
  private geometry = new PlaneGeometry(1, 1, 24, 1);
  private blank: DataTexture;
  private loader: ImageLoader;
  private badges = new BadgeFactory();
  private layouts: Record<ViewName, Layout> = {
    ring: new RingLayout(),
    corridor: new CorridorLayout(),
    constellation: new ConstellationLayout(),
  };
  private layout: Layout;
  private cards: Card[] = [];
  private exiting: Card[] = [];
  private pending: { items: CardItem[]; since: number; reset: boolean } | null = null;
  private camPose = newCameraPose();
  private camFrom: CameraPose | null = null;
  private camBlendStart = 0;
  private camBlendDuration = 1;
  private time = 0;
  private last = 0;
  private frame = 0;
  private running = false;
  private reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  private pointer = { down: false, id: -1, x: 0, y: 0, startX: 0, startY: 0, t: 0, startT: 0, dragging: false, inside: false };
  private hoverIndex: number | null = null;
  private focusIndex: number | null = null;
  private lastActive = -2;
  private lastProgress = -1;
  private raycaster = new Raycaster();
  private ndc = new Vector2();
  private tmp = newPose();
  private dpr = 1;
  private maxDpr: number;
  private frameTimes: number[] = [];
  private lastDprChange = 0;
  private width = 1;
  private height = 1;

  constructor(private host: HTMLElement, view: ViewName, private events: EngineEvents) {
    this.renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    this.renderer.setClearColor(0x000000, 0);
    this.maxDpr = Math.min(window.devicePixelRatio || 1, 2);
    this.dpr = this.maxDpr;
    this.renderer.setPixelRatio(this.dpr);
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.setAttribute("aria-hidden", "true");
    this.blank = new DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1, RGBAFormat);
    this.blank.needsUpdate = true;
    this.loader = new ImageLoader(Math.min(8, this.renderer.capabilities.getMaxAnisotropy()));
    this.layout = this.layouts[view];
    this.resize();
    this.layout.camera(this.camPose);
    this.bind();
  }

  get view(): ViewName {
    return this.layout.name;
  }

  private get reducedMotion(): boolean {
    return this.reduced.matches;
  }

  // ------------------------------------------------------------------
  // Items
  // ------------------------------------------------------------------

  /**
   * Replace the displayed set.  The current prints stay until the first new
   * images are decoded (max ~0.8 s), then leave while the new ones arrive.
   * A keyframe present in both sets keeps its print and glides to its new place.
   */
  setItems(items: CardItem[], reset = true): void {
    this.pending = { items, since: this.time, reset };
    // Start decoding the first prints right away.
    items.slice(0, 14).forEach((item, i) => {
      if (item.kind === "result") this.loader.want(item.result.image_url, 1000 - i);
    });
  }

  private commitPending(): void {
    const pending = this.pending;
    if (!pending) return;
    const ready = pending.items.slice(0, 10).every((item) =>
      item.kind !== "result" || this.loader.isSettled(item.result.image_url));
    if (!ready && this.time - pending.since < 0.8) return;
    this.pending = null;
    const previous = new Map(this.cards.map((card) => [card.key, card]));
    const next: Card[] = [];
    const hadCards = this.cards.length > 0;
    const exitDuration = this.reducedMotion ? 0.15 : 0.45;
    pending.items.forEach((item, index) => {
      const key = itemKey(item);
      const kept = previous.get(key);
      if (kept) {
        previous.delete(key);
        kept.index = index;
        kept.item = item;
        kept.from = { ...kept.pose, pos: kept.pose.pos.clone(), quat: kept.pose.quat.clone() };
        kept.blendStart = this.time;
        kept.blendDuration = this.reducedMotion ? 0.2 : 0.9;
        next.push(kept);
      } else {
        const stagger = this.reducedMotion ? 0 : Math.min(index * 0.022, 0.6);
        next.push(this.createCard(item, index, this.time + (hadCards ? exitDuration * 0.55 : 0.05) + stagger));
      }
    });
    for (const card of previous.values()) {
      card.exitAt = this.time;
      card.dropX = card.pose.pos.x;
      this.exiting.push(card);
    }
    this.cards = next;
    this.layout.setCount(next.length, pending.reset);
    this.hoverIndex = null;
    this.focusIndex = null;
    this.lastActive = -2;
    this.resizeLayouts();
  }

  private createCard(item: CardItem, index: number, enterAt: number): Card {
    const material = createCardMaterial(this.blank);
    const mesh = new Mesh(this.geometry, material);
    mesh.frustumCulled = true;
    mesh.visible = false;
    this.scene.add(mesh);
    const card: Card = {
      key: itemKey(item), item, index, mesh, material,
      url: item.kind === "result" ? item.result.image_url : null,
      requested: -1, hasMap: false, load: 0, hover: 0, focus: 0,
      enterAt, exitAt: -1, from: null, blendStart: 0, blendDuration: 1,
      pose: newPose(), dropX: 0,
    };
    mesh.userData.card = card;
    const u = material.uniforms;
    if (item.kind === "result") {
      const badge = this.badges.get(formatTime(item.result.pts_time));
      u.uBadge.value = badge.texture;
      u.uBadgeAspect.value = badge.aspect;
      u.uHasBadge.value = 1;
      u.uDecor.value = 1;
      this.loader.retain(card.url!);
    } else {
      u.uDecor.value = 0;
    }
    return card;
  }

  private disposeCard(card: Card): void {
    this.scene.remove(card.mesh);
    card.material.dispose();
    if (card.url) this.loader.release(card.url);
  }

  // ------------------------------------------------------------------
  // Views
  // ------------------------------------------------------------------

  setView(view: ViewName): void {
    if (view === this.layout.name) return;
    const duration = this.reducedMotion ? 0.35 : 1.25;
    for (const card of this.cards) {
      card.from = { ...card.pose, pos: card.pose.pos.clone(), quat: card.pose.quat.clone() };
      card.blendStart = this.time;
      card.blendDuration = duration;
    }
    this.camFrom = { pos: this.camPose.pos.clone(), target: this.camPose.target.clone(), fov: this.camPose.fov };
    this.camBlendStart = this.time;
    this.camBlendDuration = duration;
    const previousActive = this.layout.active();
    this.layout = this.layouts[view];
    this.layout.setCount(this.cards.length, false);
    this.resizeLayouts();
    // Arrive on the print that was in front in the previous view.
    if (previousActive >= 0) this.layout.focus(previousActive);
    this.lastActive = -2;
  }

  focus(index: number | null): void {
    this.focusIndex = index;
    if (index !== null && index >= 0 && index < this.cards.length) this.layout.focus(index);
  }

  key(code: string): boolean {
    return this.layout.key(code);
  }

  // ------------------------------------------------------------------
  // Loop
  // ------------------------------------------------------------------

  start(): void {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    const tick = (now: number) => {
      if (!this.running) return;
      const dt = Math.min(0.05, Math.max(0, (now - this.last) / 1000));
      this.last = now;
      this.step(dt);
      this.adaptResolution(dt);
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  stop(): void {
    this.running = false;
    cancelAnimationFrame(this.frame);
  }

  private step(dt: number): void {
    this.time += dt;
    this.commitPending();
    this.layout.update(dt, this.reducedMotion);
    this.updateCamera();
    this.pick();
    const visible: Card[] = [];
    for (const card of this.cards) {
      this.updateCard(card, dt);
      if (card.mesh.visible) visible.push(card);
    }
    this.exiting = this.exiting.filter((card) => {
      const done = this.updateExit(card);
      if (done) this.disposeCard(card);
      return !done;
    });
    if (this.time - Math.floor(this.time / 0.25) * 0.25 < dt) this.requestTextures();
    this.renderer.render(this.scene, this.camera);
    this.emit();
  }

  private updateCamera(): void {
    const target = newCameraPose();
    this.layout.camera(target);
    if (this.camFrom) {
      const t = clamp((this.time - this.camBlendStart) / this.camBlendDuration, 0, 1);
      const e = easeInOutCubic(t);
      this.camPose.pos.lerpVectors(this.camFrom.pos, target.pos, e);
      this.camPose.target.lerpVectors(this.camFrom.target, target.target, e);
      this.camPose.fov = lerp(this.camFrom.fov, target.fov, e);
      if (t >= 1) this.camFrom = null;
    } else {
      this.camPose.pos.copy(target.pos);
      this.camPose.target.copy(target.target);
      this.camPose.fov = target.fov;
    }
    this.camera.position.copy(this.camPose.pos);
    this.camera.lookAt(this.camPose.target);
    if (this.camera.fov !== this.camPose.fov) {
      this.camera.fov = this.camPose.fov;
      this.camera.updateProjectionMatrix();
    }
  }

  private updateCard(card: Card, dt: number): void {
    const out = card.pose;
    const tmp = this.tmp;
    tmp.opacity = 1;
    this.layout.pose(card.index, tmp, this.camPose);
    if (card.from) {
      const t = clamp((this.time - card.blendStart) / card.blendDuration, 0, 1);
      const e = easeInOutCubic(t);
      out.pos.lerpVectors(card.from.pos, tmp.pos, e);
      out.quat.slerpQuaternions(card.from.quat, tmp.quat, e);
      out.w = lerp(card.from.w, tmp.w, e);
      out.h = lerp(card.from.h, tmp.h, e);
      out.bend = e < 0.5 ? card.from.bend : tmp.bend;
      out.opacity = lerp(card.from.opacity, tmp.opacity, e);
      if (t >= 1) card.from = null;
    } else {
      out.pos.copy(tmp.pos);
      out.quat.copy(tmp.quat);
      out.w = tmp.w;
      out.h = tmp.h;
      out.bend = tmp.bend;
      out.opacity = tmp.opacity;
    }
    // Entry: rise and fade in, staggered by rank.
    const enter = this.reducedMotion
      ? clamp((this.time - card.enterAt) / 0.2, 0, 1)
      : easeOutCubic(clamp((this.time - card.enterAt) / 0.75, 0, 1));
    const target = { hover: this.hoverIndex === card.index ? 1 : 0, focus: this.focusIndex === card.index ? 1 : 0 };
    card.hover = lerp(card.hover, target.hover, 1 - Math.exp(-dt * 12));
    card.focus = lerp(card.focus, target.focus, 1 - Math.exp(-dt * 12));
    if (card.hasMap && card.load < 1) card.load = Math.min(1, card.load + dt / 0.35);
    this.apply(card, out, enter, 0);
  }

  private updateExit(card: Card): boolean {
    const t = clamp((this.time - card.exitAt) / (this.reducedMotion ? 0.15 : 0.45), 0, 1);
    this.apply(card, card.pose, 1, easeInOutCubic(t));
    return t >= 1;
  }

  private apply(card: Card, pose: Pose, enter: number, exit: number): void {
    const mesh = card.mesh;
    const opacity = pose.opacity * enter * (1 - exit);
    mesh.visible = opacity > 0.004;
    if (!mesh.visible) return;
    const scale = (0.92 + 0.08 * enter) * (1 - 0.08 * exit) * (1 + 0.035 * card.hover);
    const w = pose.w * scale;
    const h = pose.h * scale;
    const margin = MARGIN * h;
    mesh.position.copy(pose.pos);
    mesh.quaternion.copy(pose.quat);
    const normal = new Vector3(0, 0, 1).applyQuaternion(pose.quat);
    mesh.position.addScaledVector(normal, this.layout.hoverLift * card.hover - exit * 0.6);
    mesh.position.y -= (1 - enter) * 0.7;
    mesh.scale.set(w + 2 * margin, h + 2 * margin, 1);
    const u = card.material.uniforms;
    u.uCard.value.set(w, h);
    u.uPlane.value.set(w + 2 * margin, h + 2 * margin);
    u.uBend.value = pose.bend;
    u.uOpacity.value = opacity;
    u.uHover.value = card.hover;
    u.uFocus.value = card.focus;
    u.uLoad.value = card.load;
  }

  private requestTextures(): void {
    const camPos = this.camera.position;
    const active = this.layout.active();
    for (const card of this.cards) {
      if (!card.url || card.hasMap || card.requested === 1) continue;
      const near = active >= 0 && Math.abs(card.index - active) <= 12;
      if (!card.mesh.visible && !near) continue;
      const dist = Math.max(0.5, card.pose.pos.distanceTo(camPos));
      const priority = (card.mesh.visible ? 100 : 0) + (card.pose.h / dist) * 100 - card.index * 0.01;
      const url = card.url;
      if (card.requested === 0) {
        this.loader.want(url, priority);
        continue;
      }
      card.requested = 0;
      this.loader.want(url, priority, (entry) => {
        if (card.url !== url) return;
        if (entry) {
          card.material.uniforms.uMap.value = entry.texture;
          card.material.uniforms.uTexAspect.value = entry.aspect;
          card.material.uniforms.uHasMap.value = 1;
          card.hasMap = true;
        }
        card.requested = 1;
      });
    }
  }

  private emit(): void {
    const active = this.cards.length ? this.layout.active() : -1;
    if (active !== this.lastActive) {
      this.lastActive = active;
      this.events.active(active);
    }
    const progress = this.layout.progress();
    if (Math.abs(progress - this.lastProgress) > 0.0005) {
      this.lastProgress = progress;
      this.events.progress(progress);
    }
  }

  /** Lower the render resolution on slow machines, raise it again when there is headroom. */
  private adaptResolution(dt: number): void {
    this.frameTimes.push(dt);
    if (this.frameTimes.length < 90 || this.time - this.lastDprChange < 2) return;
    const avg = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;
    this.frameTimes.length = 0;
    let next = this.dpr;
    if (avg > 1 / 40 && this.dpr > 1) next = Math.max(1, this.dpr - 0.25);
    else if (avg < 1 / 58 && this.dpr < this.maxDpr) next = Math.min(this.maxDpr, this.dpr + 0.25);
    if (next !== this.dpr) {
      this.dpr = next;
      this.renderer.setPixelRatio(next);
      this.renderer.setSize(this.width, this.height, false);
      this.lastDprChange = this.time;
    }
  }

  // ------------------------------------------------------------------
  // Input
  // ------------------------------------------------------------------

  private bind(): void {
    const el = this.renderer.domElement;
    el.style.touchAction = "none";
    el.addEventListener("pointerdown", this.onDown);
    el.addEventListener("pointermove", this.onMove);
    el.addEventListener("pointerup", this.onUp);
    el.addEventListener("pointercancel", this.onCancel);
    el.addEventListener("pointerleave", () => { this.pointer.inside = false; });
    el.addEventListener("wheel", this.onWheel, { passive: false });
    window.addEventListener("resize", this.onResize);
    document.addEventListener("visibilitychange", this.onVisibility);
  }

  private onDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    const p = this.pointer;
    p.down = true;
    p.id = e.pointerId;
    p.x = p.startX = e.clientX;
    p.y = p.startY = e.clientY;
    p.t = p.startT = performance.now();
    p.dragging = false;
    this.renderer.domElement.setPointerCapture(e.pointerId);
  };

  private onMove = (e: PointerEvent) => {
    const p = this.pointer;
    p.inside = true;
    if (!p.down || e.pointerId !== p.id) {
      p.x = e.clientX;
      p.y = e.clientY;
      return;
    }
    const now = performance.now();
    if (!p.dragging && Math.hypot(e.clientX - p.startX, e.clientY - p.startY) > DRAG_THRESHOLD) {
      p.dragging = true;
      document.body.classList.add("is-dragging");
      this.layout.dragStart();
    }
    if (p.dragging) this.layout.drag(e.clientX - p.x, e.clientY - p.y, Math.max(0.001, (now - p.t) / 1000));
    p.x = e.clientX;
    p.y = e.clientY;
    p.t = now;
  };

  private onUp = (e: PointerEvent) => {
    const p = this.pointer;
    if (!p.down || e.pointerId !== p.id) return;
    p.down = false;
    if (p.dragging) {
      p.dragging = false;
      document.body.classList.remove("is-dragging");
      this.layout.dragEnd();
      return;
    }
    if (performance.now() - p.startT > CLICK_MAX_MS) return;
    const index = this.hitTest(e.clientX, e.clientY);
    const card = index === null ? null : this.cards[index];
    if (card && card.item.kind === "result") {
      this.focusIndex = index;
      this.events.open(card.item.result, index!);
    }
  };

  private onCancel = () => {
    const p = this.pointer;
    if (p.dragging) this.layout.dragEnd();
    p.down = p.dragging = false;
    document.body.classList.remove("is-dragging");
  };

  private onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const scale = e.deltaMode === 1 ? 32 : e.deltaMode === 2 ? this.height : 1;
    this.layout.wheel(e.deltaX * scale, e.deltaY * scale);
  };

  private onResize = () => this.resize();

  private onVisibility = () => {
    // Nothing to animate while the tab is hidden.
    if (document.hidden) this.stop();
    else this.start();
  };

  private hitTest(x: number, y: number): number | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.ndc.set(((x - rect.left) / rect.width) * 2 - 1, -((y - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(this.ndc, this.camera);
    const meshes = this.cards.filter((c) => c.mesh.visible && c.material.uniforms.uOpacity.value > 0.3).map((c) => c.mesh);
    for (const hit of this.raycaster.intersectObjects(meshes, false)) {
      const card = hit.object.userData.card as Card;
      if (!hit.uv) continue;
      const plane = card.material.uniforms.uPlane.value as Vector2;
      const size = card.material.uniforms.uCard.value as Vector2;
      const px = Math.abs((hit.uv.x - 0.5) * plane.x);
      const py = Math.abs((hit.uv.y - 0.5) * plane.y);
      if (px <= size.x / 2 && py <= size.y / 2) return card.index;
    }
    return null;
  }

  private pick(): void {
    const p = this.pointer;
    let index: number | null = null;
    if (p.inside && !p.dragging) index = this.hitTest(p.x, p.y);
    if (index !== null && this.cards[index]?.item.kind !== "result") index = null;
    if (index !== this.hoverIndex) {
      this.hoverIndex = index;
      this.renderer.domElement.style.cursor = index !== null ? "pointer" : "grab";
      this.events.hover(index);
    }
  }

  resize(): void {
    const rect = this.host.getBoundingClientRect();
    this.width = Math.max(1, Math.round(rect.width));
    this.height = Math.max(1, Math.round(rect.height));
    this.renderer.setSize(this.width, this.height, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
    this.resizeLayouts();
  }

  private resizeLayouts(): void {
    for (const layout of Object.values(this.layouts)) layout.resize({ width: this.width, height: this.height });
  }

  /** Debug/QA hook (used by the Playwright checks). */
  debugState() {
    const layout = this.layout as unknown as Record<string, unknown>;
    return {
      view: this.layout.name,
      count: this.cards.length,
      exiting: this.exiting.length,
      pending: !!this.pending,
      active: this.layout.active(),
      progress: this.layout.progress(),
      u: layout.u, velocity: layout.velocity, travel: layout.travel, yaw: layout.yaw,
      keys: this.cards.slice(0, 60).map((c) => c.key),
      loaded: this.cards.filter((c) => c.hasMap).length,
      dpr: this.dpr,
    };
  }

  /** Project a card's centre to screen pixels (for tests and the accessible list). */
  screenPosition(index: number): { x: number; y: number; visible: boolean } | null {
    const card = this.cards[index];
    if (!card) return null;
    const v = card.mesh.position.clone().project(this.camera);
    return { x: ((v.x + 1) / 2) * this.width, y: ((1 - v.y) / 2) * this.height, visible: card.mesh.visible };
  }

  dispose(): void {
    this.stop();
    for (const card of [...this.cards, ...this.exiting]) this.disposeCard(card);
    this.cards = [];
    this.exiting = [];
    this.badges.clear();
    this.geometry.dispose();
    this.blank.dispose();
    window.removeEventListener("resize", this.onResize);
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}

export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

