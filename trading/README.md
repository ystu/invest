# BTC Pattern Backtest

This is a first-pass backtest for a BTC spread/trend pattern idea:

- W bottom: `L-H-L` pivots with similar lows, then a close above the neckline.
- M top: `H-L-H` pivots with similar highs, then a close below the neckline.
- N long: `L-H-L` pivots with a higher low, then a close above the prior swing high.
- N short: `H-L-H` pivots with a lower high, then a close below the prior swing low.

The detector uses ZigZag-style pivots, volume confirmation, a pattern invalidation stop, and a measured-move target.

## Run

```powershell
python .\backtest_patterns.py --symbol BTCUSDT --interval 4h --start 2025-01-01
```

Outputs:

- `data/btcusdt_4h.csv`: downloaded OHLCV candles
- `reports/trades_4h.csv`: trade list
- `reports/signals_4h.svg`: visual signal chart for the latest bars

## Useful Parameters

```powershell
python .\backtest_patterns.py `
  --start 2025-01-01 `
  --interval 4h `
  --zigzag-pct 8 `
  --stop-mode retest `
  --tolerance-pct 1.5 `
  --volume-mult 1.3 `
  --entry-mode retest_break `
  --retest-wait 12 `
  --retest-tolerance-pct 1.5 `
  --min-entry-rr 2 `
  --risk-pct 1 `
  --fee-bps 4
```

Notes:

- `--zigzag-pct`: bigger values find larger swings and fewer trades.
- `--tolerance-pct`: controls how close W-bottom lows or M-top highs must be.
- `--volume-mult`: requires breakout volume to exceed recent average volume.
- `--stop-mode`: `classic` uses the pattern pivot; `range` uses the prior consolidation edge; `impulse` uses the breakout long candle low / breakdown long candle high; `retest` uses the retest low/high when `--entry-mode retest_break`; `tightest` uses the closest valid structural stop.
- `--stop-lookback`: number of candles used for the prior consolidation range.
- `--min-entry-rr`: only enter when the measured-move target is at least this reward-to-risk multiple.
- `--entry-mode`: `breakout` enters immediately on the breakout close; `retest` waits for a neckline retest and confirming candle; `retest_break` waits for a retest that holds the breakout candle low/high, then enters when price closes beyond the prior candle high/low.
- `--risk-pct`: account risk per trade, sized from entry to stop.
- `--sizing-mode`: `risk` keeps each stop near `risk-pct`; `notional` uses a fixed allocation so tighter stops reduce the dollar loss.
- `--position-pct`: fixed allocation used when `--sizing-mode notional`.
- `--csv path/to/file.csv`: use a local OHLCV CSV with columns `timestamp,open,high,low,close,volume`.

This is research code, not trading advice. Use it to pressure-test rules before considering live execution.
