(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.ResearchTimer = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function timestamp(value) {
    if (value instanceof Date) return value.getTime();
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value !== "string" || !value.trim()) return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function calculateDurationSeconds(startedAt, completedAt) {
    const start = timestamp(startedAt);
    const end = timestamp(completedAt);
    if (start === null || end === null || end < start) return null;
    return Math.floor((end - start) / 1000);
  }

  function calculateLiveElapsedSeconds(startedAt, now = Date.now()) {
    const start = timestamp(startedAt);
    const end = timestamp(now);
    if (start === null || end === null) return 0;
    return Math.max(0, Math.floor((end - start) / 1000));
  }

  function shouldRunLiveTimer(status) {
    return status === "queued" || status === "running";
  }

  function sameRun(left, right) {
    return left !== null && left !== undefined
      && right !== null && right !== undefined
      && String(left) === String(right);
  }

  function createCompletionCoordinator({
    stopPolling,
    stopElapsedTimer,
    closeEventSource,
    getFallbackSeconds,
    renderComplete,
  }) {
    let currentRunId = null;
    let completionBegun = false;
    let fallbackSeconds = 0;
    let rendered = false;
    let renderedPersisted = false;

    function reset(runId) {
      currentRunId = runId;
      completionBegun = false;
      fallbackSeconds = 0;
      rendered = false;
      renderedPersisted = false;
    }

    function beginCompletion(originatingRunId) {
      if (!sameRun(originatingRunId, currentRunId)) return false;
      if (completionBegun) return true;
      completionBegun = true;
      stopPolling();
      stopElapsedTimer();
      closeEventSource();
      const fallback = Number(getFallbackSeconds());
      fallbackSeconds = Number.isFinite(fallback) ? Math.max(0, Math.floor(fallback)) : 0;
      return true;
    }

    function finishCompletion(originatingRunId, runOrNull) {
      if (!sameRun(originatingRunId, currentRunId)) return false;
      if (!completionBegun && !beginCompletion(originatingRunId)) return false;

      if (runOrNull && !sameRun(runOrNull.id, originatingRunId)) return false;
      const persistedSeconds = runOrNull
        ? calculateDurationSeconds(runOrNull.started_at, runOrNull.completed_at)
        : null;

      if (persistedSeconds !== null) {
        if (renderedPersisted) return false;
        renderComplete(persistedSeconds);
        rendered = true;
        renderedPersisted = true;
        return true;
      }

      if (rendered || renderedPersisted) return false;
      renderComplete(fallbackSeconds);
      rendered = true;
      return true;
    }

    return { reset, beginCompletion, finishCompletion };
  }

  return {
    calculateDurationSeconds,
    calculateLiveElapsedSeconds,
    shouldRunLiveTimer,
    createCompletionCoordinator,
  };
});
