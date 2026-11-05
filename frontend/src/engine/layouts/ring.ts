import { PerspectiveCamera, Vector3 } from "three";
import { clamp, damp, mod, smoothstep } from "../math.ts";
import { VelocityTracker } from "./layout.ts";
import type { CameraPose, Layout, Pose, Viewport } from "./layout.ts";

// Camera fitted to the approved RING screenshot (1942×809): vertical FOV,
// elevation above the ring plane, distance and look-at height for R = 10.
const R = 10;
const FOV = 28.1;
const ELEVATION = 0.1465;
const DISTANCE = 26.13;
const TARGET_Y = 0.767;
const REF_ASPECT = 1942 / 809;
const REF_EXTENT = 0.3424; // ring half-width / viewport width in the reference
const CARD_ASPECT = 16 / 9;
const UP = new Vector3(0, 1, 0);

type Mode = "window" | "full" | "arc";

/**
 * A real 3D carousel: item i sits at θ = (i − u)·Δ on a circle of radius R,
 * x = R sin θ, z = R cos θ, facing outward and bent along the cylinder.
 * u (in slots) is the rotation; dragging changes u, releasing keeps the
 * angular velocity and lets it decay exponentially (time-based damping).
 *
 * Result counts:
 *   n ≥ slots      window: `slots` consecutive results are on the ring, the
 *                  rest stream in at the rear (fade) — all results reachable.
 *   12 ≤ n < slots full ring, spacing 2π/n, slightly larger prints.
 *   n < 12         a bounded arc starting at the front; no duplicates.
 */
export class RingLayout implements Layout {
  readonly name = "ring" as const;
  readonly hoverLift = 0.45;
  count = 0;
  private slots = 26;
  private mode: Mode = "arc";
  private u = 0;
  private velocity = 0;
  private dragging = false;
  private seek: number | null = null;
  private idle = 0;
  private tracker = new VelocityTracker();
  private pxPerSlot = 60;
  private scale = 1;
  private elevation = ELEVATION;
  private probe = new PerspectiveCamera(FOV, 1, 0.1, 400);

  setCount(n: number, reset: boolean): void {
    this.count = n;
    this.mode = n >= this.slots ? "window" : n >= 12 ? "full" : "arc";
    if (reset) {
      this.u = 0;
      this.velocity = 0;
      this.seek = null;
      this.idle = 0;
    } else if (this.mode === "arc") {
      this.u = clamp(this.u, 0, Math.max(0, n - 1));
    }
  }

  private spacing(): number {
    if (this.mode === "window") return (Math.PI * 2) / this.slots;
    if (this.mode === "full") return (Math.PI * 2) / Math.max(1, this.count);
    return ((Math.PI * 2) / this.slots) * 1.1;
  }

  private cardWidth(): number {
    const base = ((Math.PI * 2 * R) / this.slots) * 0.95;
    if (this.mode === "full") return base * Math.min(1.3, this.slots / this.count);
    if (this.mode === "arc") return base * 1.1;
    return base;
  }

  resize({ width, height }: Viewport): void {
    const aspect = width / height;
    const previousSlots = this.slots;
    this.slots = width < 700 ? 16 : width < 1100 ? 22 : 26;
    // Portrait screens look down on the ring a little more so it stays legible.
    const portrait = aspect < 1;
    this.elevation = portrait ? 0.4 : ELEVATION;
    const extent = portrait ? 0.52 : aspect < 1.2 ? 0.47 : 0.4;
    this.scale = Math.max(1, (REF_ASPECT / aspect) * (REF_EXTENT / extent));
    if (previousSlots !== this.slots) this.setCount(this.count, false);
    // Pixels per slot at the front of the ring: maps a drag to a rotation so
    // the front prints follow the pointer.
    const pose = { pos: new Vector3(), target: new Vector3(), fov: FOV };
    this.camera(pose);
    this.probe.aspect = aspect;
    this.probe.fov = FOV;
    this.probe.position.copy(pose.pos);
    this.probe.lookAt(pose.target);
    this.probe.updateProjectionMatrix();
    this.probe.updateMatrixWorld();
    const d = this.spacing();
    const a = new Vector3(0, 0, R).project(this.probe);
    const b = new Vector3(R * Math.sin(d), 0, R * Math.cos(d)).project(this.probe);
    this.pxPerSlot = Math.max(8, (Math.abs(b.x - a.x) * width) / 2);
  }

  camera(out: CameraPose): void {
    const s = this.scale;
    out.fov = FOV;
    out.target.set(0, TARGET_Y * s, 0);
    out.pos.set(0, TARGET_Y * s + Math.sin(this.elevation) * DISTANCE * s, Math.cos(this.elevation) * DISTANCE * s);
  }

