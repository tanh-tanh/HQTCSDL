import { Matrix4, Quaternion, Vector3 } from "three";
import { clamp, damp, hash01 } from "../math.ts";
import { VelocityTracker } from "./layout.ts";
import type { CameraPose, Layout, Pose, Viewport } from "./layout.ts";

const GOLDEN = Math.PI * (3 - Math.sqrt(5));
const UP = new Vector3(0, 1, 0);

/**
 * Prints scattered on a loose sphere (Fibonacci directions, deterministic
 * radius jitter); best ranks sit nearer the centre and are larger.  The
 * camera orbits the cluster: drag to orbit with inertia, wheel to zoom.
 * Prints always face the camera.
 */
export class ConstellationLayout implements Layout {
  readonly name = "constellation" as const;
  readonly hoverLift = 0.4;
  count = 0;
  private yaw = 0.35;
  private pitch = 0.18;
  private yawVel = 0;
  private pitchVel = 0;
  private distance = 28;
  private distanceTarget = 28;
  private dragging = false;
  private idle = 0;
  private trackX = new VelocityTracker();
  private trackY = new VelocityTracker();
  private points: Vector3[] = [];
  private facing = new Quaternion();
  private eye = new Vector3();
  private matrix = new Matrix4();
  private fov = 34;
  private seekYaw: number | null = null;
  private seekPitch: number | null = null;

  setCount(n: number, reset: boolean): void {
    this.count = n;
    this.points = [];
    for (let i = 0; i < n; i++) {
      const t = n === 1 ? 0.5 : (i + 0.5) / n;
      const y = 1 - 2 * t;
      const r = Math.sqrt(1 - y * y);
      const a = i * GOLDEN;
      const rank = n > 1 ? i / (n - 1) : 0;
      const radius = n === 1 ? 0 : 3 + 5.4 * rank + 1.3 * hash01(i + 17);
      this.points.push(new Vector3(Math.cos(a) * r, y * 0.5, Math.sin(a) * r).multiplyScalar(radius));
    }
    if (reset) {
      this.yawVel = this.pitchVel = 0;
      this.seekYaw = this.seekPitch = null;
    }
  }

  resize({ width, height }: Viewport): void {
    const aspect = width / height;
    this.fov = aspect < 1 ? 52 : 34;
  }

  camera(out: CameraPose): void {
    out.fov = this.fov;
    // Look slightly above the cluster so it sits below the search controls.
    out.target.set(0, 1.4, 0);
    out.pos.set(
      Math.sin(this.yaw) * Math.cos(this.pitch) * this.distance,
      1.4 + Math.sin(this.pitch) * this.distance,
      Math.cos(this.yaw) * Math.cos(this.pitch) * this.distance,
    );
  }

  pose(i: number, out: Pose): void {
    const p = this.points[i];
    if (!p) {
      out.opacity = 0;
      return;
    }
    out.pos.copy(p);
    out.quat.copy(this.facing);
    const rank = this.count > 1 ? i / (this.count - 1) : 0;
    out.w = 2.6 - 1.1 * rank;
    out.h = out.w / (16 / 9);
    out.bend = 0;
    // Prints behind the cluster centre recede a little.
    const toCam = this.eye.clone().sub(p).normalize();
    const facingAway = p.length() > 0.01 ? -p.clone().normalize().dot(toCam) : -1;
    out.opacity = 1 - 0.45 * clamp(facingAway, 0, 1);
  }

  update(dt: number, reducedMotion: boolean): void {
    this.idle += dt;
    if (this.dragging) {
      this.trackX.decayIdle(dt);
      this.trackY.decayIdle(dt);
    } else {
      if (this.seekYaw !== null && this.seekPitch !== null) {
        this.yaw = damp(this.yaw, this.seekYaw, 6, dt);
        this.pitch = damp(this.pitch, this.seekPitch, 6, dt);
        if (Math.abs(this.yaw - this.seekYaw) < 1e-3) this.seekYaw = this.seekPitch = null;
      } else {
        this.yaw += this.yawVel * dt;
        this.pitch += this.pitchVel * dt;
        const k = Math.exp(-dt * (reducedMotion ? 9 : 2.4));
        this.yawVel *= k;
        this.pitchVel *= k;
        if (!reducedMotion && this.idle > 2.5 && Math.abs(this.yawVel) < 0.05) {
          this.yawVel = damp(this.yawVel, 0.035, 1, dt);
        }
      }
    }
    this.pitch = clamp(this.pitch, -0.85, 0.85);
    this.distance = damp(this.distance, this.distanceTarget, 6, dt);
    const cam = { pos: this.eye, target: new Vector3(), fov: this.fov };
    this.camera(cam);
    this.matrix.lookAt(this.eye, cam.target, UP);
    this.facing.setFromRotationMatrix(this.matrix);
  }

  dragStart(): void {
    this.dragging = true;
    this.seekYaw = this.seekPitch = null;
    this.yawVel = this.pitchVel = 0;
    this.trackX.value = this.trackY.value = 0;
    this.idle = 0;
  }

  drag(dxPx: number, dyPx: number, dt: number): void {
    const dYaw = -dxPx * 0.0042;
    const dPitch = dyPx * 0.0032;
    this.yaw += dYaw;
    this.pitch = clamp(this.pitch + dPitch, -0.85, 0.85);
    this.trackX.add(dYaw, dt);
    this.trackY.add(dPitch, dt);
    this.idle = 0;
  }

  dragEnd(): void {
    this.dragging = false;
    this.yawVel = clamp(this.trackX.value, -4, 4);
    this.pitchVel = clamp(this.trackY.value, -2, 2);
    this.idle = 0;
  }

  wheel(_dx: number, dy: number): void {
    this.distanceTarget = clamp(this.distanceTarget + dy * 0.02, 15, 46);
    this.idle = 0;
  }

  key(code: string): boolean {
    const step = 0.35;
    if (code === "ArrowLeft") this.yawVel -= step * 2;
    else if (code === "ArrowRight") this.yawVel += step * 2;
    else if (code === "ArrowUp") this.pitchVel -= step;
    else if (code === "ArrowDown") this.pitchVel += step;
    else return false;
    this.idle = 0;
    return true;
  }

  focus(i: number): void {
    const p = this.points[i];
    if (!p || p.length() < 0.01) return;
    const yaw = Math.atan2(p.x, p.z);
    // Shortest way round.
    this.seekYaw = this.yaw + (((yaw - this.yaw + Math.PI) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    this.seekPitch = clamp(Math.asin(clamp(p.y / p.length(), -1, 1)), -0.6, 0.6);
    this.yawVel = this.pitchVel = 0;
    this.idle = 0;
  }

  active(): number {
    // The print closest to the line of sight, preferring the near side.
    const forward = this.eye.clone().negate().normalize();
    let best = -1;
    let bestScore = -Infinity;
    this.points.forEach((p, i) => {
      const to = p.clone().sub(this.eye);
      const dist = to.length();
      const score = to.normalize().dot(forward) * 4 - dist * 0.05;
      if (score > bestScore) {
        bestScore = score;
        best = i;
      }
    });
    return best;
  }

  progress(): number {
    const a = this.active();
    return this.count > 1 && a >= 0 ? a / (this.count - 1) : 0;
  }
}
