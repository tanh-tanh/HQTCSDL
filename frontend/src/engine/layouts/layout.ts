import { Quaternion, Vector3 } from "three";
import type { ViewName } from "../../types.ts";

export interface Pose {
  pos: Vector3;
  quat: Quaternion;
  /** Card width/height in world units. */
  w: number;
  h: number;
  /** Cylinder radius the card is bent along (0 = flat). */
  bend: number;
  opacity: number;
}

export interface CameraPose {
  pos: Vector3;
  target: Vector3;
  fov: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export const newPose = (): Pose => ({
  pos: new Vector3(), quat: new Quaternion(), w: 1, h: 1, bend: 0, opacity: 1,
});
export const newCameraPose = (): CameraPose => ({ pos: new Vector3(), target: new Vector3(), fov: 30 });

export interface Layout {
  readonly name: ViewName;
  /** Number of items currently laid out. */
  count: number;
  setCount(n: number, reset: boolean): void;
  resize(viewport: Viewport): void;
  update(dt: number, reducedMotion: boolean): void;
  pose(index: number, out: Pose, camera: CameraPose): void;
  camera(out: CameraPose): void;
  dragStart(): void;
  drag(dxPx: number, dyPx: number, dt: number): void;
  dragEnd(): void;
  wheel(dx: number, dy: number): void;
  key(code: string): boolean;
  focus(index: number): void;
  /** Item the view is currently "on" (front of the ring, NOW VIEWING...). */
  active(): number;
  /** 0..1 position through the result set. */
  progress(): number;
  /** Outward direction used for the hover lift, in the card's local frame. */
  readonly hoverLift: number;
}

/** Tracks a smoothed pointer velocity for inertia (units per second). */
export class VelocityTracker {
  value = 0;
  private moved = false;
  add(delta: number, dt: number): void {
    if (dt <= 0) return;
    const instant = delta / Math.max(dt, 1 / 240);
    // Weight recent movement more.
    this.value = this.value * 0.55 + instant * 0.45;
    this.moved = true;
  }
  /** Called once per frame while dragging: a pause before release kills the fling. */
  decayIdle(dt: number): void {
    if (!this.moved) this.value *= Math.exp(-dt * 14);
    this.moved = false;
  }
}
