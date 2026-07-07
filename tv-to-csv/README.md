# tv-to-csv

Reconstructs trading **positions** from a TradingView order-history export.
TradingView stores each position as two or more raw orders (entry + bracket).
This tool groups those orders back into one record per position, classifies the
outcome, and writes the result as CSV or JSON for import into a trade journal.

---

## Quick start

```
python main.py samples/order-history.txt                     # CSV to stdout
python main.py samples/order-history.txt -f json             # JSON to stdout
python main.py samples/order-history.txt -f csv -o out.csv   # CSV to file
```

Run `docker compose up -d` from the repo root and open `http://localhost:8080` —
drag-and-drop the raw `.txt` export to visualise positions without running Python.

---

## Files

```
tv-to-csv/
  order-history.txt        example export from TradingView
  models.py                RawOrder, OrderLeg, Position dataclasses
  order_parser.py          CSV parser (handles quoted Margin field)
  grouper.py               position reconstruction logic  ← core
  main.py                  CLI entry point
  exporters/
    __init__.py            plugin registry + BaseExporter ABC
    csv_exporter.py        @register('csv')
    json_exporter.py       @register('json')
../html/
  index.html               self-contained browser viewer (served via nginx)
../docker-compose.yml      spins up nginx:alpine on localhost:8080
```

---

## Input format

TradingView → Portfolio → History → Export CSV.  The file has a header row
followed by one order per line, 14 comma-separated columns:

| # | Column | Notes |
|---|--------|-------|
| 0 | Symbol | broker-prefixed: `OANDA:EURUSD` |
| 1 | Side | `Buy` or `Sell` |
| 2 | Type | `Market`, `Limit`, or `Stop` |
| 3 | Quantity | numeric |
| 4 | Limit price | blank for Stop/Market |
| 5 | Stop price | blank for Limit/Market |
| 6 | Fill price | blank when Cancelled |
| 7 | Status | `Filled` or `Cancelled` |
| 8 | Commission | usually blank |
| 9 | Placing time | `YYYY-MM-DD HH:MM:SS` |
| 10 | Closing time | fill/cancel timestamp |
| 11 | Order ID | numeric string, broker-assigned sequential |
| 12 | Leverage | e.g. `50:1` — **only on entry orders**; blank on SL/TP |
| 13 | Margin | e.g. `"4,883.64 USD"` — quoted because value contains a comma |

The parser pads short rows to 14 columns and uses `csv.reader` to handle the
quoted Margin field correctly.

---

## Output columns (CSV)

One row per reconstructed position.

| Column | Description |
|--------|-------------|
| `placing_time` | When the trade setup was submitted |
| `broker` | Prefix extracted from Symbol (e.g. `OANDA`) |
| `symbol` | Ticker without broker prefix (e.g. `EURUSD`) |
| `direction` | `Long`, `Short`, or `Unknown` |
| `quantity` | As in the source |
| `leverage` | From the entry order |
| `margin` | From the entry order |
| `outcome` | See Outcomes below |
| `entry_side_type` | e.g. `Buy Market` |
| `entry_order_price` | Intended price (stop or limit field) |
| `entry_fill_price` | Actual execution price |
| `entry_time` | Entry fill timestamp |
| `entry_status` | `Filled` / `Cancelled` |
| `sl_*` | Same five fields for the Stop Loss leg |
| `tp_*` | Same five fields for the Take Profit leg |
| `close_side_type` | Manual-close order type (4-order positions only) |
| `close_fill_price` | Manual-close fill price |
| `close_time` | Manual-close timestamp |
| `close_status` | Manual-close status |
| `exit_time` | When the position was exited (fill time of the exit leg) |
| `duration_seconds` | Seconds from entry fill to exit fill |
| `reconstruction` | `exact`, `proximity`, `partial`, or `unmatched` |
| `notes` | Warnings, missing legs, detection trail |

### Outcomes

