"""Convert TradingView order history to reconstructed position records.

Usage:
    python main.py order-history.txt
    python main.py order-history.txt -f json -o positions.json
    python main.py order-history.txt -f csv  -o positions.csv
"""
from __future__ import annotations
import argparse
import sys

from order_parser import parse_orders
from grouper import group_into_positions

# Import exporters to trigger their @register decorators
import exporters.csv_exporter   # noqa: F401
import exporters.json_exporter  # noqa: F401

from exporters import get_exporter, available_formats
from models import Position


def _summary(positions: list[Position]) -> str:
    total        = len(positions)
    tp_hit       = sum(1 for p in positions if p.outcome == 'TP_HIT')
    sl_hit       = sum(1 for p in positions if p.outcome == 'SL_HIT')
    manual_close = sum(1 for p in positions if p.outcome == 'MANUAL_CLOSE')
    cancelled    = sum(1 for p in positions if p.outcome == 'CANCELLED')
    unknown      = sum(1 for p in positions if p.outcome == 'UNKNOWN')
    flagged      = sum(1 for p in positions if p.notes)
    return (
        f"Positions: {total} total | "
        f"{tp_hit} TP hit | {sl_hit} SL hit | {manual_close} manual close | "
        f"{cancelled} cancelled | {unknown} unknown | "
        f"{flagged} with notes"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description='Reconstruct positions from TradingView order history.')
    ap.add_argument('input', help='Order history CSV exported from TradingView')
    ap.add_argument('-o', '--output', default='-',
                    help='Output file path (default: stdout)')
    ap.add_argument('-f', '--format', default='csv',
                    choices=available_formats(),
                    help='Output format (default: csv)')
    args = ap.parse_args()

    orders = parse_orders(args.input)
    positions = group_into_positions(orders)
    exporter = get_exporter(args.format)

    if args.output == '-':
        exporter.export(positions, sys.stdout)
    else:
        with open(args.output, 'w', newline='', encoding='utf-8') as fh:
            exporter.export(positions, fh)
        print(f"Written to {args.output}", file=sys.stderr)

    print(_summary(positions), file=sys.stderr)


if __name__ == '__main__':
    main()
