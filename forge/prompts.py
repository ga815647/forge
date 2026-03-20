"""prompts.py - All prompt templates as pure functions returning strings."""
from __future__ import annotations

from pathlib import Path

# ── Anti-hallucination suffix injected into every think()/do() call ───────────

ANTI_HALLUCINATION = """

重要警告：
- 如果你不確定某件事，說「我不確定」，不要編造答案
- 如果你沒在文件裡看到某些你被求的東西，不要假設它存在
- 如果使用者的描述有多種解讀，列出所有可能性，不要選一個當預設
- 引用具體文件路徑和行號來支持你的判斷
- 如果你的判斷基於推測，明確標注 ⚠️ 推測"""

COMMAND_CONFIRM_PREFIX = """在執行任何動作前，判斷這個命令是否有模糊或歧義之處。
如果涉及刪除檔案、覆寫資料、或修改 config，則列出影響範圍。
如果有疑問，則報告並不是自行假設。

"""

# ── Info quality format (injected into all .agent/*.md output) ────────────────

INFO_QUALITY_FORMAT = """
請用以下格式標注資訊：
### 已確認（來源）
- ...（這個資訊來自哪個檔案或使用者對話）

### ⚠️ 推測（依據）
- ...（這個推測依據是什麼）

### ❓ 未知（待確認）
- [ ] ...
"""


# ── Prompt functions ──────────────────────────────────────────────────────────


def recon_prompt(project_path: Path) -> str:
    return f"""你正在偵察目標專案目錄：{project_path}

請列出：
1. 目錄結構（最多 2 層深，跳過 node_modules/.venv/.git/dist/build/__pycache__）
2. 發現的主要配置檔案（package.json, pyproject.toml, Cargo.toml, README 等）
3. git log 最近 10 筆（如果有）
4. 主要程式語言和框架

只報告你看到的。不要推測。不要提建議。{ANTI_HALLUCINATION}"""


def preflight_prompt(recon: str, user_input: str, chunk_titles: list[str]) -> str:
    chunks_section = ""
    if chunk_titles:
        chunks_section = "\n\n## 附件 chunks 標題列表\n" + "\n".join(
            f"- {t}" for t in chunk_titles
        )

    return f"""你是 Forge 的 pre-flight 分析師。

## 偵察結果
{recon}

## 使用者需求
{user_input}
{chunks_section}

請完成以下任務：
1. **判斷任務類型**：建新系統 / 改既有程式 / 改 prompt / 其他
2. **找出矛盾和缺失**：需求和現實是否衝突？缺少哪些資訊？
3. **生成 purpose.md**：包含目的、不是什麼、成功標準、確認 checklist
4. **生成 architecture.md**：你自己決定架構，不要等指示
5. **生成 skill.md**：根據技術棧自動生成踩坑提醒（根據偵察到的語言/框架）
6. **生成 meta.md**：從 purpose 推導品質底線 + 原子定義 + 中斷防禦規則
7. **生成 plan.md**：依賴有序的執行計劃，每步是原子操作

{INFO_QUALITY_FORMAT}
{ANTI_HALLUCINATION}"""


def plan_prompt(
    purpose: str, architecture: str, skill: str, chunk_summaries: list[str]
) -> str:
    chunks_section = ""
    if chunk_summaries:
        chunks_section = "\n\n## 附件內容摘要\n" + "\n\n".join(chunk_summaries)

    return f"""根據以下文件生成執行計劃。

## purpose.md
{purpose}

## architecture.md
{architecture}

## skill.md
{skill}
{chunks_section}

請生成 plan.md：
- 每個步驟是一個原子操作（一次 do() 能完成）
- 標注依賴關係（哪步完成才能做哪步）
- 標注預估複雜度（輕量/中等/複雜）
- 未知的東西用 ❓ 標注，不要假設

{ANTI_HALLUCINATION}"""


def task_prompt(
    current_task: str, skill: str, lower_progress: str
) -> str:
    return f"""{COMMAND_CONFIRM_PREFIX}## 當前任務
{current_task}

## 技術踩坑提醒（skill.md）
{skill}

## 已完成的進度（lower/progress.md）
{lower_progress}

請執行當前任務。完成後報告：
1. 修改了哪些檔案（具體路徑）
2. 遇到了什麼問題（如果有）
3. 是否完成（yes/partial/blocked）

{ANTI_HALLUCINATION}"""


def judge_prompt(summary: str, plan: str, purpose: str) -> str:
    return f"""你是 Forge 的品質評審。

## 本輪執行摘要
{summary}

## 計劃（plan.md）
{plan}

## 目標（purpose.md）
{purpose}

請評審：
1. 本輪做的事是否符合計劃？
2. 是否有偏離目標？
3. audit/測試結果如何？
4. 下一步應該做什麼？
5. 是否建議實測（輸出「建議實測」字樣）？

{INFO_QUALITY_FORMAT}
{ANTI_HALLUCINATION}"""


