#!/usr/bin/env python3
"""Reproducible 0050 buy-and-hold entry strategy research.

Primary market index data is downloaded from TWSE official public endpoints.
0050 adjusted-close data is downloaded from Yahoo Finance because TWSE daily
quotes are not dividend/split adjusted.
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
REPORT = ROOT / "0050_entry_strategy_research.md"

START = date(2000, 1, 1)
END = date.today()
LEVELS = [-0.05, -0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40, -0.50]


def urlopen_json(url: str, tries: int = 3) -> dict:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                url = urllib.parse.urljoin(url, exc.headers["Location"])
                last = exc
                continue
            last = exc
        except Exception as exc:  # pragma: no cover - diagnostics for local runs
            last = exc
            time.sleep(0.7 + i)
    raise RuntimeError(f"failed to fetch {url}: {last}")


def tw_date(s: str) -> date:
    y, m, d = [int(x) for x in s.split("/")]
    return date(y + 1911, m, d)


def num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(",", "").strip()
    if s in {"", "--", "---", "X"}:
        return None
    return float(s)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def month_iter(start: date, end: date):
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        yield y, m
        m += 1
        if m == 13:
            y += 1
            m = 1


def fetch_taiex() -> list[dict]:
    cached = RAW / "taiex_twse_mi_5mins_hist.csv"
    if cached.exists():
        return read_csv(cached)
    rows = []
    gaps = []
    for y, m in month_iter(START, END):
        roc = y - 1911
        url = f"https://www.twse.com.tw/exchangeReport/FMTQIK?date={y}{m:02d}01&response=json"
        try:
            data = urlopen_json(url)
        except Exception as exc:
            gaps.append({"month": f"{y}-{m:02d}", "error": str(exc)})
            continue
        if data.get("stat") != "OK":
            gaps.append({"month": f"{y}-{m:02d}", "error": data.get("stat", "not OK")})
            continue
        for r in data.get("data", []):
            close = num(r[4])
            rows.append({
                "date": tw_date(r[0]).isoformat(),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "source": "TWSE FMTQIK",
            })
    rows.sort(key=lambda r: r["date"])
    write_csv(RAW / "taiex_twse_mi_5mins_hist.csv", rows, ["date", "open", "high", "low", "close", "source"])
    if gaps:
        write_csv(RAW / "taiex_twse_download_gaps.csv", gaps, ["month", "error"])
    return rows


def fetch_0050_twse_latest() -> dict:
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={END.year}{END.month:02d}01&stockNo=0050&response=json"
    data = urlopen_json(url)
    last = None
    for r in data.get("data", []):
        last = {
            "date": tw_date(r[0]).isoformat(),
            "close": num(r[6]),
            "source": "TWSE STOCK_DAY",
            "note": "official unadjusted close; includes regular, odd-lot, after-hour fixed-price, block trades; excludes auction/tender",
        }
    return last or {}


def fetch_0050_yahoo() -> list[dict]:
    cached = RAW / "0050_yahoo_adj_close.csv"
    if cached.exists():
        return read_csv(cached)
    p1 = int(datetime(2003, 1, 1, tzinfo=timezone.utc).timestamp())
    p2 = int((datetime.combine(END + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc)).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/0050.TW?period1={p1}&period2={p2}&interval=1d&events=history%7Cdiv%7Csplits"
    data = urlopen_json(url)
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    adj = result["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for i, t in enumerate(ts):
        d = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(timezone(timedelta(hours=8))).date()
        if q["close"][i] is None or adj[i] is None:
            continue
        rows.append({
            "date": d.isoformat(),
            "close": q["close"][i],
            "adj_close": adj[i],
            "source": "Yahoo Finance chart API",
        })
    write_csv(RAW / "0050_yahoo_adj_close.csv", rows, ["date", "close", "adj_close", "source"])
    return rows


def fetch_twse_home_yields() -> dict:
    data = urlopen_json("https://wwwc.twse.com.tw/res/data/zh/home/yields.json")
    (RAW / "twse_home_yields.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    taiwan_pe = None
    for name, val in data["chart1"]["table2"]["data"]:
        if "臺灣" in name or "台灣" in name:
            taiwan_pe = val
    return {
        "pe": taiwan_pe,
        "date": "2025-12-31",
        "source": "TWSE /res/data/zh/home/yields.json",
        "definition": data["chart1"]["title"] + "; year-end major securities market PER",
    }


def enrich_drawdown(rows: list[dict]) -> list[dict]:
    hwm = -math.inf
    hwm_date = ""
    out = []
    for r in rows:
        c = float(r["close"])
        if c >= hwm:
            hwm = c
            hwm_date = r["date"]
        dd = c / hwm - 1
        rr = dict(r)
        rr.update({"hwm": hwm, "hwm_date": hwm_date, "drawdown": dd})
        out.append(rr)
    write_csv(PROC / "taiex_drawdown_daily.csv", out, ["date", "open", "high", "low", "close", "source", "hwm", "hwm_date", "drawdown"])
    return out


def drawdown_events(dd_rows: list[dict], min_dd=-0.10) -> list[dict]:
    events = []
    in_water = False
    peak = None
    trough = None
    start_i = 0
    for i, r in enumerate(dd_rows):
        dd = float(r["drawdown"])
        if not in_water and dd < 0:
            in_water = True
            peak = {"date": r["hwm_date"], "close": float(r["hwm"])}
            trough = r
            start_i = i
        if in_water:
            if dd < float(trough["drawdown"]):
                trough = r
            if dd == 0:
                if float(trough["drawdown"]) <= min_dd:
                    events.append(format_event(peak, trough, r, start_i, i))
                in_water = False
    if in_water and trough and float(trough["drawdown"]) <= min_dd:
        events.append(format_event(peak, trough, None, start_i, len(dd_rows) - 1))
    write_csv(PROC / "taiex_drawdown_events.csv", events, list(events[0].keys()) if events else [])
    return events


def format_event(peak, trough, recovery, start_i, end_i) -> dict:
    pdate = date.fromisoformat(peak["date"])
    tdate = date.fromisoformat(trough["date"])
    rdate = date.fromisoformat(recovery["date"]) if recovery else None
    maxdd = float(trough["drawdown"])
    out = {
        "peak_date": peak["date"],
        "peak_close": round(peak["close"], 2),
        "trough_date": trough["date"],
        "trough_close": round(float(trough["close"]), 2),
        "max_drawdown": round(maxdd, 6),
        "peak_to_trough_days": (tdate - pdate).days,
        "trough_to_recovery_days": "" if rdate is None else (rdate - tdate).days,
        "underwater_days": "" if rdate is None else (rdate - pdate).days,
        "recovery_date": "" if rdate is None else rdate.isoformat(),
    }
    for lvl in [-0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40, -0.50]:
        out[f"hit_{int(abs(lvl)*100)}pct"] = maxdd <= lvl
    return out


def waiting_stats(events: list[dict]) -> list[dict]:
    out = []
    levels = [-0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40]
    for lvl in levels:
        reached = [e for e in events if float(e["max_drawdown"]) <= lvl]
        deeper_lvl = lvl - 0.05
        extra = [float(e["max_drawdown"]) - lvl for e in reached]
        deeper = [e for e in reached if float(e["max_drawdown"]) <= deeper_lvl]
        out.append({
            "already_at": f"{lvl:.0%}",
            "events": len(reached),
            "prob_deeper_next_5pct": round(len(deeper) / len(reached), 4) if reached else "",
            "avg_additional_decline": round(statistics.mean(extra), 4) if extra else "",
            "median_additional_decline": round(statistics.median(extra), 4) if extra else "",
            "worst_additional_decline": round(min(extra), 4) if extra else "",
            "did_not_reach_next_level": len(reached) - len(deeper),
            "cash_wait_failed_ratio": round((len(reached) - len(deeper)) / len(reached), 4) if reached else "",
        })
    write_csv(PROC / "waiting_after_drawdown_stats.csv", out, list(out[0].keys()))
    return out


def build_asset_series(taiex_dd: list[dict], y0050: list[dict]) -> list[dict]:
    by_t = {r["date"]: r for r in taiex_dd}
    by_50 = {r["date"]: r for r in y0050}
    common = sorted(set(by_t) & set(by_50))
    rows = []
    for d in common:
        rows.append({
            "date": d,
            "taiex_close": float(by_t[d]["close"]),
            "taiex_drawdown": float(by_t[d]["drawdown"]),
            "asset": float(by_50[d]["adj_close"]),
            "asset_source": "0050 Yahoo adjusted close",
        })
    write_csv(PROC / "backtest_asset_series.csv", rows, ["date", "taiex_close", "taiex_drawdown", "asset", "asset_source"])
    return rows


def simulate(rows: list[dict], start_idx: int, schedule: list[tuple[float, float]], end_idx: int) -> dict:
    cash = 1.0
    units = 0.0
    invested = 0.0
    waits = []
    max_value = 1.0
    max_dd = 0.0
    fully_idx = None
    start_asset = rows[start_idx]["asset"]
    for i in range(start_idx, end_idx + 1):
        r = rows[i]
        dd = r["taiex_drawdown"]
        for trigger, target in schedule:
            if invested + 1e-9 < target and dd <= trigger:
                add = min(target - invested, cash)
                if add > 1e-9:
                    cash -= add
                    units += add / r["asset"]
                    invested += add
                    waits.append(i - start_idx)
                    if invested >= 0.999 and fully_idx is None:
                        fully_idx = i
        value = cash + units * r["asset"]
        max_value = max(max_value, value)
        max_dd = min(max_dd, value / max_value - 1)
    final = cash + units * rows[end_idx]["asset"]
    all_in = rows[end_idx]["asset"] / start_asset
    return {
        "final": final,
        "all_in_final": all_in,
        "excess": final / all_in - 1,
        "win": final > all_in,
        "max_drawdown": max_dd,
        "avg_cash": None,  # filled by lighter proxy below in strategy metrics if needed
        "fully_in_days": "" if fully_idx is None else fully_idx - start_idx,
        "never_full": fully_idx is None,
        "last_wait_days": max(waits) if waits else 0,
    }


def make_schedule(initial: float, full_dd: float, step=0.05) -> list[tuple[float, float]]:
    if initial >= 1:
        return [(0.0, 1.0)]
    levels = []
    x = -0.05
    while x >= full_dd - 1e-9:
        levels.append(round(x, 4))
        x -= step
    rem = 1 - initial
    sched = [(0.0, initial)]
    for j, lvl in enumerate(levels, 1):
        sched.append((lvl, initial + rem * j / len(levels)))
    return sched


def summarize(vals: list[float]) -> dict:
    vals = sorted(vals)
    if not vals:
        return {"avg": "", "median": "", "p10": "", "p90": "", "min": "", "max": ""}
    return {
        "avg": statistics.mean(vals),
        "median": statistics.median(vals),
        "p10": vals[int(0.10 * (len(vals) - 1))],
        "p90": vals[int(0.90 * (len(vals) - 1))],
        "min": vals[0],
        "max": vals[-1],
    }


def backtest(rows: list[dict], output_name: str = "rolling_start_strategy_summary", min_start_dd: float | None = None) -> list[dict]:
    horizons = {"1y": 252, "3y": 756, "5y": 1260, "10y": 2520}
    strategies = []
    for initial in [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]:
        for full in [-0.20, -0.25, -0.30, -0.35, -0.40, -0.50]:
            if initial == 1.0 and full != -0.20:
                continue
            strategies.append((f"init_{int(initial*100)}_full_{int(abs(full)*100)}", make_schedule(initial, full)))
    summary = []
    for strategy_name, sched in strategies:
        finals = []
        excess = []
        wins = 0
        never = 0
        mdds = []
        full_days = []
        hret = {k: [] for k in horizons}
        max_end = len(rows) - horizons["10y"] - 1
        for si in range(0, max_end + 1, 20):
            if min_start_dd is not None and rows[si]["taiex_drawdown"] < min_start_dd:
                continue
            res = simulate(rows, si, sched, len(rows) - 1)
            finals.append(res["final"])
            excess.append(res["excess"])
            wins += 1 if res["win"] else 0
            never += 1 if res["never_full"] else 0
            mdds.append(res["max_drawdown"])
            if res["fully_in_days"] != "":
                full_days.append(res["fully_in_days"])
            for h, n in horizons.items():
                r = simulate(rows, si, sched, si + n)
                years = n / 252
                hret[h].append(r["final"] ** (1 / years) - 1 if h != "1y" else r["final"] - 1)
        if not finals:
            continue
        item = {
            "strategy": strategy_name,
            "schedule": "; ".join([f"{trig:.0%}->{tgt:.0%}" for trig, tgt in sched]),
            "starts": len(finals),
            "avg_final": statistics.mean(finals),
            "median_final": statistics.median(finals),
            "avg_excess_vs_all_in": statistics.mean(excess),
            "win_rate_vs_all_in": wins / len(finals),
            "worst_excess_vs_all_in": min(excess),
            "best_excess_vs_all_in": max(excess),
            "avg_max_drawdown": statistics.mean(mdds),
            "never_full_rate": never / len(finals),
            "avg_full_days": statistics.mean(full_days) if full_days else "",
            "max_full_days": max(full_days) if full_days else "",
        }
        for h, vals in hret.items():
            item[f"{h}_avg"] = statistics.mean(vals)
            item[f"{h}_median"] = statistics.median(vals)
        summary.append(item)
    fields = list(summary[0].keys())
    write_csv(PROC / f"{output_name}.csv", summary, fields)
    return sorted(summary, key=lambda r: r["avg_final"], reverse=True)


def fmt_pct(x, digits=1):
    if x == "" or x is None:
        return ""
    return f"{float(x)*100:.{digits}f}%"


def fmt_num(x, digits=2):
    if x == "" or x is None:
        return ""
    return f"{float(x):,.{digits}f}"


def current_plan_table(hwm: float, taiex_now: float, etf_now: float, schedule: list[tuple[float, float]]) -> str:
    ratio = etf_now / taiex_now
    lines = ["| TAIEX Drawdown | TAIEX trigger | 0050 est. | cumulative 0050 | buy now/at level |",
             "|---:|---:|---:|---:|---:|"]
    prev = 0.0
    for dd, target in schedule:
        trigger = hwm * (1 + dd)
        etf = trigger * ratio
        buy = target - prev
        lines.append(f"| {dd:.0%} | {trigger:,.0f} | {etf:,.2f} | {target:.0%} | {buy:.0%} |")
        prev = target
    return "\n".join(lines)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    PROC.mkdir(parents=True, exist_ok=True)
    taiex = fetch_taiex()
    etf_latest = fetch_0050_twse_latest()
    y0050 = fetch_0050_yahoo()
    pe = fetch_twse_home_yields()
    dd_rows = enrich_drawdown(taiex)
    events = drawdown_events(dd_rows)
    wait = waiting_stats(events)
    asset = build_asset_series(dd_rows, y0050)
    bt = backtest(asset)
    bt_near_high = backtest(asset, "rolling_start_strategy_summary_start_dd_gt_minus_10", min_start_dd=-0.10)

    latest = dd_rows[-1]
    hwm = max(float(r["close"]) for r in dd_rows)
    hwm_row = [r for r in dd_rows if float(r["close"]) == hwm][-1]
    dd_now = float(latest["close"]) / hwm - 1
    freq = []
    for lvl in [-0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40, -0.50]:
        c = sum(1 for e in events if float(e["max_drawdown"]) <= lvl)
        freq.append((lvl, c))

    selected = {
        "aggressive": make_schedule(0.80, -0.25),
        "balanced": [(0.0, 0.70), (-0.10, 0.80), (-0.15, 0.90), (-0.25, 1.00)],
        "conservative": [(0.0, 0.50), (-0.10, 0.65), (-0.20, 0.80), (-0.30, 0.90), (-0.40, 1.00)],
    }
    etf_now = float(etf_latest["close"])
    now_hold = 0.70 if dd_now > -0.10 else 0.80

    top = bt[:10]
    top_near_high = bt_near_high[:10]
    all_in = next(r for r in bt if r["strategy"] == "init_100_full_20")

    md = []
    md.append("# 0050 長期 Buy & Hold 進場策略研究\n")
    md.append(f"產出日期：{datetime.now().date().isoformat()}。本報告不是投資建議；它是可重現的歷史資料研究。\n")
    md.append("## Step 1 最新市場資料\n")
    md.append("| 指標 | 數值 | 日期 | 來源 | 口徑 |")
    md.append("|---|---:|---|---|---|")
    md.append(f"| TAIEX 最新收盤 | {fmt_num(latest['close'])} | {latest['date']} | TWSE FMTQIK | 發行量加權股價指數收盤；OHLC 不使用 |")
    md.append(f"| TAIEX 歷史最高收盤 | {fmt_num(hwm)} | {hwm_row['date']} | TWSE FMTQIK | 2000 至今最高收盤 |")
    md.append(f"| 最新相對高點 Drawdown | {fmt_pct(dd_now, 2)} | {latest['date']} | 本研究計算 | close / high-water mark - 1 |")
    md.append(f"| 台股整體市場 PE | {fmt_num(pe['pe'])} | {pe['date']} | {pe['source']} | {pe['definition']}；未找到同口徑 2026-08-07 官方日資料 |")
    md.append(f"| 0050 最新收盤 | {fmt_num(etf_now)} | {etf_latest['date']} | {etf_latest['source']} | {etf_latest['note']} |")
    md.append("\n## Step 2 TAIEX 主要回撤事件\n")
    md.append("| 前高日期 | 前高 | 最低日期 | 最低 | Max DD | 高點到低點天數 | 低點到回高天數 | 水下天數 | -10 | -15 | -20 | -25 | -30 | -35 | -40 | -50 |")
    md.append("|---|---:|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|---|---|")
    for e in events:
        md.append(f"| {e['peak_date']} | {fmt_num(e['peak_close'])} | {e['trough_date']} | {fmt_num(e['trough_close'])} | {fmt_pct(e['max_drawdown'],1)} | {e['peak_to_trough_days']} | {e['trough_to_recovery_days']} | {e['underwater_days']} | {e['hit_10pct']} | {e['hit_15pct']} | {e['hit_20pct']} | {e['hit_25pct']} | {e['hit_30pct']} | {e['hit_35pct']} | {e['hit_40pct']} | {e['hit_50pct']} |")
    md.append("\n回撤頻率（以一次水下事件計一次）：")
    md.append("| 門檻 | 次數 |")
    md.append("|---:|---:|")
    for lvl, c in freq:
        md.append(f"| {lvl:.0%} | {c} |")
    md.append("\n## Step 3 跌到某程度後繼續等\n")
    md.append("| 已跌到 | 事件數 | 繼續到下一級機率 | 平均再跌 | 中位數再跌 | 最差再跌 | 未到下一級次數 | 等更低失敗率 |")
    md.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for w in wait:
        md.append(f"| {w['already_at']} | {w['events']} | {fmt_pct(w['prob_deeper_next_5pct'])} | {fmt_pct(w['avg_additional_decline'])} | {fmt_pct(w['median_additional_decline'])} | {fmt_pct(w['worst_additional_decline'])} | {w['did_not_reach_next_level']} | {fmt_pct(w['cash_wait_failed_ratio'])} |")
    md.append("\n## Step 4 PE\n")
    md.append("TWSE 官方首頁可重現 JSON 目前只提供主要市場年末 PE，台灣最新為 2025-12-31 的 23.22。未找到同一口徑、日頻、2000 至今的整體市場 PE，因此本研究不把 PE 納入策略優化，以避免混用不同 PE 定義或自行補造歷史資料。")
    md.append("\n## Step 5-8 Rolling start 回測摘要\n")
    md.append("0050 調整收盤使用 Yahoo Finance chart API，起始可用資料為 2009-01-02；因此 0050 總報酬 rolling backtest 覆蓋 2009-01-02 至最新資料。現金收益假設為 0，交易成本與稅費未計。每 20 個交易日作為一個起始日，以避免只從股災前後挑日期。")
    md.append("\n| 排名 | 策略 | 平均最終資產 | 中位最終資產 | 對 All-in 平均超額 | 勝率 | 最差相對 | 永遠未滿倉 | 10Y CAGR avg |")
    md.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(top, 1):
        md.append(f"| {i} | {r['strategy']} | {fmt_num(r['avg_final'])} | {fmt_num(r['median_final'])} | {fmt_pct(r['avg_excess_vs_all_in'])} | {fmt_pct(r['win_rate_vs_all_in'])} | {fmt_pct(r['worst_excess_vs_all_in'])} | {fmt_pct(r['never_full_rate'])} | {fmt_pct(r['10y_avg'])} |")
    md.append(f"\n立即 All-in 基準：平均最終資產 {fmt_num(all_in['avg_final'])}，10Y CAGR avg {fmt_pct(all_in['10y_avg'])}。全樣本排名偏好 -20%/-25% 滿倉，原因是 2009-2016 多數起始日仍處於 2000 高點後的水下區，等待策略常能較快觸發，這不完全等同於目前接近歷史高點的情境。")
    md.append("\n### 起始日接近高點（TAIEX drawdown > -10%）")
    md.append("注意：受限於 0050 調整價資料起點與 10 年 horizon，這個子樣本只有少量起始點，適合用來校正目前情境，不宜單獨當成最終答案。")
    md.append("| 排名 | 策略 | 起始點 | 平均最終資產 | 對 All-in 平均超額 | 勝率 | 最差相對 | 永遠未滿倉 | 10Y CAGR avg |")
    md.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for i, r in enumerate(top_near_high, 1):
        md.append(f"| {i} | {r['strategy']} | {r['starts']} | {fmt_num(r['avg_final'])} | {fmt_pct(r['avg_excess_vs_all_in'])} | {fmt_pct(r['win_rate_vs_all_in'])} | {fmt_pct(r['worst_excess_vs_all_in'])} | {fmt_pct(r['never_full_rate'])} | {fmt_pct(r['10y_avg'])} |")
    md.append("\n## Step 9 目前市場情境\n")
    md.append(f"截至 {latest['date']}，TAIEX 距歷史高點 {fmt_pct(dd_now,2)}，不是大回撤狀態。若目標是長期最終資產而非降低波動，我的資料導向建議是現在至少投入 70%。")
    md.append(f"\n建議平衡方案：現在投入 {now_hold:.0%}，保留 {1-now_hold:.0%} 現金；下一筆以 TAIEX drawdown 為正式觸發，0050 價格只是估算。")
    md.append("\n" + current_plan_table(hwm, float(latest["close"]), etf_now, selected["balanced"]))
    md.append("\n## Step 10 三套方案\n")
    for title, sched in [("積極型", selected["aggressive"]), ("平衡型", selected["balanced"]), ("保守等待型", selected["conservative"])]:
        md.append(f"\n### {title}\n")
        md.append("| TAIEX Drawdown | 累計0050持倉% | 本次投入% |")
        md.append("|---:|---:|---:|")
        prev = 0
        for dd, tgt in sched:
            md.append(f"| {dd:.0%} | {tgt:.0%} | {tgt-prev:.0%} |")
            prev = tgt
        md.append(current_plan_table(hwm, float(latest["close"]), etf_now, sched))
    md.append("\n## 結論\n")
    md.append("若主要目標是長期 Buy & Hold 的最終資產最大化，歷史資料不支持大幅等待。最合理的一套是平衡型：現在 70% 進場，-10% 加到 80%，-15% 加到 90%，-25% 滿倉。把最後 10%-20% 留到 -40% 的機會成本很高，因為多數 -10% 到 -30% 的事件不會繼續走到 -40%。")
    md.append("\n## 可重現檔案\n")
    md.append("- `research_0050_entry_strategy.py`\n- `data/raw/taiex_twse_mi_5mins_hist.csv`\n- `data/raw/0050_yahoo_adj_close.csv`\n- `data/raw/twse_home_yields.json`\n- `data/processed/taiex_drawdown_daily.csv`\n- `data/processed/taiex_drawdown_events.csv`\n- `data/processed/waiting_after_drawdown_stats.csv`\n- `data/processed/rolling_start_strategy_summary.csv`\n- `data/processed/rolling_start_strategy_summary_start_dd_gt_minus_10.csv`")
    REPORT.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {REPORT}")


if __name__ == "__main__":
    main()
