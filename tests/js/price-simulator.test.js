const test = require("node:test");
const assert = require("node:assert/strict");

const {
  clampAdjustment,
  getCurrencyPrice,
  resolveSimulatorHoldings,
  calculateSimulation,
  reconcileAdjustments,
  createSelectionSaveCoordinator,
} = require("../../static/js/price-simulator.js");


const holdings = [
  {
    key: "stock:AAA",
    current_value_usd: 1000,
    current_value_cad: 1250,
    current_price_usd: 100,
    current_price_cad: 125,
    current_price_php: 5000,
  },
  {
    key: "crypto:bitcoin",
    current_value_usd: 400,
    current_value_cad: 500,
    current_price_usd: 20,
    current_price_cad: 25,
    current_price_php: 1000,
  },
];


test("clamps percentage adjustments to -100% and +500%", () => {
  assert.equal(clampAdjustment(-101), -100);
  assert.equal(clampAdjustment(0), 0);
  assert.equal(clampAdjustment(501), 500);
});


test("selects unit price in the active currency and treats missing prices as zero", () => {
  assert.equal(getCurrencyPrice(holdings[0], "USD"), 100);
  assert.equal(getCurrencyPrice(holdings[0], "CAD"), 125);
  assert.equal(getCurrencyPrice(holdings[0], "PHP"), 5000);
  assert.equal(getCurrencyPrice({}, "USD"), 0);
});


test("resolves selected holdings in saved order and omits stale keys", () => {
  const result = resolveSimulatorHoldings(holdings, [
    "crypto:bitcoin",
    "stock:missing",
    "stock:AAA",
  ]);

  assert.deepEqual(
    result.map((holding) => holding.key),
    ["crypto:bitcoin", "stock:AAA"],
  );
});


test("calculates -100%, 0%, and +500% prices and selected portfolio impact", () => {
  const result = calculateSimulation({
    baseTotal: 2000,
    holdings,
    adjustments: {
      "stock:AAA": 500,
      "crypto:bitcoin": -100,
    },
    currency: "USD",
  });

  assert.equal(result.rows[0].simulatedUnitPrice, 600);
  assert.equal(result.rows[1].simulatedUnitPrice, 0);
  assert.equal(result.delta, 4600);
  assert.equal(result.simulatedTotal, 6600);
  assert.ok(Math.abs(result.deltaPercent - 230) < Number.EPSILON * 256);
});


test("reconciles adjustments across swap, remove, re-add, and currency refresh", () => {
  assert.deepEqual(
    reconcileAdjustments(
      { "stock:AAA": 25, "crypto:bitcoin": 50 },
      ["stock:AAA", "crypto:bitcoin"],
      ["crypto:bitcoin", "stock:NEW"],
    ),
    { "crypto:bitcoin": 50, "stock:NEW": 0 },
  );

  assert.deepEqual(
    reconcileAdjustments(
      { "crypto:bitcoin": 50 },
      ["crypto:bitcoin"],
      ["crypto:bitcoin"],
    ),
    { "crypto:bitcoin": 50 },
  );
});


function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}


test("save coordinator serializes requests and coalesces to the latest desired selection", async () => {
  const requests = [];
  const states = [];
  const first = deferred();
  const second = deferred();
  const save = (keys) => {
    requests.push([...keys]);
    return requests.length === 1 ? first.promise : second.promise;
  };
  const coordinator = createSelectionSaveCoordinator({
    initialKeys: ["stock:AAA"],
    save,
    onState: (state) => states.push(state),
  });

  coordinator.setDesired(["crypto:bitcoin"]);
  coordinator.setDesired(["stock:AAA", "crypto:bitcoin"]);
  assert.deepEqual(requests, [["crypto:bitcoin"]]);

  first.resolve({ holding_keys: ["crypto:bitcoin"] });
  await first.promise;
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests, [
    ["crypto:bitcoin"],
    ["stock:AAA", "crypto:bitcoin"],
  ]);

  second.resolve({ holding_keys: ["stock:AAA", "crypto:bitcoin"] });
  await second.promise;
  await coordinator.whenIdle();
  assert.deepEqual(coordinator.getState(), {
    confirmed: ["stock:AAA", "crypto:bitcoin"],
    desired: ["stock:AAA", "crypto:bitcoin"],
    status: "saved",
  });
  assert.equal(states.at(-1).status, "saved");
});


test("save coordinator rolls back and clears queued edits after failure", async () => {
  const request = deferred();
  const coordinator = createSelectionSaveCoordinator({
    initialKeys: ["stock:AAA"],
    save: () => request.promise,
    onState: () => {},
  });

  coordinator.setDesired(["crypto:bitcoin"]);
  coordinator.setDesired(["stock:AAA", "crypto:bitcoin"]);
  request.reject(new Error("network down"));
  await assert.rejects(request.promise, /network down/);
  await coordinator.whenIdle();

  assert.deepEqual(coordinator.getState(), {
    confirmed: ["stock:AAA"],
    desired: ["stock:AAA"],
    status: "error",
  });
});
