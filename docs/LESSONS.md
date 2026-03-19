# Forge 建構經驗紀錄

> 無 git history（專案尚未初始化 git），以下記錄本 session 的架構決策與踩坑。

---

## Stage 1: monitor.py 與 agent.py 的依賴方向

原因：meta_prompt 說「monitor.py 依賴 agent.py 的 process 管理」，直覺上以為 monitor.py 要 import agent.py。
實際：monitor.py 只接收 subprocess.Popen 物件，不需要 import agent.py。agent.py 才是 import monitor 的那個。
規則：「依賴 X 的 Y 功能」意思是「Y 在執行時需要 X 提供的物件」，不代表 import 方向一定是 Y → X。

---

## Stage 2: compress() 的 lazy import

原因：agent.py 需要 prompts.py（在 compress() 裡），但 prompts.py 建構順序在 agent.py 之後。
實際：用 `from . import prompts as _p` 放在函式內部（lazy import）可以解決循環/順序問題。
規則：模組間有順序問題時，優先考慮 lazy import（在函式內 import），而不是重新設計架構。

---

## Stage 3: write_agent_file() 放哪裡

原因：write_agent_file() 需要呼叫 think()（for review），但 think() 在 agent.py，security.py 不應該 import agent.py（違反依賴方向）。
實際：把 write_agent_file() 放在 agent.py，safe_write() + update_manifest() 保留在 security.py。
規則：需要 LLM 的邏輯放 agent.py，純 Python 的檔案操作放 security.py。

---

## Stage 4: orchestrator_loop.py 嚴重超過 300 行

原因：一輪迴圈有 12 個步驟（壓縮、外部偵測、manifest 驗證、think、do、audit、security scan、judge、timeline、reality check、lessons、plan 完成檢查），加上所有 helper 函式，單檔很容易爆。
實際：現在是 520 行，遠超 300 行限制。
規則：下一階段應把 orchestrator_loop.py 的 helper 函式拆到獨立的 `loop_helpers.py`，或依步驟分群拆為 `loop_think.py` / `loop_do.py`。

---

## Stage 5: shell=True 的使用

原因：audit_runner.py 需要跑複合指令如 `pytest tests/ -v`，subprocess.run 加 list 形式在 Windows 上不支援 PATH 查找 shell 內建。
實際：只在 `_run_tool()` 內使用 `shell=True`，cmd 值來自 `detect_tools()`（程式內部硬碼），不來自使用者輸入。
規則：shell=True 必須加注釋說明 cmd 來源，且來源必須是程式內部常數，不能是任何外部輸入。

---

## Stage 6: 測試全部缺失

原因：開發順序是先寫所有模組再寫測試，導致被中斷時測試完全空白。
實際：0 tests ran。
規則：下一階段應嚴格遵守 meta_prompt §4「測試先寫」——先寫 test_{模組}.py（只有 stub），再寫模組實作，再補齊測試內容。

---

## Stage 7: 無 git 導致無 checkpoint

原因：project 目錄沒有初始化 git，orchestrator_main.py 的 create_checkpoint() 會靜默失敗。
實際：rollback 功能在無 git 環境完全不可用。
規則：forge 啟動時應先檢查 git 是否存在，若否則提示使用者執行 `git init`，或自動初始化（需使用者確認）。

---

## Stage 8: psutil.NoSuchProcess 未被捕獲

原因：kill_proc_tree() 只捕獲 ImportError，沒有捕獲 psutil.NoSuchProcess（PID 不存在時拋出）。
實際：測試用無效 PID 呼叫 kill_proc_tree() 時 crash。
規則：kill_proc_tree 應在 psutil.Process(pid) 前加 try/except NoSuchProcess，PID 不存在時直接 return。

---

## Stage 9: Windows 上 Path 格式問題

原因：測試中用 `Path("/my/project")` 在 Windows 會變成 `\\my\\project`，assert `"/my/project" in result` 就失敗。
實際：test_recon_prompt_includes_path 失敗。
規則：測試 Path 是否在字串中時，用 `str(path) in result` 而不是寫死字串。

---

## Stage 10: Windows grep 不可用

原因：audit_runner.run_security_scan() 用 subprocess.run(["grep", ...])，Windows 預設沒有 grep。
實際：test_run_security_scan_detects_hardcoded_password 在 Windows 上失敗（grep 沒有 match）。
規則：涉及外部二進位工具（grep、find）的測試，應 mock subprocess.run，不依賴工具是否安裝。

---

## Stage 11: 測試先寫原則的實踐

原因：本 session 在拆分模組後立即寫測試，避免了累積型技術債。
實際：197 tests 全部通過，沒有任何「事後補測試卻發現行為不符預期」的問題。
規則：每個模組寫完後立刻補測試，不要等到所有模組都完成再寫。
