from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
MS_PER_DAY = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Pivot:
    index: int
    timestamp: int
    kind: str  # "H" or "L"
    price: float


@dataclass(frozen=True)
class EntrySetup:
    index: int
    stop: float | None


@dataclass(frozen=True)
class Signal:
    index: int
    timestamp: int
    side: str  # "long" or "short"
    pattern: str
    entry: float
    stop: float
    target: float
    description: str


@dataclass(frozen=True)
class Trade:
    pattern: str
    side: str
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    stop: float
    target: float
    qty: float
    pnl: float
    return_pct: float
    r_multiple: float
    reason: str


def parse_time(value: str | None, default: dt.datetime) -> int:
    if not value:
        return int(default.timestamp() * 1000)
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


def iso_ms(timestamp: int) -> str:
    return dt.datetime.fromtimestamp(timestamp / 1000, tz=dt.timezone.utc).strftime(
        "%Y-%m-%d %H:%M"
    )


def fetch_binance_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    sleep_seconds: float = 0.2,
) -> list[Candle]:
    candles: list[Candle] = []
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        with urllib.request.urlopen(f"{BINANCE_KLINES_URL}?{query}", timeout=30) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
        if not rows:
            break
        for row in rows:
            candles.append(
                Candle(
                    timestamp=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        next_cursor = int(rows[-1][0]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(sleep_seconds)
    return candles


def read_csv(path: Path) -> list[Candle]:
    candles: list[Candle] = []
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw_time = row.get("timestamp") or row.get("time") or row.get("date")
            if raw_time is None:
                raise ValueError("CSV needs a timestamp/time/date column.")
            if raw_time.isdigit():
                timestamp = int(raw_time)
                if timestamp < 10_000_000_000:
                    timestamp *= 1000
            else:
                timestamp = parse_time(raw_time, dt.datetime.now(dt.timezone.utc))
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    candles.sort(key=lambda c: c.timestamp)
    return candles


def write_candles(path: Path, candles: Iterable[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            writer.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume])


def find_zigzag_pivots(candles: list[Candle], reversal_pct: float) -> list[Pivot]:
    if len(candles) < 3:
        return []

    threshold = reversal_pct / 100.0
    pivots: list[Pivot] = []
    candidate_high_i = candidate_low_i = 0
    trend: str | None = None

    for i, candle in enumerate(candles[1:], start=1):
        if candle.high >= candles[candidate_high_i].high:
            candidate_high_i = i
        if candle.low <= candles[candidate_low_i].low:
            candidate_low_i = i

        if trend is None:
            up_move = candle.high / candles[candidate_low_i].low - 1.0
            down_move = 1.0 - candle.low / candles[candidate_high_i].high
            if up_move >= threshold:
                low = candles[candidate_low_i]
                pivots.append(Pivot(candidate_low_i, low.timestamp, "L", low.low))
                trend = "up"
                candidate_high_i = i
            elif down_move >= threshold:
                high = candles[candidate_high_i]
                pivots.append(Pivot(candidate_high_i, high.timestamp, "H", high.high))
                trend = "down"
                candidate_low_i = i
            continue

        if trend == "up":
            if candle.high >= candles[candidate_high_i].high:
                candidate_high_i = i
            drawdown = 1.0 - candle.low / candles[candidate_high_i].high
            if drawdown >= threshold:
                high = candles[candidate_high_i]
                if not pivots or pivots[-1].index != candidate_high_i:
                    pivots.append(Pivot(candidate_high_i, high.timestamp, "H", high.high))
                trend = "down"
                candidate_low_i = i
        else:
            if candle.low <= candles[candidate_low_i].low:
                candidate_low_i = i
            rebound = candle.high / candles[candidate_low_i].low - 1.0
            if rebound >= threshold:
                low = candles[candidate_low_i]
                if not pivots or pivots[-1].index != candidate_low_i:
                    pivots.append(Pivot(candidate_low_i, low.timestamp, "L", low.low))
                trend = "up"
                candidate_high_i = i

    return pivots


def avg_volume(candles: list[Candle], index: int, lookback: int) -> float:
    start = max(0, index - lookback)
    volumes = [c.volume for c in candles[start:index]]
    return statistics.fmean(volumes) if volumes else candles[index].volume


def avg_body(candles: list[Candle], index: int, lookback: int) -> float:
    start = max(0, index - lookback)
    bodies = [abs(c.close - c.open) for c in candles[start:index]]
    return statistics.fmean(bodies) if bodies else abs(candles[index].close - candles[index].open)


def structural_stop(
    candles: list[Candle],
    breakout_index: int,
    side: str,
    mode: str,
    lookback: int,
    body_lookback: int,
    impulse_body_mult: float,
    stop_buffer_pct: float,
    entry_price: float | None = None,
) -> float | None:
    entry = entry_price if entry_price is not None else candles[breakout_index].close
    start = max(0, breakout_index - lookback)
    prior_range = candles[start:breakout_index]
    if not prior_range:
        return None

    buffer = stop_buffer_pct / 100
    breakout = candles[breakout_index]
    body = abs(breakout.close - breakout.open)
    long_body = body >= avg_body(candles, breakout_index, body_lookback) * impulse_body_mult
    candidates: list[float] = []

    if side == "long":
        range_low = min(c.low for c in prior_range) * (1 - buffer)
        if mode in {"range", "tightest"}:
            candidates.append(range_low)
        if mode in {"impulse", "tightest"} and breakout.close > breakout.open and long_body:
            candidates.append(breakout.low * (1 - buffer))
        valid = [stop for stop in candidates if stop < entry]
        return max(valid) if valid else None

    range_high = max(c.high for c in prior_range) * (1 + buffer)
    if mode in {"range", "tightest"}:
        candidates.append(range_high)
    if mode in {"impulse", "tightest"} and breakout.close < breakout.open and long_body:
        candidates.append(breakout.high * (1 + buffer))
    valid = [stop for stop in candidates if stop > entry]
    return min(valid) if valid else None


def reward_r(entry: float, target: float, stop: float) -> float:
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return reward / risk if risk else 0


def passes_entry_rr(entry: float, target: float, stop: float, min_entry_rr: float) -> bool:
    return reward_r(entry, target, stop) >= min_entry_rr


def find_breakout(
    candles: list[Candle],
    start_index: int,
    level: float,
    side: str,
    max_wait: int,
    volume_lookback: int,
    volume_mult: float,
) -> int | None:
    end = min(len(candles), start_index + max_wait + 1)
    for i in range(start_index + 1, end):
        c = candles[i]
        has_price_break = c.close > level if side == "long" else c.close < level
        has_volume = c.volume >= avg_volume(candles, i, volume_lookback) * volume_mult
        if has_price_break and has_volume:
            return i
    return None


def find_retest_entry(
    candles: list[Candle],
    breakout_index: int,
    level: float,
    side: str,
    retest_wait: int,
    retest_tolerance_pct: float,
) -> int | None:
    tolerance = retest_tolerance_pct / 100
    end = min(len(candles), breakout_index + retest_wait + 1)
    for i in range(breakout_index + 1, end):
        c = candles[i]
        if side == "long":
            touches_level = c.low <= level * (1 + tolerance)
            confirms = c.close > level and c.close > c.open
        else:
            touches_level = c.high >= level * (1 - tolerance)
            confirms = c.close < level and c.close < c.open
        if touches_level and confirms:
            return i
    return None


def find_retest_break_entry(
    candles: list[Candle],
    breakout_index: int,
    level: float,
    side: str,
    retest_wait: int,
    retest_tolerance_pct: float,
    body_lookback: int,
    impulse_body_mult: float,
) -> EntrySetup | None:
    breakout = candles[breakout_index]
    breakout_body = abs(breakout.close - breakout.open)
    long_body = breakout_body >= avg_body(candles, breakout_index, body_lookback) * impulse_body_mult
    if side == "long" and not (breakout.close > breakout.open and long_body):
        return None
    if side == "short" and not (breakout.close < breakout.open and long_body):
        return None

    tolerance = retest_tolerance_pct / 100
    end = min(len(candles), breakout_index + retest_wait + 1)
    retest_found = False
    retest_stop = math.inf if side == "long" else -math.inf
    guard = breakout.low if side == "long" else breakout.high

    for i in range(breakout_index + 1, end):
        c = candles[i]
        prev = candles[i - 1]

        if side == "long":
            if c.low < guard:
                return None
            if retest_found and c.close > prev.high and c.close > level:
                return EntrySetup(i, retest_stop)
            if c.low <= level * (1 + tolerance):
                retest_found = True
                retest_stop = min(retest_stop, c.low)
        else:
            if c.high > guard:
                return None
            if retest_found and c.close < prev.low and c.close < level:
                return EntrySetup(i, retest_stop)
            if c.high >= level * (1 - tolerance):
                retest_found = True
                retest_stop = max(retest_stop, c.high)

    return None


def resolve_entry_setup(
    candles: list[Candle],
    breakout_index: int,
    level: float,
    side: str,
    entry_mode: str,
    retest_wait: int,
    retest_tolerance_pct: float,
    body_lookback: int,
    impulse_body_mult: float,
) -> EntrySetup | None:
    if entry_mode == "breakout":
        return EntrySetup(breakout_index, None)
    if entry_mode == "retest_break":
        return find_retest_break_entry(
            candles,
            breakout_index,
            level,
            side,
            retest_wait,
            retest_tolerance_pct,
            body_lookback,
            impulse_body_mult,
        )
    entry_i = find_retest_entry(
        candles,
        breakout_index,
        level,
        side,
        retest_wait,
        retest_tolerance_pct,
    )
    return EntrySetup(entry_i, None) if entry_i is not None else None


def detect_signals(
    candles: list[Candle],
    pivots: list[Pivot],
    low_tolerance_pct: float,
    high_tolerance_pct: float,
    min_height_pct: float,
    max_wait: int,
    volume_lookback: int,
    volume_mult: float,
    stop_buffer_pct: float,
    target_mult: float,
    stop_mode: str,
    stop_lookback: int,
    body_lookback: int,
    impulse_body_mult: float,
    min_entry_rr: float,
    entry_mode: str,
    retest_wait: int,
    retest_tolerance_pct: float,
) -> list[Signal]:
    signals: list[Signal] = []
    seen: set[tuple[str, int]] = set()

    for a, b, c in zip(pivots, pivots[1:], pivots[2:]):
        kinds = a.kind + b.kind + c.kind
        if kinds == "LHL":
            neckline = b.price
            height = neckline - min(a.price, c.price)
            if height / neckline < min_height_pct / 100:
                continue

            lows_are_close = abs(c.price / a.price - 1.0) <= low_tolerance_pct / 100
            higher_low = c.price > a.price and (c.price / a.price - 1.0) <= 10 * low_tolerance_pct / 100
            breakout_i = find_breakout(
                candles, c.index, neckline, "long", max_wait, volume_lookback, volume_mult
            )
            entry_setup = (
                resolve_entry_setup(
                    candles,
                    breakout_i,
                    neckline,
                    "long",
                    entry_mode,
                    retest_wait,
                    retest_tolerance_pct,
                    body_lookback,
                    impulse_body_mult,
                )
                if breakout_i is not None
                else None
            )
            entry_i = entry_setup.index if entry_setup is not None else None
            if entry_i is not None and lows_are_close and ("W_BOTTOM", entry_i) not in seen:
                entry = candles[entry_i].close
                if entry_setup.stop is not None:
                    stop = entry_setup.stop
                elif stop_mode == "classic":
                    stop = c.price * (1 - stop_buffer_pct / 100)
                else:
                    stop = structural_stop(
                        candles,
                        breakout_i,
                        "long",
                        stop_mode,
                        stop_lookback,
                        body_lookback,
                        impulse_body_mult,
                        stop_buffer_pct,
                        entry,
                    )
                if stop is None:
                    continue
                target = entry + height * target_mult
                if stop < entry < target and passes_entry_rr(entry, target, stop, min_entry_rr):
                    signals.append(
                        Signal(
                            entry_i,
                            candles[entry_i].timestamp,
                            "long",
                            "W_BOTTOM",
                            entry,
                            stop,
                            target,
                            "L-H-L lows near each other, close breaks neckline with volume.",
                        )
                    )
                    seen.add(("W_BOTTOM", entry_i))

            if entry_i is not None and higher_low and ("N_LONG", entry_i) not in seen:
                entry = candles[entry_i].close
                if entry_setup.stop is not None:
                    stop = entry_setup.stop
                elif stop_mode == "classic":
                    stop = c.price * (1 - stop_buffer_pct / 100)
                else:
                    stop = structural_stop(
                        candles,
                        breakout_i,
                        "long",
                        stop_mode,
                        stop_lookback,
                        body_lookback,
                        impulse_body_mult,
                        stop_buffer_pct,
                        entry,
                    )
                if stop is None:
                    continue
                target = c.price + (b.price - a.price) * target_mult
                if stop < entry < target and passes_entry_rr(entry, target, stop, min_entry_rr):
                    signals.append(
                        Signal(
                            entry_i,
                            candles[entry_i].timestamp,
                            "long",
                            "N_LONG",
                            entry,
                            stop,
                            target,
                            "L-H-L higher low, close breaks B with volume.",
                        )
                    )
                    seen.add(("N_LONG", entry_i))

        elif kinds == "HLH":
            neckline = b.price
            height = max(a.price, c.price) - neckline
            if height / neckline < min_height_pct / 100:
                continue

            highs_are_close = abs(c.price / a.price - 1.0) <= high_tolerance_pct / 100
            lower_high = c.price < a.price and (a.price / c.price - 1.0) <= 10 * high_tolerance_pct / 100
            breakdown_i = find_breakout(
                candles, c.index, neckline, "short", max_wait, volume_lookback, volume_mult
            )
            entry_setup = (
                resolve_entry_setup(
                    candles,
                    breakdown_i,
                    neckline,
                    "short",
                    entry_mode,
                    retest_wait,
                    retest_tolerance_pct,
                    body_lookback,
                    impulse_body_mult,
                )
                if breakdown_i is not None
                else None
            )
            entry_i = entry_setup.index if entry_setup is not None else None
            if entry_i is not None and highs_are_close and ("M_TOP", entry_i) not in seen:
                entry = candles[entry_i].close
                if entry_setup.stop is not None:
                    stop = entry_setup.stop
                elif stop_mode == "classic":
                    stop = c.price * (1 + stop_buffer_pct / 100)
                else:
                    stop = structural_stop(
                        candles,
                        breakdown_i,
                        "short",
                        stop_mode,
                        stop_lookback,
                        body_lookback,
                        impulse_body_mult,
                        stop_buffer_pct,
                        entry,
                    )
                if stop is None:
                    continue
                target = entry - height * target_mult
                if target < entry < stop and passes_entry_rr(entry, target, stop, min_entry_rr):
                    signals.append(
                        Signal(
                            entry_i,
                            candles[entry_i].timestamp,
                            "short",
                            "M_TOP",
                            entry,
                            stop,
                            target,
                            "H-L-H highs near each other, close breaks neckline with volume.",
                        )
                    )
                    seen.add(("M_TOP", entry_i))

            if entry_i is not None and lower_high and ("N_SHORT", entry_i) not in seen:
                entry = candles[entry_i].close
                if entry_setup.stop is not None:
                    stop = entry_setup.stop
                elif stop_mode == "classic":
                    stop = c.price * (1 + stop_buffer_pct / 100)
                else:
                    stop = structural_stop(
                        candles,
                        breakdown_i,
                        "short",
                        stop_mode,
                        stop_lookback,
                        body_lookback,
                        impulse_body_mult,
                        stop_buffer_pct,
                        entry,
                    )
                if stop is None:
                    continue
                target = c.price - (a.price - b.price) * target_mult
                if target < entry < stop and passes_entry_rr(entry, target, stop, min_entry_rr):
                    signals.append(
                        Signal(
                            entry_i,
                            candles[entry_i].timestamp,
                            "short",
                            "N_SHORT",
                            entry,
                            stop,
                            target,
                            "H-L-H lower high, close breaks B with volume.",
                        )
                    )
                    seen.add(("N_SHORT", entry_i))

    return sorted(signals, key=lambda s: s.index)


def backtest(
    candles: list[Candle],
    signals: list[Signal],
    initial_cash: float,
    risk_pct: float,
    sizing_mode: str,
    position_pct: float,
    fee_bps: float,
) -> tuple[list[Trade], float]:
    cash = initial_cash
    fee_rate = fee_bps / 10_000
    trades: list[Trade] = []
    next_allowed_index = 0

    for signal in signals:
        if signal.index <= next_allowed_index or cash <= 0:
            continue
        risk_per_unit = abs(signal.entry - signal.stop)
        if risk_per_unit <= 0:
            continue
        if sizing_mode == "risk":
            qty = (cash * risk_pct / 100) / risk_per_unit
        else:
            qty = (cash * position_pct / 100) / signal.entry
        exit_price = candles[-1].close
        exit_index = len(candles) - 1
        reason = "end"

        for i in range(signal.index + 1, len(candles)):
            c = candles[i]
            if signal.side == "long":
                if c.low <= signal.stop:
                    exit_price = signal.stop
                    exit_index = i
                    reason = "stop"
                    break
                if c.high >= signal.target:
                    exit_price = signal.target
                    exit_index = i
                    reason = "target"
                    break
            else:
                if c.high >= signal.stop:
                    exit_price = signal.stop
                    exit_index = i
                    reason = "stop"
                    break
                if c.low <= signal.target:
                    exit_price = signal.target
                    exit_index = i
                    reason = "target"
                    break

        gross = (exit_price - signal.entry) * qty
        if signal.side == "short":
            gross *= -1
        fees = (signal.entry * qty + exit_price * qty) * fee_rate
        pnl = gross - fees
        cash += pnl
        r_multiple = pnl / (risk_per_unit * qty) if qty else 0
        trades.append(
            Trade(
                signal.pattern,
                signal.side,
                signal.timestamp,
                candles[exit_index].timestamp,
                signal.entry,
                exit_price,
                signal.stop,
                signal.target,
                qty,
                pnl,
                pnl / max(cash - pnl, 1) * 100,
                r_multiple,
                reason,
            )
        )
        next_allowed_index = exit_index
    return trades, cash


def summarize(trades: list[Trade], initial_cash: float, final_cash: float) -> dict[str, float | int | str]:
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "initial_cash": round(initial_cash, 2),
        "final_cash": round(final_cash, 2),
        "net_return_pct": round((final_cash / initial_cash - 1) * 100, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else "inf",
        "avg_r": round(statistics.fmean([t.r_multiple for t in trades]), 3) if trades else 0,
    }


def write_trades(path: Path, trades: list[Trade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "pattern",
                "side",
                "entry_time",
                "exit_time",
                "entry",
                "exit",
                "stop",
                "target",
                "qty",
                "pnl",
                "return_pct",
                "r_multiple",
                "reason",
            ]
        )
        for t in trades:
            writer.writerow(
                [
                    t.pattern,
                    t.side,
                    iso_ms(t.entry_time),
                    iso_ms(t.exit_time),
                    round(t.entry, 2),
                    round(t.exit, 2),
                    round(t.stop, 2),
                    round(t.target, 2),
                    round(t.qty, 8),
                    round(t.pnl, 2),
                    round(t.return_pct, 3),
                    round(t.r_multiple, 3),
                    t.reason,
                ]
            )


def make_svg(path: Path, candles: list[Candle], signals: list[Signal], trades: list[Trade], bars: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = candles[-bars:] if len(candles) > bars else candles
    if not sample:
        return
    start_i = len(candles) - len(sample)
    width, height = 1280, 720
    pad_l, pad_r, pad_t, pad_b = 70, 40, 55, 75
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    lo = min(c.low for c in sample)
    hi = max(c.high for c in sample)
    span = hi - lo or 1

    def x_for(i: int) -> float:
        if len(sample) == 1:
            return pad_l
        return pad_l + (i - start_i) / (len(sample) - 1) * plot_w

    def y_for(price: float) -> float:
        return pad_t + (hi - price) / span * plot_h

    points = " ".join(f"{x_for(i):.1f},{y_for(c.close):.1f}" for i, c in enumerate(candles[start_i:], start=start_i))
    trade_by_entry = {t.entry_time: t for t in trades}
    signal_shapes = []
    for s in signals:
        if s.index < start_i:
            continue
        x = x_for(s.index)
        y = y_for(s.entry)
        color = "#16a34a" if s.side == "long" else "#dc2626"
        shape = "polygon" if s.side == "long" else "rect"
        if shape == "polygon":
            signal_shapes.append(f'<polygon points="{x},{y-9} {x-9},{y+9} {x+9},{y+9}" fill="{color}"/>')
        else:
            signal_shapes.append(f'<rect x="{x-8}" y="{y-8}" width="16" height="16" fill="{color}"/>')
        if s.timestamp in trade_by_entry:
            t = trade_by_entry[s.timestamp]
            signal_shapes.append(
                f'<text x="{x+10:.1f}" y="{y-10:.1f}" font-size="14" font-family="Arial" fill="#111827">{s.pattern} {t.r_multiple:.2f}R</text>'
            )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f6f7f9"/>
  <rect x="{pad_l}" y="{pad_t}" width="{plot_w}" height="{plot_h}" fill="#ffffff" stroke="#c9cdd4"/>
  <text x="{pad_l}" y="34" font-size="24" font-family="Arial" font-weight="700" fill="#111827">Pattern backtest signals</text>
  <text x="{pad_l}" y="{height-28}" font-size="16" font-family="Arial" fill="#4b5563">Green triangle = long entry, red square = short entry. Last {len(sample)} candles only.</text>
  <polyline points="{points}" fill="none" stroke="#111827" stroke-width="2"/>
  <text x="10" y="{y_for(hi)+5:.1f}" font-size="14" font-family="Arial" fill="#4b5563">{hi:.0f}</text>
  <text x="10" y="{y_for(lo)+5:.1f}" font-size="14" font-family="Arial" fill="#4b5563">{lo:.0f}</text>
  {''.join(signal_shapes)}
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="BTC pattern backtest: W/M/N via ZigZag pivots.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--start", default=None, help="UTC ISO date, e.g. 2025-01-01")
    parser.add_argument("--end", default=None, help="UTC ISO date, default now")
    parser.add_argument("--csv", type=Path, default=None, help="Use local OHLCV CSV instead of fetching Binance.")
    parser.add_argument("--data-out", type=Path, default=None)
    parser.add_argument("--trades-out", type=Path, default=None)
    parser.add_argument("--chart-out", type=Path, default=None)
    parser.add_argument("--initial-cash", type=float, default=10_000)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument(
        "--sizing-mode",
        choices=["risk", "notional"],
        default="risk",
        help="risk sizes each trade to lose risk-pct at the stop; notional uses a fixed position-pct allocation.",
    )
    parser.add_argument("--position-pct", type=float, default=100.0)
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--zigzag-pct", type=float, default=8.0)
    parser.add_argument("--tolerance-pct", type=float, default=1.5)
    parser.add_argument("--min-height-pct", type=float, default=2.0)
    parser.add_argument("--max-wait", type=int, default=72)
    parser.add_argument("--volume-lookback", type=int, default=20)
    parser.add_argument("--volume-mult", type=float, default=1.3)
    parser.add_argument("--stop-buffer-pct", type=float, default=0.2)
    parser.add_argument(
        "--stop-mode",
        choices=["classic", "range", "impulse", "retest", "tightest"],
        default="retest",
        help="classic uses the pattern pivot; range uses prior consolidation; impulse uses the breakout long candle low / breakdown long candle high; retest uses the retest low/high when entry-mode is retest_break; tightest uses the closest valid structural stop.",
    )
    parser.add_argument("--stop-lookback", type=int, default=60)
    parser.add_argument("--body-lookback", type=int, default=20)
    parser.add_argument("--impulse-body-mult", type=float, default=1.5)
    parser.add_argument(
        "--min-entry-rr",
        type=float,
        default=2.0,
        help="Only enter when measured target reward / stop risk is at least this value.",
    )
    parser.add_argument(
        "--entry-mode",
        choices=["breakout", "retest", "retest_break"],
        default="retest_break",
        help="breakout enters on the breakout close; retest waits for a neckline retest and confirming candle; retest_break waits for a retest that holds the breakout candle low/high, then enters when price closes beyond the prior candle high/low.",
    )
    parser.add_argument("--retest-wait", type=int, default=12)
    parser.add_argument("--retest-tolerance-pct", type=float, default=1.5)
    parser.add_argument("--target-mult", type=float, default=1.0)
    parser.add_argument("--chart-bars", type=int, default=700)
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    default_start = now - dt.timedelta(days=365)
    start_ms = parse_time(args.start, default_start)
    end_ms = parse_time(args.end, now)

    symbol_slug = args.symbol.lower()
    interval_slug = args.interval.lower()
    data_out = args.data_out or Path(f"data/{symbol_slug}_{interval_slug}.csv")
    trades_out = args.trades_out or Path(f"reports/trades_{interval_slug}.csv")
    chart_out = args.chart_out or Path(f"reports/signals_{interval_slug}.svg")

    if args.csv:
        candles = read_csv(args.csv)
    else:
        candles = fetch_binance_klines(args.symbol, args.interval, start_ms, end_ms)
        write_candles(data_out, candles)
    candles = [c for c in candles if start_ms <= c.timestamp <= end_ms]

    if len(candles) < 100:
        raise SystemExit(f"Need more candles; got {len(candles)}.")

    pivots = find_zigzag_pivots(candles, args.zigzag_pct)
    signals = detect_signals(
        candles,
        pivots,
        args.tolerance_pct,
        args.tolerance_pct,
        args.min_height_pct,
        args.max_wait,
        args.volume_lookback,
        args.volume_mult,
        args.stop_buffer_pct,
        args.target_mult,
        args.stop_mode,
        args.stop_lookback,
        args.body_lookback,
        args.impulse_body_mult,
        args.min_entry_rr,
        args.entry_mode,
        args.retest_wait,
        args.retest_tolerance_pct,
    )
    trades, final_cash = backtest(
        candles,
        signals,
        args.initial_cash,
        args.risk_pct,
        args.sizing_mode,
        args.position_pct,
        args.fee_bps,
    )
    summary = summarize(trades, args.initial_cash, final_cash)
    write_trades(trades_out, trades)
    make_svg(chart_out, candles, signals, trades, args.chart_bars)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"candles: {len(candles)}")
    print(f"pivots: {len(pivots)}")
    print(f"signals: {len(signals)}")
    print(f"trades_csv: {trades_out}")
    print(f"chart_svg: {chart_out}")


if __name__ == "__main__":
    main()
