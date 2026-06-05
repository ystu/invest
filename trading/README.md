# BTC 型態回測

這個資料夾用來測試 BTC 型態交易規則：W 底、M 頭、N 型續漲、N 型續跌。

程式目前會用 ZigZag 轉折找型態，搭配量能確認、結構停損與漲跌幅滿足目標價。

## 執行

```powershell
python .\backtest_patterns.py --symbol BTCUSDT --interval 4h --start 2025-01-01
```

輸出：

- `data/btcusdt_4h.csv`：下載的 OHLCV K 線資料
- `reports/trades_4h.csv`：交易紀錄
- `reports/signals_4h.svg`：最近 K 線的訊號圖

## 常用參數

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
  --exit-mode two_targets `
  --risk-pct 1 `
  --fee-bps 4
```

重點參數：

- `--zigzag-pct`：越大越偏大波段，交易次數越少。
- `--tolerance-pct`：W 底低點或 M 頭高點的接近程度。
- `--volume-mult`：突破量必須高於近期均量的倍數。
- `--stop-mode`：停損模式，常用 `retest` 或 `tightest`。
- `--entry-mode`：進場模式，常用 `retest_break`。
- `--min-entry-rr`：最低進場賺賠比。
- `--exit-mode`：`single_target` 一波滿足全出；`two_targets` 一波賣一半、兩波全出。
- `--patterns`：只測指定型態，例如 `M_TOP,N_SHORT`。
- `--sides`：只測指定方向，例如 `short`。
- `--risk-pct`：每筆交易承擔的帳戶風險比例。

## 核心交易規則

### 1. 先判斷大方向

- 用 BTC、ETH 和主要指數判斷市場風險。
- 多頭市場才積極做多；空頭市場以放空、防守或空手為主。
- 美股至少要有兩個主要指數偏多，個股多單才值得做。
- 做多找強勢族群裡的強勢或補漲股；做空找弱勢族群裡的弱勢股。

### 2. 再選主要型態

- 大型態優先，小型態只做輔助。
- 有主型態就看主型態，沒有主型態才看波段滿足。
- 先出現且有效的型態優先；新的大型態出現後，改看大型態。
- 已經被破壞的型態，不再拿來算目標價。
- 盤整區間必須能畫出明確小型態，才適合用來做波段滿足。

### 3. 確認突破或跌破

- 4H 收盤用來確認趨勢與型態是否成立。
- 1H 收盤可以用來進場，但最後 4H 收盤若沒有突破，就出場。
- 真突破要實體站上頸線，最好同時突破前高。
- 斜頸線突破後，仍要觀察前高；斜頸線跌破後，仍要觀察前低。
- 突破最好帶量；跌破不一定需要帶量。
- 前方有重要頸線或壓力時，不急著進場。

### 4. 處理假突破與破底翻

- 假突破後，看壓不看撐；必須重新突破最高點，才算重新轉強。
- 假突破若盤中已明顯成立，可以直接出場，不必等收盤。
- 破底翻必須跌破後重新站回頸線，最好再突破起跌長黑高點。
- 破底翻成立後，看撐不看壓，停損放在前低。
- 空單到達跌幅滿足後，如果出現破底翻，先回補。

### 5. 停損與停利

- 多單停損放在整理區下緣、突破 K 低點，或起漲低點。
- 空單停損放在整理區上緣、跌破 K 高點，或起跌高點。
- 到一波滿足先賣一半。
- 到兩波滿足全出。
- 到滿足後若回到主力攻擊成本區，只有守住支撐才考慮買回。
- 無法計算滿足的短波段，獲利抓約 1%，停損不超過 1%。

### 6. 資金控管

- 同時最多持有三檔標的。
- 每筆先決定最多可賠多少，再用進場價與停損價回推部位。
- 小型態小資金，大型態才加大資金。
- 連勝後才增加籌碼，虧損後要減少籌碼。
- 不在不對的位置進場，寧可等待下一個型態。

## 下一步回測方向

- 加入 1H 進場、4H 確認的多週期邏輯。
- 加入假突破立即出場規則。
- 加入破底翻反向規則。
- 加入大型態優先於小型態的排序。
- 加入一波賣一半、兩波全出的分批停利。
- 加入最多三檔持倉限制。
- 加入 BTC、ETH 和主要指數作為市場方向濾網。

## 外部筆記

- 交易試算表：
  <https://docs.google.com/spreadsheets/d/1eEMzSJd-Te-XIVy9c6AMAvjGwY1WGhKZ/edit?usp=sharing&ouid=103910740627286942830&rtpof=true&sd=true>
- 交易投影片：
  <https://docs.google.com/presentation/d/1RAliwQBXmkpm7cBuBMVA2KZOtgJt1c4tFAacC-qHkwY/edit?usp=drivesdk>

這是研究用程式，不是投資建議。實盤前先用回測與人工覆盤驗證規則。
