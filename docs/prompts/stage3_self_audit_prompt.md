# Stage 3 自我審查 Prompt

> **任務目標:** 對 Stage 3 已完成的代碼進行邏輯層面的審查,找出潛在的 Look-ahead bias、時序對齊錯誤、領域知識誤解等問題。

---

## 審查範圍與方法

你是一個資深的台股量化系統審查員。請按以下清單逐項檢查 Stage 3 的實作,對每項給出「✅ 通過」或「⚠️ 需關注」,並說明理由。

**不要修改代碼,只產生審查報告。**

---

## 第一部分: Look-ahead Bias 關鍵路徑檢查

### 1.1 還原股價實作 (最高優先級)

**檢查文件:** `data/fetcher.py`, `data/processor.py`, `engine/backtest.py`

**檢查項目:**
```
□ fetcher.py 是否使用 TaiwanStockPriceAdj (不是 TaiwanStockPrice)
□ processor.py 計算技術指標時是否都用 adjusted price
□ backtest.py 的進場/出場價是否都用 kline_adj 的數據
□ 有沒有混用 raw price 和 adjusted price 的情況
```

**驗證方法:**
1. 用 grep 搜尋 `TaiwanStockPrice` (不含 Adj) 是否還存在
2. 檢查 processor.py 的 price 相關計算是否從正確的 parquet 讀取
3. 查看 backtest.py 的 entry_price/exit_price 來源

**輸出格式:**
```
### 1.1 還原股價實作
狀態: ✅ 通過 / ⚠️ 需關注
發現:
- fetcher.py 第 XX 行: 使用 TaiwanStockPriceAdj ✅
- processor.py 第 XX 行: 所有技術指標計算都用 Close_adj ✅
- backtest.py 第 XX 行: [描述發現的問題或確認正確]
結論: [一句話總結]
```

---

### 1.2 月營收 Look-ahead 防護

**檢查文件:** `data/fetcher.py`, `data/processor.py`

**檢查項目:**
```
□ 月營收資料是否對齊到「次月 10 日」(不是營收所屬月份)
□ processor.py 計算月增率/年增率時,日期對齊是否正確
□ 有沒有在 T 日用到 T 月的營收(應該用 T-1 月)
```

**驗證方法:**
```python
# 在 Python 中手動檢查對齊邏輯
# 假設 8 月營收應該對齊到 9/10,請檢查代碼是否這樣做
```

**輸出格式:** (同上)

---

### 1.3 PER/PBR Look-ahead 防護

**檢查文件:** `data/fetcher.py`

**檢查項目:**
```
□ PER 資料是否對齊到季報公告截止日(不是季度結束日)
□ Q1 → 5/15, Q2 → 8/14, Q3 → 11/14, Q4 → 3/31 對齊是否正確
□ 有沒有在 5/14 就用到 Q1 的 PER (應該 5/15 之後才能用)
```

---

### 1.4 外資持股週頻對齊

**檢查文件:** `data/fetcher.py`, `data/processor.py`

**檢查項目:**
```
□ 外資持股是否保持週頻,沒有做日頻內插
□ 日線回測時,是否用「上週五」的持股比例(不是當週未完成的)
□ detect_sequence 計算「連續 N 週增加」時,是否以週五為單位(不是每日)
```

**特別注意:**
Prompt §3.6 明確要求:「不得用日頻 index 對應週頻資料,否則每天看到同一個數字重複五次」

---

### 1.5 當沖比率時序

**檢查文件:** `data/fetcher.py`, FILTER 判斷邏輯

**檢查項目:**
```
□ 當沖比率 FILTER 是否永遠使用 T-1 日資料(不是 T 日)
□ 理由標註: T 日當沖比率 21:30 才公布,與 scanner 觸發時間衝突
```

---

### 1.6 週K對齊規則

**檢查文件:** `data/processor.py`, TVA 計算邏輯

