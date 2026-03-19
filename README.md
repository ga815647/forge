# Forge

AI 驅動的程式碼重構助手，以 Gradio UI 操作，內建安全審查、git checkpoint 與成本追蹤。

## 功能

- **多模式執行** — 支援 auto / manual / review 等模式
- **安全防護** — 自動偵測危險指令（`rm -rf`、`force push`、`reset --hard` 等）並提示確認
- **Git 整合** — 每輪自動建立 checkpoint，支援一鍵回滾
- **成本追蹤** — 記錄每輪 token 用量，顯示累計費用摘要
- **自動審查** — 內建 `agent_review` 對輸出進行 quick / auto review
- **197 tests** — 完整單元測試覆蓋（pytest）

## 目錄結構

```
forge/
├── forge/
│   ├── main.py              # Gradio UI 入口
│   ├── ui_builder.py        # UI 元件建構
│   ├── main_config.py       # 設定載入
│   ├── orchestrator_main.py # 路由、安全檢查、CostTracker
│   ├── orchestrator_init.py # 初始化流程
│   ├── orchestrator_loop.py # 主迴圈
│   ├── loop_helpers.py      # 迴圈輔助函式
│   ├── agent.py             # LLM agent 呼叫
│   ├── agent_review.py      # auto / quick review
│   ├── audit_runner.py      # 稽核執行器
│   ├── git_ops.py           # checkpoint / rollback / squash
│   ├── init_chunker.py      # 檔案分塊邏輯
│   ├── monitor.py           # 資源監控
│   ├── prompts.py           # Prompt 模板
│   ├── security.py          # 路徑安全、prompt injection 偵測
│   ├── timeline.py          # 執行時間軸記錄
│   └── tests/               # 197 tests（8 個測試檔）
├── docs/
│   ├── PROGRESS.md
│   └── LESSONS.md
└── requirements.txt
```

## 安裝

```bash
pip install -r requirements.txt
```

## 執行

```bash
python -m forge.main
```


開啟後在瀏覽器存取 Gradio UI（預設 `http://localhost:7860`）。

## 測試

```bash
pytest forge/tests/ -v
```

| 測試檔 | Tests |
|--------|-------|
| test_security.py | 27 |
| test_monitor.py | 16 |
| test_agent.py | 19 |
| test_prompts.py | 26 |
| test_timeline.py | 14 |
| test_audit_runner.py | 23 |
| test_orchestrator_init.py | 25 |
| test_orchestrator_loop.py | 47 |
| **總計** | **197** |

## 依賴

- [gradio](https://gradio.app/) >= 4.0.0
- [psutil](https://github.com/giampaolo/psutil) >= 5.9.0
- [plyer](https://github.com/kivy/plyer) >= 2.1.0
- [pytest](https://pytest.org/) >= 7.0.0
