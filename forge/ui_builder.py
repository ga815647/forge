"""ui_builder.py - Gradio UI layout builders for Forge."""
from __future__ import annotations

from .main_config import detect_engines, load_config, save_config


def build_combined_ui(chat_fn, stop_fn, rollback_fn, commits_fn, cost_summary_fn):
    """Single Blocks UI: settings side-panel + main chat + radio-based rollback."""
    import gradio as gr

    initial_cfg = load_config()
    engines = detect_engines()
    cfg = initial_cfg or {
        "engines": engines,
        "default_engine": "claude",
        "default_mode": "forge",
        "review_mode": False,
        "token_warning_pct": 85,
        "token_kill_pct": 95,
    }

    engine_choices = [k for k, v in engines.items() if v.get("installed")] or ["claude", "codex"]
    default_engine = cfg.get("default_engine", engine_choices[0])
    if default_engine not in engine_choices:
        default_engine = engine_choices[0]

    # Welcome message for first run
    if initial_cfg is None:
        claude_s = "✅" if engines["claude"]["installed"] else "❌"
        codex_s  = "✅" if engines["codex"]["installed"] else "❌"
        welcome = (
            f"Claude CLI {claude_s}　Codex CLI {codex_s}\n\n"
            f"預設使用 **{default_engine}**。可在右側調整設定，或直接開始。"
        )
        initial_history = [{"role": "assistant", "content": welcome}]
    else:
        initial_history = []

    with gr.Blocks(title="Forge") as app:

        # ── Top toolbar ────────────────────────────────────────────────────────
        with gr.Row():
            project_path_box = gr.Textbox(
                label="專案路徑", placeholder="e.g. C:/Users/me/myproject", scale=4
            )
            engine_dd = gr.Dropdown(engine_choices, value=default_engine, label="Engine", scale=2)
            mode_dd   = gr.Dropdown(["forge", "direct"], value=cfg.get("default_mode", "forge"),
                                    label="模式", scale=2)
            review_chk = gr.Checkbox(value=cfg.get("review_mode", False), label="審核模式", scale=1)
            settings_toggle = gr.Button("⚙️", scale=1, min_width=60)

        # ── Main area: chat (left) + settings panel (right) ───────────────────
        with gr.Row():

            # Left: always visible chat column
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(
                    value=initial_history, height=460, label="Forge",
                )

                # Rollback area (hidden until ↩️ pressed)
                rollback_radio = gr.Radio(
                    choices=[], label="選擇回滾點", visible=False
                )
                with gr.Row(visible=False) as rollback_action_row:
                    rollback_confirm_btn = gr.Button("確認回滾", variant="primary", scale=1)
                    rollback_cancel_btn  = gr.Button("取消", scale=1)

                # Bottom input toolbar
                with gr.Row():
                    msg_box    = gr.Textbox(placeholder="輸入你的需求或指令...",
                                            label="", scale=6, lines=2)
                    submit_btn = gr.Button("送出", variant="primary", scale=1)

                with gr.Row():
                    stop_btn     = gr.Button("🛑 停止", scale=1)
                    rollback_btn = gr.Button("↩️ 回滾", scale=1)

                cost_display = gr.Textbox(label="Token 用量", interactive=False)

            # Right: settings panel (hidden after first save)
            with gr.Column(scale=1, visible=initial_cfg is None) as settings_col:
                gr.Markdown("## ⚙️ 設定")
                claude_s = "✅ 已安裝" if engines["claude"]["installed"] else "❌ 未安裝"
                codex_s  = "✅ 已安裝" if engines["codex"]["installed"] else "❌ 未安裝"
                gr.Markdown(f"- Claude: {claude_s}\n- Codex: {codex_s}")

                s_engine  = gr.Dropdown(["claude", "codex"],
                                        value=cfg.get("default_engine", "claude"),
                                        label="預設 Engine")
                s_mode    = gr.Dropdown(["forge", "direct"],
                                        value=cfg.get("default_mode", "forge"),
                                        label="預設模式")
                s_review  = gr.Checkbox(value=cfg.get("review_mode", False),
                                        label="審核模式（每輪確認）")
                s_warn    = gr.Slider(50, 99,  value=cfg.get("token_warning_pct", 85),
                                     step=1, label="Token 警告 %")
                s_kill    = gr.Slider(60, 100, value=cfg.get("token_kill_pct", 95),
                                     step=1, label="Token 強制停止 %")
                save_btn  = gr.Button("儲存", variant="primary")
                save_msg  = gr.Textbox(label="", interactive=False, lines=1)

        # ── Callbacks ──────────────────────────────────────────────────────────

        settings_visible_state = gr.State(value=initial_cfg is None)

        def _toggle(visible):
            new_val = not visible
            return gr.update(visible=new_val), new_val

        settings_toggle.click(
            _toggle,
            inputs=settings_visible_state,
            outputs=[settings_col, settings_visible_state],
        )

        def do_save(eng, mode, review, warn, kill):
            cfg_new = {
                "engines": engines,
                "default_engine": eng,
                "default_mode": mode,
                "review_mode": review,
                "token_warning_pct": int(warn),
                "token_kill_pct": int(kill),
            }
            save_config(cfg_new)
            return (
                gr.update(value=eng),
                gr.update(value=mode),
                gr.update(value=review),
                gr.update(visible=False),
                False,
                "✅ 已儲存",
            )

        save_btn.click(
            do_save,
            inputs=[s_engine, s_mode, s_review, s_warn, s_kill],
            outputs=[engine_dd, mode_dd, review_chk, settings_col,
                     settings_visible_state, save_msg],
        )

        def submit(message, history, path, engine, mode, review):
            gen = chat_fn(message, history, path, engine, mode, review)
            for updated_history in gen:
                yield updated_history, cost_summary_fn(), ""

        submit_btn.click(
            submit,
            inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
            outputs=[chatbot, cost_display, msg_box],
        )
        msg_box.submit(
            submit,
            inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
            outputs=[chatbot, cost_display, msg_box],
        )

        stop_btn.click(stop_fn, outputs=cost_display)

        # Rollback flow
        def show_rollback(path, history):
            commits_text = commits_fn(path)
            # Parse into choices: "hash8 — message" per line
            lines = [l.strip() for l in commits_text.splitlines() if l.strip()]
            # strip markdown backticks if present (format: `hash8` message)
            choices = []
            for line in lines:
                # remove leading backtick-hash-backtick pattern
                clean = line.replace("`", "").strip()
                choices.append(clean)

            msg = "**可以回滾到以下時間點**（選擇後點「確認回滾」）：\n\n" + commits_text
            new_history = history + [{"role": "assistant", "content": msg}]
            return (
                new_history,
                gr.update(choices=choices, value=None, visible=True),
                gr.update(visible=True),
            )

        rollback_btn.click(
            show_rollback,
            inputs=[project_path_box, chatbot],
            outputs=[chatbot, rollback_radio, rollback_action_row],
        )

        def do_rollback(path, selected, history):
            if not selected:
                new_history = history + [{"role": "assistant", "content": "請先選擇一個回滾點。"}]
                return new_history, gr.update(visible=True), gr.update(visible=True)
            # Extract hash (first token)
            target_hash = selected.split()[0]
            result = rollback_fn(path, target_hash)
            new_history = history + [{"role": "assistant", "content": result}]
            return (
                new_history,
                gr.update(choices=[], value=None, visible=False),
                gr.update(visible=False),
            )

        rollback_confirm_btn.click(
            do_rollback,
            inputs=[project_path_box, rollback_radio, chatbot],
            outputs=[chatbot, rollback_radio, rollback_action_row],
        )

        def cancel_rollback(history):
            new_history = history + [{"role": "assistant", "content": "取消回滾。"}]
            return (
                new_history,
                gr.update(choices=[], value=None, visible=False),
                gr.update(visible=False),
            )

        rollback_cancel_btn.click(
            cancel_rollback,
            inputs=[chatbot],
            outputs=[chatbot, rollback_radio, rollback_action_row],
        )

    return app


