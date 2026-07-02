"""Reconstruct trading positions from raw TradingView order history."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Callable, Optional
from models import RawOrder, OrderLeg, Position

PROXIMITY_WINDOW_SECONDS = 120


# ── Entry detection cascade ────────────────────────────────────────────────
#
# Each method receives the current candidate list and returns a narrowed list,
# or an empty list if the method is not applicable to those candidates.
# The cascade calls methods in priority order, updating the candidate set each
# time a method narrows it.  The first run that reaches exactly 1 candidate wins.

def _m_minority_side(candidates: list[RawOrder]) -> list[RawOrder]:
    """Entry is always the single order on the minority Side (1 Buy vs 2 Sells
    for a Long, or 1 Sell vs 2 Buys for a Short).  Not applicable when all
    orders share the same side, or when both sides are equally represented."""
    counts = Counter(o.side for o in candidates)
    if len(counts) < 2:
        return []  # all same side — method cannot distinguish
    min_count = min(counts.values())
    minority = [o for o in candidates if counts[o.side] == min_count]
    if len(minority) == len(candidates):
        return []  # perfect tie — method cannot distinguish
    return minority


def _m_market_type(candidates: list[RawOrder]) -> list[RawOrder]:
    """Market orders execute immediately and are always entries; SL and TP are
    always Stop or Limit orders.  Not applicable when no Market orders exist."""
    return [o for o in candidates if o.order_type == 'Market']


def _m_lowest_order_id(candidates: list[RawOrder]) -> list[RawOrder]:
    """The broker assigns sequential IDs; the entry order is submitted first and
    therefore holds the lowest ID within the bracket group."""
    try:
        min_id = min(int(o.order_id) for o in candidates)
        lowest = [o for o in candidates if int(o.order_id) == min_id]
        # Only narrows if it actually removes something from the candidate set
        return lowest if len(lowest) < len(candidates) else []
    except (ValueError, TypeError):
        return []


def _m_leverage_field(candidates: list[RawOrder]) -> list[RawOrder]:
    """On margin platforms the leverage setting is attached to the order that
    opens (and is charged against) the position.  SL/TP contingent orders carry
    no leverage.  Broker-specific: not applicable when the field is absent."""
    return [o for o in candidates if o.leverage]


def _m_earliest_fill(candidates: list[RawOrder]) -> list[RawOrder]:
    """The entry always fills before the exit leg (SL or TP).  Among filled
    orders, the one with the earliest closing_time is the entry.  Not applicable
    when no orders have filled."""
    filled = [o for o in candidates if o.status == 'Filled']
    if not filled:
        return []
    earliest = min(o.closing_time for o in filled)
    result = [o for o in filled if o.closing_time == earliest]
    return result if len(result) < len(candidates) else []


def _m_margin_field(candidates: list[RawOrder]) -> list[RawOrder]:
    """Margin is consumed only by the order that opens the position; it is not
    set on SL/TP orders.  Most specific but only present on filled entries."""
    return [o for o in candidates if o.margin]


# Ordered list of (name, function).  Add new methods here; order matters.
_DETECTION_METHODS: list[tuple[str, Callable[[list[RawOrder]], list[RawOrder]]]] = [
    ('minority_side',   _m_minority_side),
    ('market_type',     _m_market_type),
    ('lowest_order_id', _m_lowest_order_id),
    ('leverage_field',  _m_leverage_field),
    ('earliest_fill',   _m_earliest_fill),
    ('margin_field',    _m_margin_field),
]


def identify_entry(
    orders: list[RawOrder],
) -> tuple[Optional[RawOrder], list[str]]:
    """Cascade through detection methods, narrowing candidates at each step.

    Returns (entry_order, trail) where trail is the list of method names that
    each contributed a narrowing step.  Returns (None, trail) when the cascade
    exhausts all methods without converging to a single candidate.
    """
    candidates = orders[:]
    trail: list[str] = []

    for name, fn in _DETECTION_METHODS:
        narrowed = fn(candidates)
        if not narrowed:
            continue  # method not applicable to current candidates — skip
        if len(narrowed) < len(candidates):
            candidates = narrowed
            trail.append(name)
        if len(candidates) == 1:
            return candidates[0], trail

    return (candidates[0] if len(candidates) == 1 else None), trail


# ── SL / TP classification ─────────────────────────────────────────────────

def _classify_others(
    entry: RawOrder, others: list[RawOrder]
) -> tuple[Optional[RawOrder], Optional[RawOrder], list[RawOrder]]:
    """Split the non-entry orders into (sl, tp, unknown).

    For Long (Buy entry):   SL = Sell Stop,  TP = Sell Limit
    For Short (Sell entry): SL = Buy Stop,   TP = Buy Limit
    """
    exit_side = 'Sell' if entry.side == 'Buy' else 'Buy'
    sl: Optional[RawOrder] = None
    tp: Optional[RawOrder] = None
    unknown: list[RawOrder] = []

    for o in others:
        if o.side == exit_side and o.order_type == 'Stop':
            sl = o if sl is None else (unknown.append(o) or sl)
        elif o.side == exit_side and o.order_type == 'Limit':
            tp = o if tp is None else (unknown.append(o) or tp)
        else:
            unknown.append(o)

    return sl, tp, unknown


# ── Position building ──────────────────────────────────────────────────────

def _make_leg(order: RawOrder) -> OrderLeg:
    return OrderLeg(
        side=order.side,
        order_type=order.order_type,
        order_price=order.order_price,
        fill_price=order.fill_price,
        placing_time=order.placing_time,
        fill_time=order.closing_time if order.status == 'Filled' else None,
        status=order.status,
    )


def _outcome(
    entry: Optional[RawOrder],
    sl: Optional[RawOrder],
    tp: Optional[RawOrder],
) -> str:
    if entry is None or entry.status != 'Filled':
        return 'CANCELLED'
    if tp and tp.status == 'Filled':
        return 'TP_HIT'
    if sl and sl.status == 'Filled':
        return 'SL_HIT'
    return 'UNKNOWN'


def _build(
    entry: RawOrder,
    sl: Optional[RawOrder],
    tp: Optional[RawOrder],
    reconstruction: str,
    detection_trail: list[str],
    extra_notes: str = '',
) -> Position:
    entry_leg = _make_leg(entry)
    sl_leg    = _make_leg(sl) if sl else None
    tp_leg    = _make_leg(tp) if tp else None

    exit_leg: Optional[OrderLeg] = None
    if tp_leg and tp_leg.status == 'Filled':
        exit_leg = tp_leg
    elif sl_leg and sl_leg.status == 'Filled':
        exit_leg = sl_leg

    entry_time = entry_leg.fill_time
    exit_time  = exit_leg.fill_time if exit_leg else None
    duration   = (exit_time - entry_time).total_seconds() if entry_time and exit_time else None

    notes_parts: list[str] = []
    missing = [label for label, leg in (('SL', sl), ('TP', tp)) if leg is None]
    if missing:
        notes_parts.append(f"Missing {', '.join(missing)}")
    # Record detection trail only when more than the simplest case was needed
    if len(detection_trail) > 1 or (detection_trail and detection_trail[0] != 'minority_side'):
        notes_parts.append(f"entry via {' → '.join(detection_trail)}")
    if extra_notes:
        notes_parts.append(extra_notes)

    return Position(
        symbol=entry.clean_symbol,
        broker=entry.broker,
        direction='Long' if entry.side == 'Buy' else 'Short',
        quantity=entry.quantity,
        leverage=entry.leverage,
        margin=entry.margin,
        placing_time=entry.placing_time,
        entry=entry_leg,
        sl=sl_leg,
        tp=tp_leg,
        outcome=_outcome(entry, sl, tp),
        entry_time=entry_time,
        exit_time=exit_time,
        duration_seconds=duration,
        reconstruction=reconstruction,
        notes=' | '.join(notes_parts),
    )


def _build_unmatched(order: RawOrder) -> Position:
    return Position(
        symbol=order.clean_symbol,
        broker=order.broker,
        direction='Unknown',
        quantity=order.quantity,
        leverage=None,
        margin=None,
        placing_time=order.placing_time,
        entry=None,
        sl=None,
        tp=None,
        outcome='UNKNOWN',
        entry_time=None,
        exit_time=None,
        duration_seconds=None,
        reconstruction='unmatched',
        notes=f"Unmatched order: {order.side} {order.order_type} @ "
              f"{order.order_price or order.fill_price} ({order.status})",
    )


# ── Main grouping logic ────────────────────────────────────────────────────

def group_into_positions(orders: list[RawOrder]) -> list[Position]:
    # Step 1: exact groups — same symbol, quantity, and placing timestamp
    exact: dict[tuple, list[RawOrder]] = defaultdict(list)
    for o in orders:
        exact[(o.symbol, o.quantity, o.placing_time)].append(o)

    positions: list[Position] = []
    # Store as (entry, detection_trail) so the trail survives into proximity step
    orphan_entries: list[tuple[RawOrder, list[str]]] = []
    orphan_others:  list[RawOrder] = []

    for group in exact.values():
        entry, trail = identify_entry(group)

        if entry is None:
            # Cascade could not find an entry — all orders become orphans
            orphan_others.extend(group)
            continue

        others = [o for o in group if o is not entry]
        sl, tp, unknown = _classify_others(entry, others)

        if sl is None and tp is None:
            # Entry found but no exit legs in this group — try proximity later
            orphan_entries.append((entry, trail))
            orphan_others.extend(others)
            continue

        extra = ''
        if unknown:
            extra = f"Unclassified: {[(o.side, o.order_type, o.order_price) for o in unknown]}"
        rec = 'exact' if (sl and tp) else 'partial'
        positions.append(_build(entry, sl, tp, rec, trail, extra))

    # Step 2: proximity matching for orphan entries (e.g. Market orders whose
    # SL/TP arrive in a separate bracket a few seconds later)
    used: set[int] = set()

    for entry, trail in sorted(orphan_entries, key=lambda t: t[0].placing_time):
        window_end = entry.placing_time + timedelta(seconds=PROXIMITY_WINDOW_SECONDS)
        candidates = [
            o for o in orphan_others
            if o.symbol == entry.symbol
            and o.quantity == entry.quantity
            and entry.placing_time <= o.placing_time <= window_end
            and id(o) not in used
        ]

        sl, tp, unknown = _classify_others(entry, candidates)
        extra = ''
        if unknown:
            extra = f"Unclassified: {[(o.side, o.order_type, o.order_price) for o in unknown]}"

        rec = 'proximity' if candidates else 'partial'
        positions.append(_build(entry, sl, tp, rec, trail, extra))

        for o in [sl, tp, *unknown]:
            if o:
                used.add(id(o))

    # Step 3: emit any remaining unmatched orders
    for o in orphan_others:
        if id(o) not in used:
            positions.append(_build_unmatched(o))

    positions = _find_manual_closes(positions)
    return sorted(positions, key=lambda p: p.placing_time, reverse=True)


# ── Manual-close detection ─────────────────────────────────────────────────

def _find_manual_closes(positions: list[Position]) -> list[Position]:
    """Detect 4-order positions: entry + bracket (SL/TP) + manual market close.

    Pattern: position outcome is UNKNOWN (entry filled, neither SL nor TP hit),
    and a separate partial/UNKNOWN single-order position exists on the opposite
    side for the same symbol and quantity, with fill time >= entry fill time.
    That single-order position is the manual close; its entry leg becomes the
    position's 'close' leg and the outcome changes to MANUAL_CLOSE.
    """
    # Single-leg filled positions with no bracket — these are close-order candidates
    close_pool = [
        p for p in positions
        if (p.reconstruction == 'partial'
            and p.outcome == 'UNKNOWN'
            and p.entry is not None
            and p.entry.fill_price is not None
            and p.sl is None
            and p.tp is None)
    ]
    close_pool_ids = {id(p) for p in close_pool}

    # UNKNOWN positions with a filled entry that could have been manually closed
    open_unknowns = [
        p for p in positions
        if (p.outcome == 'UNKNOWN'
            and p.entry is not None
            and p.entry.fill_price is not None
            and id(p) not in close_pool_ids)
    ]

    consumed: set[int] = set()

    for pos in open_unknowns:
        close_side = 'Buy' if pos.direction == 'Short' else 'Sell'
        entry_fill = pos.entry.fill_time
        if entry_fill is None:
            continue

        # Pick the earliest qualifying close candidate after the entry fill
        match = min(
            (c for c in close_pool
             if id(c) not in consumed
             and c.symbol == pos.symbol
             and c.quantity == pos.quantity
             and c.entry.side == close_side
             and c.entry.fill_time is not None
             and c.entry.fill_time >= entry_fill),
            key=lambda c: c.entry.fill_time,
            default=None,
        )

        if match is None:
            continue

        consumed.add(id(match))
        pos.close = match.entry
        pos.outcome = 'MANUAL_CLOSE'
        pos.exit_time = match.entry.fill_time
        if entry_fill and match.entry.fill_time:
            pos.duration_seconds = (match.entry.fill_time - entry_fill).total_seconds()

    return [p for p in positions if id(p) not in consumed]