| Value | Meaning |
|-------|---------|
| `TP_HIT` | Take-profit order filled |
| `SL_HIT` | Stop-loss order filled |
| `MANUAL_CLOSE` | Position closed by a separate market order (4-order structure) |
| `CANCELLED` | Entry order was never filled |
| `UNKNOWN` | Entry filled but no exit found in the export window |

### Reconstruction quality

| Value | Meaning |
|-------|---------|
| `exact` | All orders share the same `placing_time` — unambiguous |
| `proximity` | SL/TP linked by the 120-second time window (Market-entry case) |
| `partial` | Entry found but one or both bracket legs are missing |
| `unmatched` | Single order that could not be paired with anything |

---

## Adding an export format

Create `exporters/my_format.py`:

```python
from exporters import register, BaseExporter

@register('my_format')
class MyExporter(BaseExporter):
    def export(self, positions, out):
        ...
```

Add `import exporters.my_format` to `main.py`.  No other changes needed.

---

## Browser viewer

`html/index.html` is fully self-contained (no external dependencies).
It contains a JavaScript port of the complete parser and grouper logic.
Drop the raw TradingView export onto the page to see positions in a table.

Serve it locally with Docker:

```
docker compose up -d   # starts nginx:alpine at http://localhost:8080
docker compose down    # stop
```

Requires an external Docker network named `proxy-net` (`docker network create proxy-net`).

Features:
- Filter by outcome and direction
- Hide cancelled positions toggle
- Show/hide detail columns (broker, quantity, leverage, margin)
- Sortable Date and Qty columns
- Purple row tint and "Manual Close" badge for 4-order positions
- Click a row to highlight it with a blue outline (click again, or click another row, to move/clear it) — handy for tracking which position you're currently journaling
- Fixed table header: the table body scrolls in its own bounded box (`max-height` under the topbar/filters) so the header row stays visible

---

## Order matching algorithm

This section documents the reconstruction logic in enough detail to re-implement
it from scratch.

### Position structure

A TradingView bracket order creates **three raw orders** with identical
`placing_time`, `symbol`, and `quantity`:

```
Long position:
  Entry   Buy  Market/Limit/Stop   (opens the position)
  SL      Sell Stop                (closes at a loss)
  TP      Sell Limit               (closes at a profit)

Short position:
  Entry   Sell Market/Limit/Stop   (opens the position)
  SL      Buy  Stop                (closes at a loss)
  TP      Buy  Limit               (closes at a profit)
```

When the trader manually closes a position before SL or TP is hit, a **fourth
order** appears: a Market order on the opposite side placed at the moment of
closing.  The original SL and TP are cancelled simultaneously.

### Step 1 — Exact grouping

Group all raw orders by the composite key `(symbol, quantity, placing_time)`.
Exact same timestamp means the broker submitted these orders atomically as one
bracket.  Most positions are fully resolved in this step.

### Step 2 — Entry detection cascade

Within each group, identify which order is the entry using a cascade of methods.
Each method receives the current **candidate list** and returns a narrowed list,
or an empty list if it cannot distinguish.  The cascade applies methods in
priority order, updating the candidate set whenever a method narrows it.  The
first time the set reaches exactly one candidate, that is the entry.

Methods in priority order:

| Priority | Name | Logic |
|----------|------|-------|
| 1 | `minority_side` | In a bracket the entry is always the lone order on one side (1 Buy vs 2 Sells for Long, 1 Sell vs 2 Buys for Short).  The minority-side order is the entry.  Not applicable when all orders share the same side. |
| 2 | `market_type` | Market orders execute immediately and are always entries.  SL and TP are always Stop or Limit orders. |
| 3 | `lowest_order_id` | The broker assigns sequential IDs; the entry order is submitted first and holds the lowest ID in the group. |
| 4 | `leverage_field` | On margin platforms the leverage field is populated only on the order that opens the position.  SL/TP carry no leverage. |
| 5 | `earliest_fill` | Among filled orders, the entry always fills before the exit leg. |
| 6 | `margin_field` | Margin is consumed only by the entry order. |

