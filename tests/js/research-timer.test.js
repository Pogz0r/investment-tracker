const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  calculateDurationSeconds,
  calculateLiveElapsedSeconds,
  shouldRunLiveTimer,
  createCompletionCoordinator,
} = require("../../static/js/research-timer.js");


test("calculates persisted completion duration and floors fractional seconds", () => {
  assert.equal(
    calculateDurationSeconds("2026-07-14T10:00:00.000Z", "2026-07-14T10:02:05.900Z"),
    125,
  );
});


test("rejects missing, invalid, and negative persisted durations", () => {
  assert.equal(calculateDurationSeconds(null, "2026-07-14T10:00:00Z"), null);
  assert.equal(calculateDurationSeconds("not-a-date", "2026-07-14T10:00:00Z"), null);
  assert.equal(
    calculateDurationSeconds("2026-07-14T10:01:00Z", "2026-07-14T10:00:00Z"),
    null,
  );
});


test("keeps live elapsed time based on the current clock only for active statuses", () => {
  const start = Date.parse("2026-07-14T10:00:00Z");
  assert.equal(calculateLiveElapsedSeconds(start, start + 42_900), 42);
  assert.equal(calculateLiveElapsedSeconds(start, start - 1_000), 0);
  assert.equal(shouldRunLiveTimer("queued"), true);
  assert.equal(shouldRunLiveTimer("running"), true);
  assert.equal(shouldRunLiveTimer("complete"), false);
  assert.equal(shouldRunLiveTimer("error"), false);
});


function createHarness(fallbackSeconds = 37) {
  const calls = [];
  const renders = [];
  const coordinator = createCompletionCoordinator({
    stopPolling: () => calls.push("polling"),
    stopElapsedTimer: () => calls.push("timer"),
    closeEventSource: () => calls.push("stream"),
    getFallbackSeconds: () => fallbackSeconds,
    renderComplete: (seconds) => renders.push(seconds),
  });
  return { coordinator, calls, renders };
}


test("begins completion synchronously and only once per run", () => {
  const { coordinator, calls } = createHarness();
  coordinator.reset(7);

  assert.equal(coordinator.beginCompletion(7), true);
  assert.deepEqual(calls, ["polling", "timer", "stream"]);
  assert.equal(coordinator.beginCompletion(7), true);
  assert.deepEqual(calls, ["polling", "timer", "stream"]);
});


test("renders fallback once and upgrades it to authoritative persisted duration", () => {
  const { coordinator, renders } = createHarness(37);
  coordinator.reset(7);
  coordinator.beginCompletion(7);

  assert.equal(coordinator.finishCompletion(7, null), true);
  assert.deepEqual(renders, [37]);
  assert.equal(coordinator.finishCompletion(7, {
    id: 7,
    started_at: "2026-07-14T10:00:00Z",
    completed_at: "2026-07-14T10:02:05Z",
  }), true);
  assert.deepEqual(renders, [37, 125]);
  assert.equal(coordinator.finishCompletion(7, null), false);
  assert.deepEqual(renders, [37, 125]);
});


test("ignores stale records and pending completion callbacks after a run switch", () => {
  const { coordinator, calls, renders } = createHarness();
  coordinator.reset(7);
  coordinator.beginCompletion(7);
  coordinator.reset(8);

  assert.equal(coordinator.finishCompletion(7, null), false);
  assert.equal(coordinator.finishCompletion(7, {
    id: 7,
    started_at: "2026-07-14T10:00:00Z",
    completed_at: "2026-07-14T10:01:00Z",
  }), false);
  assert.equal(coordinator.beginCompletion(7), false);
  assert.deepEqual(calls, ["polling", "timer", "stream"]);
  assert.deepEqual(renders, []);
});


test("resets terminal state when switching directly between completed runs", () => {
  const { coordinator, renders } = createHarness();
  coordinator.reset(7);
  coordinator.beginCompletion(7);
  coordinator.finishCompletion(7, {
    id: 7,
    started_at: "2026-07-14T10:00:00Z",
    completed_at: "2026-07-14T10:01:00Z",
  });

  coordinator.reset(8);
  coordinator.beginCompletion(8);
  coordinator.finishCompletion(8, {
    id: 8,
    started_at: "2026-07-14T11:00:00Z",
    completed_at: "2026-07-14T11:02:00Z",
  });

  assert.deepEqual(renders, [60, 120]);
});


test("research page wires the helper before the UI and stops SSE timing before refresh", () => {
  const root = path.resolve(__dirname, "../..");
  const template = fs.readFileSync(path.join(root, "templates/research.html"), "utf8");
  const source = fs.readFileSync(path.join(root, "static/js/research.js"), "utf8");

  const helperIndex = template.indexOf("/static/js/research-timer.js");
  const uiIndex = template.indexOf("/static/js/research.js");
  assert.ok(helperIndex >= 0 && helperIndex < uiIndex);
  assert.match(source, /async function handleEvent\(event, sourceRunId\)/);
  const completeEvent = source.slice(
    source.indexOf('if (event.type === "pipeline_complete")'),
    source.indexOf('if (event.type === "error")'),
  );
  assert.ok(completeEvent.indexOf("beginCompletion(sourceRunId)") < completeEvent.indexOf("await refreshRun()"));
  assert.match(completeEvent, /finally\s*{/);
  assert.match(source, /shouldRunLiveTimer\(run\.status\)/);
});
