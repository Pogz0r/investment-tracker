(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.PriceSimulator = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MIN_ADJUSTMENT = -100;
  const MAX_ADJUSTMENT = 500;

  function clampAdjustment(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 0;
    return Math.min(MAX_ADJUSTMENT, Math.max(MIN_ADJUSTMENT, numeric));
  }

  function getCurrencyPrice(holding, currency) {
    const suffix = String(currency || "USD").toLowerCase();
    const price = Number(holding?.[`current_price_${suffix}`] || 0);
    return Number.isFinite(price) ? price : 0;
  }

  function getCurrencyValue(holding, currency) {
    const suffix = String(currency || "USD").toLowerCase();
    const value = Number(holding?.[`current_value_${suffix}`] || 0);
    return Number.isFinite(value) ? value : 0;
  }

  function resolveSimulatorHoldings(holdings, selectedKeys) {
    const byKey = new Map((holdings || []).map((holding) => [holding.key, holding]));
    return (selectedKeys || []).map((key) => byKey.get(key)).filter(Boolean);
  }

  function calculateSimulation({ baseTotal, holdings, adjustments, currency }) {
    let delta = 0;
    const rows = (holdings || []).map((holding) => {
      const adjustment = clampAdjustment(adjustments?.[holding.key] ?? 0);
      const multiplier = (100 + adjustment) / 100;
      const currentUnitPrice = getCurrencyPrice(holding, currency);
      const currentValue = getCurrencyValue(holding, currency);
      const simulatedUnitPrice = currentUnitPrice * multiplier;
      const simulatedValue = currentValue * multiplier;
      delta += simulatedValue - currentValue;
      return {
        key: holding.key,
        adjustment,
        currentUnitPrice,
        simulatedUnitPrice,
        currentValue,
        simulatedValue,
      };
    });
    const numericBase = Number(baseTotal) || 0;
    return {
      rows,
      delta,
      simulatedTotal: numericBase + delta,
      deltaPercent: numericBase > 0 ? (delta / numericBase) * 100 : 0,
    };
  }

  function reconcileAdjustments(adjustments, previousKeys, nextKeys) {
    const previous = new Set(previousKeys || []);
    const next = {};
    for (const key of nextKeys || []) {
      next[key] = previous.has(key) && Object.hasOwn(adjustments || {}, key)
        ? clampAdjustment(adjustments[key])
        : 0;
    }
    return next;
  }

  function sameKeys(left, right) {
    return left.length === right.length && left.every((key, index) => key === right[index]);
  }

  function createSelectionSaveCoordinator({ initialKeys, save, onState, onError = () => {} }) {
    let confirmed = [...(initialKeys || [])];
    let desired = [...confirmed];
    let status = "saved";
    let inFlight = null;
    let idleResolvers = [];

    function snapshot() {
      return { confirmed: [...confirmed], desired: [...desired], status };
    }

    function emit() {
      onState(snapshot());
    }

    function resolveIdle() {
      if (inFlight) return;
      const resolvers = idleResolvers;
      idleResolvers = [];
      resolvers.forEach((resolve) => resolve());
    }

    function pump() {
      if (inFlight) return;
      if (sameKeys(desired, confirmed)) {
        if (status !== "error") status = "saved";
        emit();
        resolveIdle();
        return;
      }

      const requestKeys = [...desired];
      status = "saving";
      emit();

      let request;
      try {
        request = save(requestKeys);
      } catch (error) {
        desired = [...confirmed];
        status = "error";
        onError(error);
        emit();
        resolveIdle();
        return;
      }

      inFlight = Promise.resolve(request);
      inFlight.then((response) => {
        confirmed = [...response.holding_keys];
        inFlight = null;
        pump();
      }).catch((error) => {
        desired = [...confirmed];
        inFlight = null;
        status = "error";
        onError(error);
        emit();
        resolveIdle();
      });
    }

    return {
      setDesired(keys) {
        desired = [...keys];
        pump();
      },
      getState: snapshot,
      whenIdle() {
        if (!inFlight && (status === "error" || sameKeys(desired, confirmed))) {
          return Promise.resolve();
        }
        return new Promise((resolve) => idleResolvers.push(resolve));
      },
    };
  }

  return {
    MIN_ADJUSTMENT,
    MAX_ADJUSTMENT,
    clampAdjustment,
    getCurrencyPrice,
    resolveSimulatorHoldings,
    calculateSimulation,
    reconcileAdjustments,
    createSelectionSaveCoordinator,
  };
});
