from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawOrder:
    symbol: str
    side: str           # Buy / Sell
    order_type: str     # Market / Limit / Stop
    quantity: float
    limit_price: Optional[float]
    stop_price: Optional[float]
    fill_price: Optional[float]
    status: str         # Filled / Cancelled
    commission: Optional[str]
    placing_time: datetime
    closing_time: datetime
    order_id: str
    leverage: Optional[str]
    margin: Optional[str]

    @property
    def broker(self) -> str:
        return self.symbol.split(':')[0] if ':' in self.symbol else ''

    @property
    def clean_symbol(self) -> str:
        return self.symbol.split(':')[-1]

    @property
    def is_entry(self) -> bool:
        """Entry orders always have leverage set; SL/TP orders do not."""
        return bool(self.leverage)

    @property
    def order_price(self) -> Optional[float]:
        """Intended trigger price: stop_price takes precedence over limit_price."""
        return self.stop_price if self.stop_price is not None else self.limit_price


@dataclass
class OrderLeg:
    """One leg of a reconstructed position (entry, SL, or TP)."""
    side: str
    order_type: str
    order_price: Optional[float]   # intended price (stop/limit)
    fill_price: Optional[float]    # actual execution price (slippage visible here)
    placing_time: datetime
    fill_time: Optional[datetime]  # None when Cancelled
    status: str                    # Filled / Cancelled


@dataclass
class Position:
    # Identity
    symbol: str
    broker: str
    direction: str          # Long / Short / Unknown
    quantity: float
    leverage: Optional[str]
    margin: Optional[str]
    placing_time: datetime  # when the trade setup was submitted

    # Legs
    entry: Optional[OrderLeg]
    sl: Optional[OrderLeg]
    tp: Optional[OrderLeg]

    # Outcome
    # TP_HIT / SL_HIT / CANCELLED / UNKNOWN / MANUAL_CLOSE
    outcome: str

    # Timing
    entry_time: Optional[datetime]
    exit_time: Optional[datetime]
    duration_seconds: Optional[float]

    # Reconstruction quality
    # exact      – all 3 orders share the same placing_time
    # proximity  – SL/TP linked by time window (Market entry case)
    # partial    – entry found but SL or TP missing
    reconstruction: str
    notes: str = ''
    close: Optional[OrderLeg] = None  # set when position was closed by a market order (4-order structure)