def build_setup_ui():
    """First-run setup screen."""
    import gradio as gr

    engines = detect_engines()
    cfg_to_save: dict = {"engines": engines}

    with gr.Blocks(title="Forge 初始設定") as setup_ui:
        gr.Markdown("# Forge 初始設定")
        claude_s = "✅ 已安裝" if engines["claude"]["installed"] else "❌ 未安裝"
        codex_s = "✅ 已安裝" if engines["codex"]["installed"] else "❌ 未安裝"
        gr.Markdown(f"- Claude: {claude_s}\n- Codex: {codex_s}")

        with gr.Row():
            default_engine = gr.Dropdown(["claude", "codex"], value="claude", label="預設 Engine")
            default_mode = gr.Dropdown(["forge", "direct"], value="forge", label="預設模式")

        review_chk = gr.Checkbox(value=False, label="審核模式（每輪確認）")

        with gr.Row():
            warn_pct = gr.Slider(50, 99, value=85, step=1, label="Token 警告 %")
            kill_pct = gr.Slider(60, 100, value=95, step=1, label="Token 強制停止 %")

        save_btn = gr.Button("儲存並開始", variant="primary")
        status_msg = gr.Textbox(label="狀態", interactive=False)

        def do_save(eng, mode, review, warn, kill):
            cfg_to_save.update({
                "default_engine": eng, "default_mode": mode,
                "review_mode": review, "token_warning_pct": int(warn),
                "token_kill_pct": int(kill),
            })
            save_config(cfg_to_save)
            return "✅ 設定已儲存，請重新啟動 Forge"

        save_btn.click(do_save,
                       inputs=[default_engine, default_mode, review_chk, warn_pct, kill_pct],
                       outputs=status_msg)

    return setup_ui