**檢查項目:**
```
□ 回測歷史時,每個交易日對應的週K是否為「該日所在週的前一完整週」
□ 例如: 2023-03-15(週三) 應使用 2023-03-06 週,不是 2023-03-13 週
□ 日常掃描時,是否使用「上週完整收盤週K」(不是當週進行中的)
```

---

## 第二部分: TVA 狀態機三處修正驗證

### 2.1 修正一: A = V.diff(1)

**檢查文件:** `engine/backtest.py` 或 `engine/tva.py` (wherever calculate_tva_state 是)

**檢查項目:**
```python
# 找到 calculate_tva_state 函數,檢查加速度計算
# ❌ 錯誤: A = V.diff(velocity_period)
# ✅ 正確: A = V.diff(1)
```

**驗證方法:**
1. 查看代碼中 A 的計算公式
2. 對照 Appendix B.4 的虛擬碼

**輸出:**
```
### 2.1 TVA 加速度計算
狀態: ✅ 通過 / ⚠️ 需關注
代碼片段:
[貼出實際代碼]

對照 Prompt 要求:
修正前: A = V.diff(velocity_period)  # 滯後,反應慢
修正後: A = V.diff(1)                # 靈敏度正確

實際實作: [描述]
結論: [是否符合要求]
```

---

### 2.2 修正二: trend_period 參數化

**檢查項目:**
```
□ trend_period 是否為參數,不再固定為 20
□ 參數格網是否為 [10, 20, 60]
□ hypothesis_id 命名是否反映 trend_period: TVA1(10), TVA2(20), TVA3(60)
□ registry 是否支援 trend_period 和 state_filter 雙參數
```

---

### 2.3 修正三: 狀態 0 邊界處理

**檢查項目:**
```
□ 前 trend_period 天是否產生狀態 0(不是 NaN,也不是 1-8)
□ 狀態 0 是否跳過 TVA 相關邏輯(不作為進場 FILTER,不觸發 TVA 出場)
□ 其他出場條件(止損止盈)在狀態 0 時是否正常運作
```

---

## 第三部分: 兩階段回測實作檢查

### 3.1 Stage A 快篩邏輯

**檢查文件:** `engine/backtest.py`

**檢查項目:**
```
□ Stage A 是否只計算固定持有期報酬(不做動態出場)
□ 是否使用向量化計算(pandas shift/rolling,無 for loop)
□ 淘汰門檻是否為: 勝率 < 50% 或 樣本數 < 50
□ config.py 是否定義 STAGE_A_MIN_WIN_RATE = 0.50, STAGE_A_MIN_SAMPLES = 50
```

---

### 3.2 Stage B 精細驗證

**檢查項目:**
```
□ Stage B 是否對 Stage A 通過的假說跑完整動態出場
□ 五個出場條件是否都實作: 止損、止盈、TVA轉差、追蹤止損、horizon_days
□ 出場價是否都用 T+1 開盤價(遵守 T+1 鐵律)
□ Stage B 的結果是否作為最終驗證(不沿用 Stage A 數字)
```

---

### 3.3 漲跌停處理

**檢查項目:**
```
□ T+1 開盤高於 T 日收盤 +5% → 跳過進場 ✅
□ T+1 開盤低於 T 日收盤 -5% → 仍進場 ✅ (刻意設計,不是筆誤)
□ 出場缺口是否使用 T+1 實際開盤價(不美化為止損價)
```

**特別注意:**
Prompt §2.3 明確警告:「下行跳空『仍進場』是刻意設計,非筆誤,不得以『保守』為由自主改為跳過」

---

## 第四部分: 特徵矩陣接口檢查

### 4.1 feature_matrix 結構

**檢查文件:** `data/feature_matrix.py`

**檢查項目:**
```
□ feature_matrix 是否為 dict[stock_id, dict]
□ 必要欄位是否都存在:
  - tva_state (dict, 各 trend_period 的值)
  - vol_ma_20
  - zscore_60
  - rel_strength_20
  - ten_year_above
  - weekly_tva
  - daytrading_pct_t1
  - price_zone
  - listing_date
□ 重新計算時機是否正確:
  - scanner 每日觸發時重算一次
  - miner 每個 micro-batch 開始前重算一次
```