  /** Signed slot offset of item i from the front, or NaN when off the ring. */
  private offset(i: number): number {
    const n = this.count;
    if (this.mode === "arc") return i - this.u;
    const d = mod(i - this.u, n);
    if (this.mode === "full") return d >= n / 2 ? d - n : d;
    // Window of `slots` positions, (−S/2 + ½, S/2 + ½]: with an integer u every
    // slot is filled and the hand-over happens half a slot behind the rear card.
    const hi = this.slots / 2 + 0.5;
    const lo = hi - this.slots;
    if (d < hi) return d;
    if (d - n >= lo) return d - n;
    return Number.NaN;
  }

  pose(i: number, out: Pose): void {
    const rel = this.offset(i);
    if (Number.isNaN(rel)) {
      out.opacity = 0;
      return;
    }
    const theta = rel * this.spacing();
    out.pos.set(R * Math.sin(theta), 0, R * Math.cos(theta));
    out.quat.setFromAxisAngle(UP, theta);
    out.w = this.cardWidth();
    out.h = out.w / CARD_ASPECT;
    out.bend = R;
    if (this.mode === "window" && this.count > this.slots) {
      // Prints are exchanged at the rear, where they are smallest: fade there.
      const hi = this.slots / 2 + 0.5;
      out.opacity = smoothstep(0, 0.45, Math.min(rel - (hi - this.slots), hi - rel));
    } else if (this.mode === "arc") {
      out.opacity = smoothstep(Math.PI, Math.PI - 0.35, Math.abs(theta));
    } else {
      out.opacity = 1;
    }
  }

  update(dt: number, reducedMotion: boolean): void {
    if (this.count === 0) return;
    this.idle += dt;
    if (this.dragging) {
      this.tracker.decayIdle(dt);
      return;
    }
    if (this.seek !== null) {
      this.u = damp(this.u, this.seek, reducedMotion ? 30 : 7, dt);
      if (Math.abs(this.u - this.seek) < 1e-3) {
        this.u = this.seek;
        this.seek = null;
      }
      return;
    }
    this.u += this.velocity * dt;
    this.velocity *= Math.exp(-dt * (reducedMotion ? 9 : 2.1));
    if (this.mode === "arc") {
      const bounded = clamp(this.u, 0, this.count - 1);
      if (bounded !== this.u) {
        this.u = damp(this.u, bounded, 9, dt);
        this.velocity *= Math.exp(-dt * 10);
      }
    } else if (!reducedMotion && this.idle > 2.5) {
      // A slow drift once the visitor lets go, as in the reference motion.
      const ramp = Math.min(1, (this.idle - 2.5) / 2);
      if (Math.abs(this.velocity) < 0.14) this.velocity = damp(this.velocity, 0.11 * ramp, 1.2, dt);
    }
    if (Math.abs(this.velocity) < 1e-4) this.velocity = 0;
  }

  dragStart(): void {
    this.dragging = true;
    this.seek = null;
    this.velocity = 0;
    this.tracker.value = 0;
    this.idle = 0;
  }

  drag(dxPx: number, _dyPx: number, dt: number): void {
    let du = -dxPx / this.pxPerSlot;
    if (this.mode === "arc" && (this.u < 0 || this.u > this.count - 1)) du *= 0.35;
    this.u += du;
    this.tracker.add(du, dt);
    this.idle = 0;
  }

  dragEnd(): void {
    this.dragging = false;
    this.velocity = clamp(this.tracker.value, -14, 14);
    this.idle = 0;
  }

  wheel(dx: number, dy: number): void {
    const delta = Math.abs(dx) > Math.abs(dy) ? dx : dy;
    this.seek = null;
    this.velocity = clamp(this.velocity + delta * 0.0045, -10, 10);
    this.idle = 0;
  }

  key(code: string): boolean {
    if (!this.count) return false;
    const base = this.seek ?? Math.round(this.u);
    if (code === "ArrowRight") this.seek = base + 1;
    else if (code === "ArrowLeft") this.seek = base - 1;
    else if (code === "Home") this.seek = this.targetFor(0);
    else return false;
    if (this.mode === "arc") this.seek = clamp(this.seek, 0, this.count - 1);
    this.velocity = 0;
    this.idle = 0;
    return true;
  }

  /** The rotation u at which item i faces the camera (shortest way round). */
  private targetFor(i: number): number {
    if (this.mode === "arc") return clamp(i, 0, this.count - 1);
    const d = mod(i - this.u, this.count);
    return this.u + (d >= this.count / 2 ? d - this.count : d);
  }

  focus(i: number): void {
    if (!this.count) return;
    this.seek = this.targetFor(i);
    this.velocity = 0;
    this.idle = 0;
  }

  active(): number {
    if (!this.count) return -1;
    if (this.mode === "arc") return clamp(Math.round(this.u), 0, this.count - 1);
    return mod(Math.round(this.u), this.count);
  }

  progress(): number {
    if (this.count <= 1) return 0;
    return this.mode === "arc" ? clamp(this.u / (this.count - 1), 0, 1) : mod(this.u, this.count) / this.count;
  }
}
