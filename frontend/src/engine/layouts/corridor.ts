import { Quaternion, Vector3 } from "three";
import { clamp, damp, smoothstep } from "../math.ts";
import { VelocityTracker } from "./layout.ts";
import type { CameraPose, Layout, Pose, Viewport } from "./layout.ts";

// Geometry fitted to the approved CORRIDOR screenshot (1640×775 inner frame):
// two walls (left/right) × two rows (upper/lower) of prints receding to one
// vanishing point.  Item i goes to lane i % 4 at depth LANE_DEPTH[lane] +
// ⌊i/4⌋·STEP, so ranks are met in order while travelling forward.
const REF_ASPECT = 1640 / 775;
const REF_FOV = 19.77;
const LANE_X = 2.893;
// The walls lean in slightly with depth so the far prints meet near the centre.
const X_CONVERGE = 0.06;
const ROW_Y = [0.5185, 0.5185, -1.156, -1.156];
const SIDE = [-1, 1, -1, 1];
const LANE_DEPTH = [8.07, 8.59, 11.11, 13.28];
const STEP = 4.485;
const YAW = 0.722;
const CARD_W = 1.24;
const CARD_H = CARD_W / 1.36;
const NEAR_FADE: [number, number] = [4.2, 6.6];
const FAR_FADE: [number, number] = [31, 45];
const ACTIVE_MIN_DEPTH = 7.6;
const UP = new Vector3(0, 1, 0);

export class CorridorLayout implements Layout {
  readonly name = "corridor" as const;
  readonly hoverLift = 0.18;
  count = 0;
  private travel = 0; // current camera travel (world units along −z)
  private target = 0;
  private velocity = 0;
  private dragging = false;
  private tracker = new VelocityTracker();
  private fov = REF_FOV;
  private laneX = LANE_X;
  private yaw: Quaternion[] = SIDE.map((side) => new Quaternion().setFromAxisAngle(UP, -side * YAW));

  depth(i: number): number {
    return LANE_DEPTH[i % 4] + Math.floor(i / 4) * STEP;
  }

  private maxTravel(): number {
    return this.count ? Math.max(0, this.depth(this.count - 1) - ACTIVE_MIN_DEPTH - 0.2) : 0;
  }

  setCount(n: number, reset: boolean): void {
    this.count = n;
    if (reset) {
      this.travel = this.target = this.velocity = 0;
    } else {
      this.target = clamp(this.target, 0, this.maxTravel());
    }
  }

  resize({ width, height }: Viewport): void {
    const aspect = width / height;
    // Keep the reference horizontal field of view so both walls stay in frame.
    const half = Math.atan((Math.tan((REF_FOV * Math.PI) / 360) * REF_ASPECT) / aspect);
    this.fov = aspect < 1 ? 44 : clamp((half * 360) / Math.PI, REF_FOV, 30);
    // Portrait: narrower walls and a wider lens instead of an extreme field of view.
    this.laneX = aspect < 1 ? LANE_X * 0.42 : aspect < 1.6 ? LANE_X * (0.75 + 0.25 * (aspect - 1) / 0.6) : LANE_X;
  }

  camera(out: CameraPose): void {
    out.fov = this.fov;
    out.pos.set(0, 0, -this.travel);
    out.target.set(0, 0, -this.travel - 10);
  }

  pose(i: number, out: Pose): void {
    const lane = i % 4;
    const z = -this.depth(i);
    const dz = this.depth(i) - this.travel;
    const x = Math.max(this.laneX * 0.1, this.laneX - X_CONVERGE * Math.max(0, dz));
    out.pos.set(SIDE[lane] * x, ROW_Y[lane], z);
    out.quat.copy(this.yaw[lane]);
    out.w = CARD_W;
    out.h = CARD_H;
    out.bend = 0;
    out.opacity = smoothstep(NEAR_FADE[0], NEAR_FADE[1], dz) * (1 - smoothstep(FAR_FADE[0], FAR_FADE[1], dz));
  }

  update(dt: number, reducedMotion: boolean): void {
    if (this.dragging) this.tracker.decayIdle(dt);
    if (!this.dragging && this.velocity !== 0) {
      this.target += this.velocity * dt;
      this.velocity *= Math.exp(-dt * (reducedMotion ? 10 : 3.2));
      if (Math.abs(this.velocity) < 1e-3) this.velocity = 0;
    }
    const max = this.maxTravel();
    if (this.target < 0 || this.target > max) {
      this.target = clamp(this.target, 0, max);
      this.velocity = 0;
    }
    this.travel = damp(this.travel, this.target, reducedMotion ? 30 : 5.5, dt);
    if (Math.abs(this.travel - this.target) < 1e-4) this.travel = this.target;
  }

  dragStart(): void {
    this.dragging = true;
    this.velocity = 0;
    this.tracker.value = 0;
  }

  drag(dxPx: number, dyPx: number, dt: number): void {
    // Pull the corridor toward you: drag up or left to move forward.
    const delta = (-dyPx - dxPx * 0.6) * 0.03;
    this.target = clamp(this.target + delta, -0.5, this.maxTravel() + 0.5);
    this.tracker.add(delta, dt);
  }

  dragEnd(): void {
    this.dragging = false;
    this.velocity = clamp(this.tracker.value, -40, 40);
  }

  wheel(dx: number, dy: number): void {
    const delta = Math.abs(dy) >= Math.abs(dx) ? dy : dx;
    this.velocity = 0;
    this.target = clamp(this.target + delta * 0.012, 0, this.maxTravel());
  }

  key(code: string): boolean {
    if (!this.count) return false;
    const active = Math.max(0, this.active());
    if (["ArrowDown", "ArrowRight", "PageDown"].includes(code)) this.focus(Math.min(this.count - 1, active + 1));
    else if (["ArrowUp", "ArrowLeft", "PageUp"].includes(code)) this.focus(Math.max(0, active - 1));
    else if (code === "Home") this.focus(0);
    else if (code === "End") this.focus(this.count - 1);
    else return false;
    return true;
  }

  focus(i: number): void {
    this.velocity = 0;
    this.target = clamp(this.depth(i) - ACTIVE_MIN_DEPTH - 0.2, 0, this.maxTravel());
  }

  active(): number {
    // The nearest print that has not started to pass the camera.
    let best = -1;
    let bestDz = Infinity;
    for (let i = 0; i < this.count; i++) {
      const dz = this.depth(i) - this.travel;
      if (dz >= ACTIVE_MIN_DEPTH && dz < bestDz) {
        best = i;
        bestDz = dz;
      }
    }
    return best === -1 ? this.count - 1 : best;
  }

  progress(): number {
    const max = this.maxTravel();
    return max > 0 ? clamp(this.travel / max, 0, 1) : this.count ? 1 : 0;
  }
}