def build_main_ui(cfg: dict, chat_fn, stop_fn, rollback_fn, commits_fn, cost_summary_fn):
    """Main chat UI."""
    import gradio as gr

    engine_choices = [k for k, v in cfg.get("engines", {}).items() if v.get("installed")]
    if not engine_choices:
        engine_choices = ["claude", "codex"]
    default_engine = cfg.get("default_engine", engine_choices[0])
    if default_engine not in engine_choices:
        default_engine = engine_choices[0]

    with gr.Blocks(title="Forge") as main_ui:
        with gr.Row():
            project_path_box = gr.Textbox(label="專案路徑",
                                          placeholder="e.g. C:/Users/me/myproject", scale=4)

        with gr.Row():
            engine_dd = gr.Dropdown(engine_choices, value=default_engine, label="Engine", scale=2)
            mode_dd = gr.Dropdown(["forge", "direct"], value=cfg.get("default_mode", "forge"),
                                  label="模式", scale=2)
            review_chk = gr.Checkbox(value=cfg.get("review_mode", False), label="審核模式", scale=1)

        chatbot = gr.Chatbot(height=500, label="Forge")

        with gr.Row():
            msg_box = gr.Textbox(placeholder="輸入你的需求或指令...", label="", scale=6, lines=2)
            submit_btn = gr.Button("送出", variant="primary", scale=1)

        with gr.Row():
            stop_btn = gr.Button("停止", scale=1)
            rollback_box = gr.Textbox(placeholder="commit hash...", label="回滾到", scale=3)
            rollback_btn = gr.Button("↩️ 回滾", scale=1)
            commits_btn = gr.Button("查看 commits", scale=1)

        cost_display = gr.Textbox(label="Token 用量", interactive=False)

        def submit(message, history, path, engine, mode, review):
            gen = chat_fn(message, history, path, engine, mode, review)
            for updated_history in gen:
                yield updated_history, cost_summary_fn(), ""

        submit_btn.click(submit,
                         inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
                         outputs=[chatbot, cost_display, msg_box])
        msg_box.submit(submit,
                       inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
                       outputs=[chatbot, cost_display, msg_box])

        stop_btn.click(stop_fn, outputs=cost_display)
        rollback_btn.click(rollback_fn, inputs=[project_path_box, rollback_box], outputs=cost_display)
        commits_btn.click(commits_fn, inputs=[project_path_box], outputs=cost_display)

    return main_ui
