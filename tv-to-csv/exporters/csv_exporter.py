from __future__ import annotations
import csv
from typing import IO
from exporters import register, BaseExporter
from models import Position, OrderLeg

_DT_FMT = '%Y-%m-%d %H:%M:%S'

FIELDS = [
    'placing_time',
    'broker',
    'symbol',
    'direction',
    'quantity',
    'leverage',
    'margin',
    'outcome',
    # Entry leg
    'entry_side_type',
    'entry_order_price',
    'entry_fill_price',
    'entry_time',
    'entry_status',
    # Stop Loss leg
    'sl_side_type',
    'sl_order_price',
    'sl_fill_price',
    'sl_time',
    'sl_status',
    # Take Profit leg
    'tp_side_type',
    'tp_order_price',
    'tp_fill_price',
    'tp_time',
    'tp_status',
    # Manual close leg (4-order positions)
    'close_side_type',
    'close_fill_price',
    'close_time',
    'close_status',
    # Summary
    'exit_time',
    'duration_seconds',
    'reconstruction',
    'notes',
]


def _leg(prefix: str, leg: OrderLeg | None) -> dict:
    if leg is None:
        return {
            f'{prefix}_side_type': '',
            f'{prefix}_order_price': '',
            f'{prefix}_fill_price': '',
            f'{prefix}_time': '',
            f'{prefix}_status': '',
        }
    return {
        f'{prefix}_side_type': f"{leg.side} {leg.order_type}",
        f'{prefix}_order_price': '' if leg.order_price is None else leg.order_price,
        f'{prefix}_fill_price': '' if leg.fill_price is None else leg.fill_price,
        f'{prefix}_time': leg.fill_time.strftime(_DT_FMT) if leg.fill_time else '',
        f'{prefix}_status': leg.status,
    }


def _close_leg(leg: OrderLeg | None) -> dict:
    if leg is None:
        return {'close_side_type': '', 'close_fill_price': '', 'close_time': '', 'close_status': ''}
    return {
        'close_side_type': f"{leg.side} {leg.order_type}",
        'close_fill_price': '' if leg.fill_price is None else leg.fill_price,
        'close_time': leg.fill_time.strftime(_DT_FMT) if leg.fill_time else '',
        'close_status': leg.status,
    }


@register('csv')
class CsvExporter(BaseExporter):
    def export(self, positions: list[Position], out: IO[str]) -> None:
        writer = csv.DictWriter(out, fieldnames=FIELDS, extrasaction='ignore',
                                lineterminator='\n')
        writer.writeheader()
        for pos in positions:
            row: dict = {
                'placing_time': pos.placing_time.strftime(_DT_FMT),
                'broker': pos.broker,
                'symbol': pos.symbol,
                'direction': pos.direction,
                'quantity': pos.quantity,
                'leverage': pos.leverage or '',
                'margin': pos.margin or '',
                'outcome': pos.outcome,
                **_leg('entry', pos.entry),
                **_leg('sl', pos.sl),
                **_leg('tp', pos.tp),
                **_close_leg(pos.close),
                'exit_time': pos.exit_time.strftime(_DT_FMT) if pos.exit_time else '',
                'duration_seconds': '' if pos.duration_seconds is None else int(pos.duration_seconds),
                'reconstruction': pos.reconstruction,
                'notes': pos.notes,
            }
            writer.writerow(row)
