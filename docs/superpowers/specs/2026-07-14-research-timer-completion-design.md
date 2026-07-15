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

Add a pure helper that accepts `started_at` and `completed_at` values and returns non-negative whole seconds, or `null` when either timestamp is missing or invalid. Fractional seconds are floored to match the existing live timer.

### Completion transition

The completion handler accepts the completed run record. It performs these operations in order:

1. Stop polling.
2. Stop the elapsed interval.
3. Close and clear the event stream.
4. Calculate the persisted duration from the run timestamps.
5. Render the complete state with that fixed duration.

The complete renderer no longer calls the live `updateElapsedDisplay()` function. It writes the fixed duration directly to `#total-elapsed`, `#current-stage-elapsed`, and the completion description. The stage elapsed text becomes `0s`, matching the existing completed-state meaning that no stage is currently active.

### State restoration

`applyRunState` starts the live timer only when the run status is `queued` or `running`. For a completed run, it passes the run record directly to the completion handler. This prevents a restored run from briefly calculating elapsed time against the current date.

The server-sent `pipeline_complete` handler first refreshes the run record, then completes using the refreshed record. If refresh unexpectedly returns no record, it stops the interval immediately and uses the currently displayed elapsed seconds as a fallback rather than allowing the timer to continue.

### Missing timestamp fallback

If a completed run lacks valid timestamps, completion still stops every timer and stream. The UI freezes the elapsed seconds already shown by the live timer. If no live elapsed value is available, it displays `00:00`. This keeps completion deterministic without inventing a duration.

## Testing

Use Node's built-in test runner with a small dependency-free timer helper module so no package is added. Tests cover:

- Persisted completed duration calculation.
- Flooring fractional seconds.
- Protection against negative durations.
- Missing and invalid timestamps.
- Live elapsed calculation remaining based on the current clock.
- Completion-state selection of persisted duration over the current clock.

Add source-level regression assertions confirming that:

- Completed runs do not start the live interval in `applyRunState`.
- The interval is stopped before complete-state rendering.
- Both completion entry points pass a run record to the completion handler.

Run the focused red test before implementation, then run it green, followed by the full pytest suite, JavaScript tests, JavaScript syntax checks, and Python compile checks.

## Scope

The implementation is limited to the Research page timer helper, timer lifecycle wiring, and regression tests. It does not change backend pipeline execution, timestamps, polling frequency, stored research results, or the completed panel's visual design.
