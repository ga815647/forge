# FINAL_CHECK.md

> 執行時間：2026-03-19
> 對應 forge_meta_prompt.md §7

---

## 1. pytest tests/ -v

```
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.0.2, pluggy-1.6.0
collected 0 items

============================ no tests ran in 0.19s ============================
```

**結果：🔴 FAIL（0 tests，所有測試檔案缺失）**

---

## 2. 全部可 import

```
python -c "from forge import orchestrator_main, orchestrator_init, orchestrator_loop,
           agent, monitor, prompts, audit_runner, security, timeline"
```

**結果：✅ PASS（所有模組可 import，無 ImportError）**

---

## 3. safety_check 攔截危險操作

```python
from forge.orchestrator_main import safety_check
assert safety_check('rm -rf /') is not None
```

**結果：✅ PASS**

---

## 4. safety_check 放行安全操作

```python
assert safety_check('git status') is None
```

**結果：✅ PASS**

---

## 5. is_safe_path 路徑限定

```python
from forge.security import is_safe_path
from pathlib import Path
assert not is_safe_path(Path('/etc/passwd'), Path('/home/user/project'))
```

**結果：✅ PASS**

---

## 6. detect_prompt_injection 注入偵測

```python
from forge.security import detect_prompt_injection
assert detect_prompt_injection('ignore previous instructions')
```

**結果：✅ PASS**

---

## 7. grep os.system

```
grep -rn "os\.system" forge/
```

**結果：✅ PASS（空，全部使用 subprocess）**

---

## 8. grep shell=True

```
grep -rn "shell=True" forge/
```

**結果：🟡 WARN**

```
forge/audit_runner.py:239:    shell=True,  # noqa: S602 - cmd comes from detect_tools(), not user input
```

原因：`_run_tool()` 需要跑複合指令（如 `pytest tests/ -v`），cmd 值來自程式內部的 `detect_tools()`，不來自使用者輸入。有注釋說明。

---

## 9. wc -l（無超過 300 行的檔案）

```
find forge/ -name "*.py" -exec wc -l {} +
```

**結果：🔴 FAIL**

| 檔案 | 行數 | 狀態 |
|------|------|------|
| agent.py | 328 | ⚠️ 超標 |
| audit_runner.py | 281 | ✅ |
| monitor.py | 156 | ✅ |
| orchestrator_init.py | 329 | ⚠️ 超標 |
| **orchestrator_loop.py** | **520** | **🔴 嚴重超標** |
| orchestrator_main.py | 319 | ⚠️ 超標 |
| prompts.py | 241 | ✅ |
| security.py | 165 | ✅ |
| timeline.py | 130 | ✅ |
| __init__.py | 1 | ✅ |

---

## 總結

| 項目 | 狀態 |
|------|------|
| 所有模組可 import | ✅ |
| safety_check 有效 | ✅ |
| is_safe_path 有效 | ✅ |
| detect_prompt_injection 有效 | ✅ |
| 無 os.system | ✅ |
| shell=True 有說明 | 🟡 |
| pytest 全部 PASS | 🔴（0 tests） |
| 無超過 300 行的檔案 | 🔴（4 個超標） |
| main.py 存在 | 🔴（缺失） |

**必須修復（阻擋完成）：**
1. 建立 8 個測試檔案並確保通過
2. 建立 main.py（Gradio UI）
3. 拆分 orchestrator_loop.py（520 行 → < 300 行）