---

### 4.2 相對強弱兩階段計算

**檢查項目:**
```
□ Pass 1: 載入全體 150 檔,計算 Universe 報酬中位數
□ Pass 2: 對每檔判斷是否超越中位數
□ 有沒有在 Pass 1 完成前就開始 Pass 2(會導致中位數錯誤)
```

---

## 第五部分: 常數定義交叉檢查

### 5.1 config.py 新增常數

**檢查項目:**
對照 §4.7 和各章節,檢查以下常數是否都在 config.py 中定義:

```
Stage 2 新增:
□ TRAILING_STOP_PCT_GRID = [0.03, 0.05, 0.07]
□ ROUND_TRIP_COST = 0.00585
□ SLIPPAGE_PCT = 0.003
□ STAGE_A_MIN_WIN_RATE = 0.50
□ STAGE_A_MIN_SAMPLES = 50

Stage 3 新增:
□ FORCE_FULL_DOWNLOAD_DAY = 1
□ EX_DIVIDEND_BLACKOUT_DAYS = 3
□ EARNINGS_BLACKOUT_DAYS = 5
□ EXTREME_VOLUME_PERCENTILE = 0.99
□ EARLY_WARNING_TRADES = 10
□ EARLY_WARNING_THRESHOLD = 0.35
□ MSCI_REBALANCE_MONTHS = [2, 5, 8, 11]
□ TW50_REBALANCE_MONTHS = [6, 12]
```

---

### 5.2 Magic Number 檢查

**檢查方法:**
```bash
# 在 engine/, data/, agents/ 中搜尋硬寫的數字
grep -rn "0.585\|0.003\|0.50\|0.99" engine/ data/ agents/
# 這些應該都引用 config.py 的常數,不直接寫數字
```

---

## 第六部分: FILTER 命名與方向檢查

### 6.1 FILTER 命名規則

**檢查項目:**
```
□ DAYTRADING_HIGH: 當沖比率高(壓抑做多) - 命名反映「市場狀態」✅
□ REL_STRONG: 個股強勢(加強做多) - 命名反映「市場狀態」✅
□ TEN_YEAR_ABOVE: 站上十年線(加強做多) - 命名反映「市場狀態」✅
□ WEEKLY_BULL: 週K多頭(加強做多) - 命名反映「市場狀態」✅

⚠️ 錯誤範例:
□ DAYTRADING_SUPPRESS (命名反映「作用」) - 應改為 DAYTRADING_HIGH
```

---

### 6.2 大盤濾網作用範圍

**檢查項目:**
```
□ 大盤環境濾網是否只作用於「進場管線 A」
□ 出場管線 B 是否不受大盤濾網影響(已持倉股票永遠掃描)
□ 靜默期間,出場通知是否仍正常推播
```

---

## 第七部分: 降級與復活機制邏輯

### 7.1 降級標準

**檢查文件:** `engine/lifecycle.py` 或 `engine/validator.py`

**檢查項目:**
```
□ 降級標準: 最近 20 筆交易滾動勝率,連續 30 次評估 < 50%
□ 「連續 30 次評估」定義: 每完成一筆新交易重新計算,不是「連續 30 筆虧損」
□ 門檻值是否為 MIN_RECENT_2Y_WIN_RATE (0.50),不是 MIN_WIN_RATE (0.55)
```

---

### 7.2 復活機制

**檢查項目:**
```
□ 復活條件: 距上次降級 ≥ 250 交易日 AND active 策略數 < 200
□ revival_count 是否遞增
□ revival_count > 3 → hard_fail 永久封存
□ strategy_lifecycle.json 的舊記錄是否自動 migration 補 revival_count: 0
```

---

### 7.3 早期預警

