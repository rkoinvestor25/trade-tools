from __future__ import annotations
import json
from typing import IO, Any
from exporters import register, BaseExporter
from models import Position, OrderLeg


def _leg(leg: OrderLeg | None) -> dict | None:
    if leg is None:
        return None
    return {
        'side_type': f"{leg.side} {leg.order_type}",
        'order_price': leg.order_price,
        'fill_price': leg.fill_price,
        'time': leg.fill_time.isoformat() if leg.fill_time else None,
        'status': leg.status,
    }


def _pos(pos: Position) -> dict[str, Any]:
    return {
        'placing_time': pos.placing_time.isoformat(),
        'broker': pos.broker,
        'symbol': pos.symbol,
        'direction': pos.direction,
        'quantity': pos.quantity,
        'leverage': pos.leverage,
        'margin': pos.margin,
        'outcome': pos.outcome,
        'entry': _leg(pos.entry),
        'sl': _leg(pos.sl),
        'tp': _leg(pos.tp),
        'close': _leg(pos.close),
        'exit_time': pos.exit_time.isoformat() if pos.exit_time else None,
        'duration_seconds': int(pos.duration_seconds) if pos.duration_seconds is not None else None,
        'reconstruction': pos.reconstruction,
        'notes': pos.notes,
    }


@register('json')
class JsonExporter(BaseExporter):
    def export(self, positions: list[Position], out: IO[str]) -> None:
        json.dump([_pos(p) for p in positions], out, indent=2)
        out.write('\n')
