// Run with `npm test` (Node ≥ 22.18 strips the TypeScript types natively).
import { test } from "node:test";
import assert from "node:assert/strict";
import { RingLayout } from "../src/engine/layouts/ring.ts";
import { CorridorLayout } from "../src/engine/layouts/corridor.ts";
import { newCameraPose, newPose } from "../src/engine/layouts/layout.ts";
import { searchParams, stateFromUrl, sameSearch } from "../src/store.ts";
import { formatTime } from "../src/engine/math.ts";

const viewport = { width: 1942, height: 809 };
const step = (layout: { update(dt: number, r: boolean): void }, seconds: number) => {
  for (let t = 0; t < seconds; t += 1 / 60) layout.update(1 / 60, true);
};

test("ring: every result reachable once, no duplicates in a window", () => {
  const ring = new RingLayout();
  ring.resize(viewport);
  ring.setCount(50, true);
  const pose = newPose();
  const visible = [] as number[];
  for (let i = 0; i < 50; i++) {
    pose.opacity = 1;
    ring.pose(i, pose, newCameraPose());
    if (pose.opacity > 0.01) visible.push(i);
  }
  assert.equal(visible.length, 26); // 26 slots on desktop, the rest stream in at the rear
  assert.ok(visible.includes(0) && visible.includes(1) && visible.includes(49));
  for (let i = 0; i < 50; i++) {
    ring.focus(i);
    step(ring, 3);
    assert.equal(ring.active(), i);
  }
});

test("ring: small result sets form a bounded arc starting at the front", () => {
  const ring = new RingLayout();
  ring.resize(viewport);
  ring.setCount(3, true);
  const pose = newPose();
  ring.pose(0, pose, newCameraPose());
  assert.ok(Math.abs(pose.pos.x) < 1e-9 && pose.pos.z > 0); // rank 1 faces the camera
  ring.dragStart();
  ring.drag(-5000, 0, 0.1); // drag far past the end
  ring.dragEnd();
  step(ring, 3);
  assert.equal(ring.active(), 2);
  ring.setCount(1, true);
  assert.equal(ring.active(), 0);
});

test("ring: fling keeps momentum then settles", () => {
  const ring = new RingLayout();
  ring.resize(viewport);
  ring.setCount(40, true);
  ring.dragStart();
  for (let i = 0; i < 8; i++) ring.drag(-30, 0, 1 / 60);
  ring.dragEnd();
  const before = ring.progress();
  for (let t = 0; t < 0.5; t += 1 / 60) ring.update(1 / 60, false);
  assert.notEqual(ring.progress(), before);
  const moving = (ring as unknown as { velocity: number }).velocity;
  assert.ok(Math.abs(moving) > 0.05);
  for (let t = 0; t < 2; t += 1 / 60) ring.update(1 / 60, false);
  assert.ok(Math.abs((ring as unknown as { velocity: number }).velocity) < Math.abs(moving));
});

test("corridor: travel is reversible and progress tracks it", () => {
  const corridor = new CorridorLayout();
  corridor.resize({ width: 1640, height: 775 });
  corridor.setCount(50, true);
  assert.equal(corridor.active(), 0);
  assert.equal(corridor.progress(), 0);
  for (let i = 0; i < 10; i++) corridor.wheel(0, 300);
  step(corridor, 4);
  const forward = corridor.progress();
  assert.ok(forward > 0.3 && corridor.active() > 0);
  for (let i = 0; i < 10; i++) corridor.wheel(0, -300);
  step(corridor, 4);
  assert.ok(corridor.progress() < 0.001 && corridor.active() === 0);
  corridor.key("End");
  step(corridor, 4);
  assert.equal(corridor.active(), 49);
  assert.ok(corridor.progress() > 0.999);
});

test("url state round-trips and compares searches", () => {
  const state = { query: "tay đua", k: 20, sources: ["ocr"], collection: 1, topic: null, view: "corridor" as const, requestId: "YC-1" };
  const qs = searchParams(state, true).toString();
  const back = stateFromUrl("?" + qs, { k: 50 } as never);
  assert.equal(back.query, "tay đua");
  assert.equal(back.view, "corridor");
  assert.deepEqual(back.sources, ["ocr"]);
  assert.equal(back.collection, 1);
  assert.equal(back.requestId, "YC-1");
  assert.ok(sameSearch(state, { ...state, query: " tay đua " }));
  assert.ok(!sameSearch(state, { ...state, k: 50 }));
  assert.equal(searchParams({ query: "", k: 50, sources: ["visual", "ocr", "asr"], collection: null, topic: null }, false).toString(), "");
});

test("timecodes", () => {
  assert.equal(formatTime(436.4), "07:16");
  assert.equal(formatTime(3725), "1:02:05");
});