**檢查項目:**
```
□ 觸發條件: 最近 10 筆勝率 < 35%
□ 推播標注: 「⚠️ 策略近期異常,僅供參考」
□ 不自動降級(仍由正式降級流程決定)
□ 解除條件: 後續 10 筆勝率回升到 ≥ 45%
```

---

## 第八部分: 時間加權勝率實作

### 8.1 時間衰減公式

**檢查文件:** `engine/validator.py` 或 `engine/time_decay.py`

**檢查項目:**
```python
# 對照 Appendix B.5 的虛擬碼
□ weight = exp(-lambda * days_ago / T_MAX_DAYS)
□ T_MAX_DAYS = 3650 (10 年)
□ DEFAULT_LAMBDA = 1.0
□ anchor = max(trade_dates),不是 datetime.now()
```

---

### 8.2 使用出場日期

**檢查項目:**
```
□ compute_weighted_win_rate 的 trade_dates 是否傳入「出場日」(不是進場日)
□ 理由標註: 反映「最近完成的交易表現」
```

---

## 審查報告格式

請將以上所有檢查結果彙整成以下格式:

```markdown
# Stage 3 自我審查報告

**審查時間:** 2026-03-19
**審查範圍:** Stage 3 所有已完成代碼

---

## 一、Look-ahead Bias 防護 (8 項)

### 1.1 還原股價實作
狀態: ✅ / ⚠️
[詳細發現]

### 1.2 月營收對齊
狀態: ✅ / ⚠️
[詳細發現]

...

---

## 二、TVA 狀態機修正 (3 項)

[同上格式]

---

## 三、兩階段回測 (3 項)

[同上格式]

---

## 四、特徵矩陣 (2 項)

[同上格式]

---

## 五、常數定義 (2 項)

[同上格式]

---

## 六、FILTER 命名 (2 項)

[同上格式]

---

## 七、生命週期機制 (3 項)

[同上格式]

---

## 八、時間加權勝率 (2 項)

[同上格式]

---

## 總結

### 通過項目 (綠燈)
- [列出所有 ✅ 項目]

### 需關注項目 (黃燈)
- [列出所有 ⚠️ 項目,標註嚴重程度]

### 建議修正優先級
1. 🔴 高優先級 (影響回測可信度): [列表]
2. 🟡 中優先級 (影響性能或穩定性): [列表]
3. 🟢 低優先級 (代碼風格或文檔): [列表]

### 整體評估
- 總檢查項: XX 項
- 通過: XX 項 (XX%)
- 需關注: XX 項 (XX%)
- 建議: [繼續 Stage 4 / 先修正問題 / 需人工深度審查]
```

---

## 執行指令

```bash
# 1. 在專案根目錄執行 Python 檢查
cd /home/claude/trading-system-main

# 2. 檢查特定函數的實作
python -c "
from engine.backtest import calculate_tva_state
import inspect
print(inspect.getsource(calculate_tva_state))
"

# 3. 搜尋潛在問題
grep -rn "\.shift(-" engine/   # 應為空
grep -rn "TaiwanStockPrice[^A]" data/  # 應為空或只在註釋中

# 4. 檢查常數定義
python -c "
from config.config import Settings
s = Settings()
required = ['TRAILING_STOP_PCT_GRID', 'ROUND_TRIP_COST', 'SLIPPAGE_PCT',
            'STAGE_A_MIN_WIN_RATE', 'EXTREME_VOLUME_PERCENTILE']
for attr in required:
    if not hasattr(s, attr):
        print(f'❌ Missing: {attr}')
    else:
        print(f'✅ Found: {attr} = {getattr(s, attr)}')
"
```

---

## 注意事項

1. **不要修改代碼** - 這是審查,不是修正
2. **逐項檢查** - 不要跳過任何項目
3. **給出證據** - 每個判斷都要貼代碼片段或 grep 結果
4. **標記嚴重程度** - 區分「必須修正」vs「可以容忍」
5. **參考 Prompt** - 每個檢查項都對應 v3-7-5.md 的具體章節

---

## 開始審查

請開始執行上述檢查,產生完整的審查報告。
