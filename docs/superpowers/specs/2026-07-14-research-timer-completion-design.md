# Research Timer Completion Design

## Problem

The Research page correctly clears its JavaScript timer interval when a pipeline completes, but completion rendering derives elapsed time from the browser's current clock. Restoring an older completed run therefore calculates `Date.now() - started_at`, producing an inflated duration such as `12973:49` even though the pipeline has already finished.

## Desired Behavior

- While a run is `queued` or `running`, display live elapsed time using the current clock and the persisted `started_at` timestamp.
- When a run becomes `complete`, clear the interval immediately and freeze the display at the authoritative pipeline duration: `completed_at - started_at`.
- Use the same frozen value in the large elapsed timer and the `Pipeline finished in ...` description.
- When restoring an already completed run, render its persisted duration without starting a live interval first.
- Preserve existing error, retry, polling, stream, pipeline, and output-rendering behavior.

## Frontend Design

### Duration calculation

Add a pure helper that accepts `started_at` and `completed_at` values and returns non-negative whole seconds, or `null` when either timestamp is missing, invalid, or earlier than the start. Fractional seconds are floored to match the existing live timer. A negative duration is invalid rather than clamped because clamping would invent a successful zero-second duration and suppress the live fallback.

The helper lives in `static/js/research-timer.js` as a dependency-free UMD module: it assigns `window.ResearchTimer` in the browser and `module.exports` under Node. `templates/research.html` loads it immediately before `research.js`.

### Completion transition

One completion coordinator owns the terminal transition for the current run. It tracks the current run ID, whether completion has begun, the captured live fallback seconds, whether a completion value has rendered, and whether that rendered value came from persisted timestamps.

Its `beginCompletion(runId)` operation performs these actions synchronously and idempotently:

1. Stop polling.
2. Stop the elapsed interval.
3. Close and clear the event stream.
4. Capture the last numeric live elapsed seconds maintained in memory by `updateElapsedDisplay`.

Calling `beginCompletion` again for the same run does not recapture or restart anything. Every current-run ID change resets the coordinator before any running or completed state is applied, including switching directly between two completed runs.

Its `finishCompletion(originatingRunId, runOrNull)` operation first rejects any call whose explicit originating ID no longer matches the coordinator's current run, then chooses and renders the duration:

- A valid persisted duration from a matching run is authoritative.
- If timestamps are unavailable, use the coordinator's captured numeric fallback.
- If neither is available, use zero.
- The first fallback may be replaced once by a later valid persisted duration from the same run.
- Once a persisted duration has rendered, duplicate polling or SSE callbacks cannot overwrite it.
- A record for a different run ID is ignored.

The complete renderer no longer calls the live `updateElapsedDisplay()` function. It writes the fixed duration directly to `#total-elapsed`, `#current-stage-elapsed`, and the completion description. The stage elapsed text becomes `0s`, matching the existing completed-state meaning that no stage is currently active.

### State restoration

`applyRunState` starts the live timer only when the run status is `queued` or `running`. For a completed run, it synchronously begins completion and then finishes with the run record. This prevents a restored run from briefly calculating elapsed time against the current date.

`connectStream(runId)` passes its captured `runId` into every event callback. The `pipeline_complete` handler ignores the event unless that originating ID still equals `currentRunId`. This prevents a delayed event from an old stream from stopping a newly selected run.

For a matching event, the handler calls `beginCompletion(originatingRunId)` before any `await`, then refreshes the run record. A `finally` path always calls `finishCompletion(originatingRunId, refreshedRunOrNull)`, so an exception or refresh failure still renders the captured fallback when that run remains current. Passing the originating ID separately ensures that even a null result cannot complete a different run selected while the refresh was pending. Existing refresh logging/error behavior remains unchanged. A concurrent polling response may finish completion first; coordinator idempotency ensures the later callback can only upgrade a fallback to the same run's valid persisted duration, never resume or inflate the timer.

### Missing timestamp fallback

`updateElapsedDisplay` stores its calculated total seconds in a numeric `lastElapsedTotalSeconds` variable as well as writing the DOM. If a completed run lacks valid timestamps, completion freezes this in-memory value rather than parsing formatted UI text. If no live elapsed value is available, it displays `00:00`. This keeps completion deterministic without inventing a duration.

## Testing

Use Node's built-in test runner with a small dependency-free timer helper module so no package is added. Tests cover:

- Persisted completed duration calculation.
- Flooring fractional seconds.
- Protection against negative durations.
- Missing and invalid timestamps.
- Live elapsed calculation remaining based on the current clock.
- Completion-state selection of persisted duration over the current clock.
- Synchronous resource shutdown when completion begins.
- Restored completed runs never entering live-timer state.
- Missing-record refresh fallback to captured numeric elapsed time.
- Duplicate completion signals remaining idempotent.
- A later valid persisted duration upgrading a fallback once.
- Mismatched or stale run records being ignored.
- Switching directly between two completed run IDs resetting completion state.
- A delayed SSE event from an old run leaving the current run untouched.
- A rejected completion refresh still rendering the captured fallback through `finally`.
- Switching runs while completion refresh is pending, followed by either a null result or rejection, leaving the new run untouched.

Add focused integration assertions confirming that:

- Completed runs do not start the live interval in `applyRunState`.
- The SSE event path begins completion before awaiting refresh.
- Restored completion and SSE completion use the same coordinator.

Coordinator tests use injected stop and render callbacks, making shutdown order and observable rendered duration testable without a browser DOM. Minimal source-level assertions cover only the two existing `research.js` integration call sites; timer calculation and race behavior are verified through executable helper tests.

Run the focused red test before implementation, then run it green, followed by the full pytest suite, JavaScript tests, JavaScript syntax checks, and Python compile checks.

## Scope

The implementation is limited to the Research page timer helper, timer lifecycle wiring, and regression tests. It does not change backend pipeline execution, timestamps, polling frequency, stored research results, or the completed panel's visual design.
