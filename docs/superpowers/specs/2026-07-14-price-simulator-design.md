# Price Simulator Enhancements Design

## Context

The portfolio homepage includes a collapsible Price Simulator. It currently selects the ten largest stock and cryptocurrency holdings automatically, displays simulated holding values, and limits slider gains to 200%. Users cannot choose which holdings appear, and the simulator does not show the actual per-unit market price that each percentage move modifies.

The approved design keeps the simulator compact and consistent with the dashboard's dark, industrial visual language while adding direct row-level editing.

## Goals

- Display the actual current per-unit price and simulated per-unit price for each selected stock or cryptocurrency.
- Extend every simulator slider to support moves from -100% through +500% in one-percent steps.
- Let each signed-in user choose and order up to ten portfolio holdings in the simulator.
- Persist those choices per user across browsers, devices, and sessions.
- Preserve the existing portfolio-level simulated value and estimated-change summaries.
- Keep the interface compact and responsive.

## Non-goals

- Persisting slider percentage adjustments between page loads.
- Allowing more than ten simulator rows.
- Adding liquid cash to the simulator.
- Changing portfolio valuation, market-price providers, refresh timing, or holding-management flows.
- Adding a separate settings page or selection modal.

## Approved Interaction Design

### Simulator rows

Each row contains four functional regions:

1. A holding selector styled as the existing ticker badge.
2. A percentage slider with a -100% minimum, +500% maximum, and 1% step.
3. A compact `current unit price -> simulated unit price` readout in the active dashboard currency.
4. A remove button for deleting the row from the saved selection.

The percentage remains visible beside the slider. The row does not display total holding value; portfolio-level impact remains in the two summary panels above the rows.

### Quiet selector treatment

At rest, the holding selector looks like a normal ticker badge and does not show standard select-box chrome. Hovering or keyboard-focusing only the holding-name region reveals a subtle amber outline and dropdown chevron. Clicking anywhere in that region opens the selector. On touch devices, where hover is unavailable, the chevron remains faintly visible so the control stays discoverable.

Already-selected holdings are disabled in other row selectors. Changing a row selection retains that row's position, resets its price adjustment to 0%, and saves the new ordered selection immediately.

### Adding, removing, and resetting

- An `Add holding` control appears when fewer than ten holdings are selected and at least one unselected stock or cryptocurrency exists.
- Adding creates a row for the next available unselected holding and persists the ordered selection.
- Removing a row persists the remaining ordered selection.
- A small count/status label reports `N / 10 selected` plus `Saving...`, `Saved`, or an error state.
- `Reset prices` returns every slider to 0% without changing or resaving the selected holdings.

### Responsive behavior

Desktop uses a single compact row. At narrower widths, the holding control stays on the first line while the slider and price readout wrap into clearly labeled regions. Touch targets remain at least 44 CSS pixels where practical, and all functionality remains available by keyboard.

## Persistence Architecture

### Model

Add a `PriceSimulatorSettings` SQLAlchemy model with:

- `id`: primary key.
- `user_id`: required, unique foreign key to `User`.
- `holding_keys`: required JSON list, defaulting to an empty list.

Holding keys use the existing canonical format:

- Stock: `stock:<ticker>`
- Cryptocurrency: `crypto:<coin_id>`

A separate table avoids altering the existing user table and allows `db.create_all()` to create the structure through the application's established startup process.

### Effective selection

The backend resolves saved keys against holdings owned by the requesting user. Missing or deleted holdings are omitted. The effective list preserves saved order, contains no duplicates, and never exceeds ten items.

If a user has no settings row, the effective default is the ten highest-value stock/crypto holdings in descending current market value. Merely viewing this default does not create a settings row. The first user edit persists the submitted ordered list.

### API contract

`GET /api/portfolio` adds `price_simulator_holding_keys`, containing the effective ordered selection.

`PUT /api/price-simulator/holdings` accepts:

```json
{
  "holding_keys": ["crypto:bitcoin", "stock:NVDA"]
}
```

The endpoint requires authentication and returns the validated ordered list:

```json
{
  "holding_keys": ["crypto:bitcoin", "stock:NVDA"]
}
```

Validation rules:

- `holding_keys` must be a JSON array of strings.
- The array may contain zero through ten keys.
- Keys must be unique.
- Every key must identify a stock or cryptocurrency currently owned by the signed-in user.
- Keys owned by a different user or using an unsupported type are rejected.

