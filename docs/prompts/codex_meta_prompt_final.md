# Codex 執行指令（搭配 codex_prompt_v3-7-5.md 使用）

> **本文件控制 HOW to work，codex_prompt_v3-7-5.md 控制 WHAT to build。**
> AGENTS.md 提供台股量化的領域知識，Codex 自動讀取。
> tools/audit.py 提供自動化品質檢查，每個 Stage 結束前跑一次。

---

## §1 執行流程

```
首次啟動（只做一次）：
  讀本文件 → 讀 docs/prompts/codex_prompt_v3-7-5.md Pre-flight 段落
    → 執行 Pre-flight → 建立 scaffold：
      - docs/PROGRESS.md（記錄進度）
      - docs/LESSONS.md（記錄踩坑教訓）
      - 所有 package 的 __init__.py
      - 確認 AGENTS.md 已在專案根目錄
      - 確認 tools/audit.py 已就位
```

```
每個 Stage 的循環：
  讀 docs/prompts/codex_prompt_v3-7-5.md 中該 Stage 的段落
      ↓
  在 docs/PROGRESS.md 列出本 Stage 所有模組，標記 [ ]
      ↓
  按依賴樹由底向上，一次寫完一個模組：
      寫完 → python -c "import {模組}" → python tools/audit.py {檔案} → PROGRESS.md 標記 [x]
      ↓
  全部模組完成 → python tools/audit.py . → pytest tests/ -v
      ↓
  全部通過 → 更新 LESSONS.md（如有新教訓）→ 下一個 Stage
  失敗 → 修復重跑，同一測試失敗 3 次就停下來報告
```

---

## §2 自動化審計（tools/audit.py）

每個 Stage 結束前跑一次。它自動檢查 11 項規則：

```
FAIL（必修）：bare except, try/except pass, import *, .shift(-N) 在 engine/,
             硬寫密碼, mutable default argument
WARN（判斷）：print() 在非測試檔, merge() 缺 how=, 檔案 >500 行,
             函式 >80 行, 公開函式缺回傳型別
```

FAIL 必須修到 0 才能進下一個 Stage。
WARN 有理由不修的，在 code 旁加 `# audit:ignore {RULE} — {理由}`。

---

## §3 品質底線

```
1. 前瞻偏差 = 系統報廢。AGENTS.md 列出台股三種特有前瞻來源。
2. NaN 穿透 = 結果不可信。進入計算前先處理，不允許「先算再 dropna」。
3. 測試不是裝飾品。前面 Stage 的測試不能被後面 Stage 破壞。
4. 不自作主張。spec 沒說的功能不加。有矛盾就停下來報告。
```

---

## §4 中斷保護

**你不知道自己何時會被截斷。以下是結構性保護：**

**一個 .py 檔案 = 一個原子單位。** 寫完一個才開始下一個。
半成品的模組對後續 session 是負擔，不是資產。

**PROGRESS.md 是接力棒。** 每完成一個模組就標記 [x]。
被截斷時，下一個 session 讀 PROGRESS.md 就知道從哪繼續。

**介面先落地。** 如果你在腦中規劃了多個模組的介面，
先把介面定義寫進 docstring 或 __init__.py，再寫實作。
被截斷時，介面契約至少存在，下一個 session 可以接。

**依賴樹由底向上：** config → data → engine → agents → orchestrator。

---

## §5 沙箱限制

```
- 沒有外部網路：FinMind、Telegram 全部用 mock
- 不嘗試 pip install 不在 requirements.txt 的套件
- Stage 0 測試用 pytest.importorskip() 避免 ImportError
  （Stage 0 結束時所有測試應為 SKIPPED，後續 Stage 逐步變 PASSED）
```

---

## §6 踩坑防禦

### §6.1 FinMind 欄位名稱陷阱

FinMind 用 `max`/`min`（不是 `high`/`low`），`Trading_Volume` 的 TV 大寫但
`Trading_money` 的 m 小寫，`stock_id` 永遠是 str `"2330"` 不是 int。
**完整欄位表在 AGENTS.md，mock fixture 必須用這些名稱。**

### §6.2 __init__.py

沒有它的資料夾不是 Python package。Stage 1 scaffold 時建立：
config/, engine/, agents/, data/, bot/, tests/, tests/acceptance/

### §6.3 Python 3.10+ 相容

目標 NAS 跑 Ubuntu 22.04（Python 3.10）。
不使用 match/case, ExceptionGroup(3.11+), type X = ...(3.12+)。

### §6.4 假測試判斷法

**如果把函式實作全部刪掉只留 `return None`，測試會不會 FAIL？**
不會 → 假測試，重寫。Mock 只用在外部邊界（API、檔案），不 mock 內部邏輯。

### §6.5 requirements.txt 漂移

每個 Stage 結束前確認：所有 import 的第三方套件都在 requirements.txt 中。
發現缺的直接補，不等確認。

### §6.6 AGENTS.md

專案根目錄的 AGENTS.md 包含台股量化領域知識。
Codex 每次任務開始前自動讀取，不需要在 prompt 裡提醒。

---

## §7 經驗累積

每次踩坑（測試失敗才發現的邏輯錯誤、spec 的隱含假設），
在 docs/LESSONS.md 追加一條：

```markdown
## Stage {N}: {一句話}
原因：{為什麼出錯}
規則：{未來怎麼避免}
```

每個 Stage 開頭重讀 LESSONS.md。不要為了寫而寫——沒踩坑就不寫。

---

## §8 跨 Session 接力

建議每個 Stage 用獨立的 Codex task。每個 task 開始時：

```
讀取 docs/prompts/codex_meta_prompt_final.md。
繼續 Stage N。讀取 docs/PROGRESS.md 確認進度。
從 docs/prompts/codex_prompt_v3-7-5.md 讀取對應段落。
```

**每個 Stage 對應的段落：**
- Stage 0：`## 階段零`
- Stage 1：`## 階段一` + `### 4.7 config.py 常數總整理`
- Stage 2：`## 階段二` + `## Appendix B`
- Stage 3：`## 階段三`
- Stage 4：`## 階段四`
- Stage 5：`## 階段五`

AGENTS.md 會自動載入。PROGRESS.md + LESSONS.md 是跨 session 的接力棒。

---

## §9 最終檢查（所有 Stage 完成後）

```bash
python tools/audit.py .                    # 0 FAIL
pytest tests/ -v                           # 全部 PASS
grep -rn "\.shift(-" engine/               # 空（無前瞻偏差）
grep -rn "short" engine/ config/ agents/ bot/ \
  | grep -vi "short_balance\|ShortSale\|MarginShort\|shortage"  # 空（無放空）
grep -rn "api_key\|token\|password" config/ engine/ agents/ bot/ \
  | grep -v "\.env\|os\.getenv\|SETTINGS\.\|#\|\.gitignore"    # 空（無硬寫密碼）
find . -name "*.py" -not -path "./.venv/*" \
  -exec wc -l {} + | awk '$1 > 500'       # 空（無超長檔案）
```

輸出 `docs/FINAL_CHECK.md` 記錄每條指令的結果。
