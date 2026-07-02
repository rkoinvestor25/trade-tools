from __future__ import annotations
import csv
from datetime import datetime
from typing import Optional
from models import RawOrder

_DT_FORMAT = '%Y-%m-%d %H:%M:%S'


def _float(s: str) -> Optional[float]:
    s = s.strip().strip('"').replace(',', '')
    return float(s) if s else None


def _str(s: str) -> Optional[str]:
    s = s.strip().strip('"')
    return s if s else None


def _dt(s: str) -> datetime:
    return datetime.strptime(s.strip(), _DT_FORMAT)


def parse_orders(filepath: str) -> list[RawOrder]:
    orders: list[RawOrder] = []
    with open(filepath, newline='', encoding='utf-8') as fh:
        reader = csv.reader(fh)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 13:
                continue
            # Pad to 14 columns so Margin is always present
            row = (row + [''] * 14)[:14]
            (symbol, side, order_type, quantity, limit_price, stop_price,
             fill_price, status, commission, placing_time, closing_time,
             order_id, leverage, margin) = row

            orders.append(RawOrder(
                symbol=symbol.strip(),
                side=side.strip(),
                order_type=order_type.strip(),
                quantity=float(quantity.strip().replace(',', '')),
                limit_price=_float(limit_price),
                stop_price=_float(stop_price),
                fill_price=_float(fill_price),
                status=status.strip(),
                commission=_str(commission),
                placing_time=_dt(placing_time),
                closing_time=_dt(closing_time),
                order_id=order_id.strip(),
                leverage=_str(leverage),
                margin=_str(margin),
            ))
    return orders