def compress_prompt(content: str, max_lines: int = 100) -> str:
    return f"""請將以下文件壓縮到 {max_lines} 行以內。
只保留下一輪需要知道的資訊。
刪除已完成、已確認、重複的內容。
保留格式（markdown headers）。
不要新增資訊。

---
{content}
---

輸出壓縮後的全文，不要加任何說明。"""


def review_prompt(content: str) -> str:
    return f"""你是 Forge 的文件審查員。審查以下 .agent/ 文件。

{content}

請回答（只選一個）：
- ✅ 通過：內容正確、完整、沒有幻覺
- ⚡ 有問題：[具體描述問題，並給出修正版本]
- 🌸 禪身：內容太長，建議刪除哪些部分

若選 ⚡，請直接輸出修正後的完整文件內容（不要只說「應該改成...」）。
若選 ✅ 或 🌸，只輸出那個 emoji 加簡短說明。"""


def quick_review_prompt(content: str) -> str:
    return f"""快速審查以下文件，只看 ⚡ 問題（幻覺、矛盾、明顯錯誤）：

{content}

回答：
- ✅ 沒問題
- ⚡ [問題描述] → [修正版本]

不要審查風格、格式、完整性。只看錯誤。"""


def slim_prompt(content: str) -> str:
    return f"""以下文件太長了。請刪除不重要的部分，保留核心資訊。
目標：刪除 30% 以上的內容，但不損失關鍵資訊。

{content}

只輸出精簡後的文件，不要說明。"""


def reality_check_prompt(recon: str, context: str) -> str:
    return f"""幻覺自查。

## 原始偵察（recon.md）
{recon}

## 當前認知（context.md）
{context}

請逐條檢查 context.md 中的每個「已確認」事項：
- 它在 recon.md 裡有依據嗎？
- 還是我自己推測出來的？

輸出格式：
- [✅ 有依據] 事項名稱
- [⚠️ 可能是推測] 事項名稱 → 依據是什麼

{ANTI_HALLUCINATION}"""


def clarification_prompt(
    user_input: str,
    recon_summary: str,
    interpretations: list[str],
    conflicts: list[str],
) -> str:
    """Generate a blocking clarification message for the user.

    Covers: prompt ambiguity, recon confirmation, assumption conflicts.
    All three are shown in one message to avoid multiple round-trips.
    """
    parts: list[str] = []

    parts.append("## 👀 Forge 在開始前需要你確認幾件事\n")
    parts.append("請直接回覆。模糊回答沒關係，Forge 會推斷你的意圖。\n")
    parts.append("---\n")

    if interpretations:
        parts.append("### 1. 你的需求，我的解讀")
        parts.append("我把你說的理解成以下幾種可能，請確認哪個最接近：")
        for i, interp in enumerate(interpretations, 1):
            parts.append(f"  {i}. {interp}")
        parts.append("")

    parts.append("### 2. 我對這個程式的認識")
    parts.append(recon_summary)
    parts.append("（這個認識正確嗎？有哪裡不對請告訴我。）\n")

    if conflicts:
        parts.append("### 3. 我發現以下可能的衝突")
        for c in conflicts:
            parts.append(f"  - {c}")
        parts.append("")

    parts.append("---")
    parts.append("你可以直接說「對」、「繼續」，或針對任何一點補充說明。")

    return "\n".join(parts)


def purpose_update_prompt(
    current_purpose: str,
    user_message: str,
    do_result: str,
) -> str:
    """Generate a prompt to incrementally update purpose.md based on this round's signals.

    Extracts directional intent from user_message and do_result,
    appends to the existing purpose without overwriting it.
    """
    return f"""你是 Forge 的方向追蹤器。

## 目前的 purpose.md
{current_purpose}

## 這輪使用者說的話
{user_message}

## 這輪做了什麼
{do_result[:400]}

請只補充「## 累積方向」這個區段。格式：
- 從使用者這輪的話，推斷出對程式方向有意義的新資訊
- 若無新資訊，輸出「（本輪無新方向訊號）」
- 不要修改 purpose.md 的其他區段
- 不要重複已經記錄過的內容

只輸出「## 累積方向」這個區段的新增內容，不要輸出整份 purpose.md。

{ANTI_HALLUCINATION}"""


def doc_prompt(purpose: str, architecture: str, timeline: str) -> str:
    return f"""根據以下文件生成專案文件。

## purpose.md
{purpose}

## architecture.md
{architecture}

## timeline.md（最後 20 輪）
{timeline}

請生成適合這個專案的文件。判斷需要哪些：
- README.md（幾乎必要）
- USAGE.md（如果有複雜使用流程）
- DEPLOY.md（如果有部署步驟）
- API.md（如果有 API）
- CHANGELOG.md（如果有版本歷史）

從 .agent/ 現有文件組裝，不是從頭寫。直接輸出內容。{ANTI_HALLUCINATION}"""