The detection trail (which methods contributed a narrowing step) is stored in
the position's `notes` field, but omitted when the simplest method
(`minority_side` alone) was sufficient.

### Step 3 — SL / TP classification

Once the entry is known, the remaining orders are classified as SL or TP using
the bracket rules:

```
exit_side = 'Sell' if entry.side == 'Buy' else 'Buy'

SL = first order where side == exit_side AND type == 'Stop'
TP = first order where side == exit_side AND type == 'Limit'
```

Any order that does not match either rule goes into an `unclassified` list and
is recorded in `notes`.

### Step 4 — Proximity fallback for orphan entries

Some entries (typically Market orders) arrive with no matching SL/TP in the
same timestamp group because the bracket orders were submitted a few seconds
later.  These become **orphan entries**.

After all exact groups are processed, for each orphan entry, search the pool of
unmatched orders for SL/TP candidates satisfying:

- same `symbol` and `quantity`
- `placing_time` between `entry.placing_time` and `entry.placing_time + 120 s`
- not already consumed by a previous proximity match

Apply the same SL/TP classification logic to the found candidates.
The reconstruction field is set to `proximity`.

Orphan entries are processed in ascending `placing_time` order to ensure
deterministic matching when multiple entries compete for the same pool.

### Step 5 — Manual close detection

After Steps 1–4, some positions have `outcome == UNKNOWN` because the entry
filled but neither SL nor TP was hit.  Concurrently, some **partial** UNKNOWN
positions exist that consist of a single filled leg with no bracket — these are
the manual close orders.

Match them as follows:

1. **Close candidates**: positions where `reconstruction == 'partial'`,
   `outcome == 'UNKNOWN'`, entry has a fill price, and both `sl` and `tp` are
   absent.

2. **Open unknowns**: all other UNKNOWN positions where the entry has a fill
   price (not close candidates themselves).

3. For each open unknown, find the earliest close candidate satisfying:
   - same `symbol` and `quantity`
   - entry `side` == exit side of the open position (`Buy` for a Short,
     `Sell` for a Long)
   - close candidate's fill time ≥ open position's entry fill time

4. If a match is found: remove the close candidate from the position list,
   attach its entry leg as the `close` leg of the open position, and set
   `outcome = 'MANUAL_CLOSE'`.

### Step 6 — Remaining unmatched orders

Any order in the orphan pool that was not consumed by proximity matching or
manual-close detection is emitted as a standalone `unmatched / UNKNOWN` position
with a note describing the raw order.

### Outcome determination

```
if entry is absent or entry.status != 'Filled':  → CANCELLED
elif tp.status == 'Filled':                       → TP_HIT
elif sl.status == 'Filled':                       → SL_HIT
elif close leg is attached:                       → MANUAL_CLOSE  (set by Step 5)
else:                                             → UNKNOWN
```

### Known edge cases in the sample data

**NZDJPY 2026-06-29 13:47:22** — 4-order manual close.  A Sell Limit entry
fills at 91.472 and is closed 5 seconds later by a Buy Market at 91.496 (loss).
The original SL and TP are cancelled at the exact moment the close order fills.
The close order arrives in a separate timestamp group with no bracket of its own
and is detected by Step 5.

**NZDJPY 2026-06-29 13:47:50** — separate Short position placed 28 seconds
after the above.  Handled as an independent exact group; SL hit at 91.555.

**EURCAD 2026-06-23 07:08:17** — two qty=1 stub orders (quickly cancelled) share
a timestamp with a real qty=1351 position.  The stubs form their own exact group;
`minority_side` fails (both are Sell), so `lowest_order_id` picks one as entry.
The other becomes unmatched UNKNOWN.  The real position resolves cleanly in its
own group.

**AUDJPY 2026-06-22 21:40:xx** — four timestamp groups within 90 seconds:
a filled Long immediately closed manually (detected by Step 5), a fully-cancelled
pending entry, and a second Long that is hit by SL.  Each group is independent.