Malformed input returns HTTP 400. Unknown or unowned holdings return HTTP 400 without revealing whether another user owns the key. Authentication failures follow the application's existing login behavior.

## Market-price Data

The portfolio payload adds normalized per-unit price fields for every stock and cryptocurrency:

- `current_price_usd`
- `current_price_cad`
- `current_price_php`

Existing `current_price` fields remain unchanged for compatibility with current holding tables. The simulator selects the normalized field matching the active dashboard currency. The simulated per-unit price is:

```text
current unit price * (100 + percentage adjustment) / 100
```

The existing simulated portfolio calculation continues to apply the same percentage adjustment to the selected holding's current total value. Cash and unselected holdings remain unchanged.

## Frontend Units and Data Flow

### Pure simulator helpers

A small standalone JavaScript unit owns pure operations that can be tested without the browser:

- Resolve the ordered effective simulator holdings from portfolio data and saved keys.
- Select the correct per-unit price for the active currency.
- Calculate simulated unit price, holding-value delta, portfolio total, and percentage change.
- Enforce slider bounds when consuming adjustment values.

It has no DOM, network, or storage dependencies.

### Simulator view/controller

The existing dashboard script owns rendering and event wiring:

- Renders rows from the effective selected keys.
- Maintains temporary percentage adjustments keyed by holding key.
- Updates row prices and summary totals on slider input.
- Handles row selector, add, remove, and reset actions.
- Calls the persistence endpoint when selection changes.
- Re-renders prices when the dashboard currency changes or fresh portfolio data arrives.

### Save behavior

Selection edits update the UI optimistically and enter a `Saving...` state. Only one save is allowed in flight; later edits are serialized so an older response cannot overwrite a newer selection. On success, the returned list becomes the saved baseline and the status changes to `Saved`. On failure, the selection returns to the last confirmed baseline and an inline error explains that the change could not be saved. The user's slider adjustments for unaffected rows remain intact.

## Error and Edge-case Handling

- No stock or crypto holdings: show the existing empty-state guidance and no add control.
- Fewer than ten holdings: default to all available holdings.
- Saved keys referencing removed holdings: omit them without breaking rendering; the next successful edit persists the cleaned list.
- Market price unavailable or zero: show an unavailable price marker and keep the row at zero portfolio contribution, following existing payload semantics.
- Rapid selection edits: serialize saves and render only the latest requested selection.
- Currency change: preserve selected keys and percentage adjustments while recalculating displayed prices and values.
- Duplicate labels: canonical keys, not labels, determine identity.
- Keyboard use: the visually quiet selector exposes a proper label, focus treatment, and native selection behavior.

## Testing Strategy

### Backend tests

Add focused pytest coverage for:

- Unauthenticated update rejection.
- Default top-ten effective selection for users without settings.
- Successful ordered selection persistence.
- Selection retrieval through the portfolio payload.
- Empty selection persistence.
- Rejection of malformed bodies, non-string keys, duplicates, more than ten keys, unsupported key types, and holdings not owned by the user.
- Isolation between two users.
- Safe omission of a saved holding after that holding is deleted.
- Normalized USD, CAD, and PHP unit prices for both stock and cryptocurrency payload items.

External market and exchange-rate calls are mocked so tests are deterministic.

### JavaScript tests

Use Node's built-in test runner, avoiding a new package dependency, to cover:

- Effective holding resolution and ordering.
- Active-currency unit-price selection.
- -100%, 0%, and +500% simulated prices.
- Portfolio delta calculations with selected and unselected holdings.
- Slider-bound clamping.
- Missing-price behavior.

### Integration and verification

- Add template-level assertions for the selector, add/remove/reset controls, accessible labels, and -100%/+500% bounds.
- Manually verify desktop, narrow viewport, hover, keyboard focus, touch affordance, currency switching, and save-failure rollback.
- Run the focused red test before implementation, then rerun it green.
- Run the complete pytest suite.
- Run JavaScript unit tests.
- Run Python compile checks as this Flask project has no asset compilation build step.

## Delivery

Implementation stays limited to the simulator model/API, portfolio payload price normalization, simulator markup/styles/script, and their tests. The approved design will be committed in its own documentation phase, followed by a conventional feature commit for implementation. After all checks pass, the working branch will be pushed to the configured GitHub origin.
