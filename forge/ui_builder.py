"""ui_builder.py - Gradio UI layout builders for Forge."""
from __future__ import annotations

from .main_config import detect_engines, save_config


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

        chatbot = gr.Chatbot(type="messages", height=500, label="Forge")

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
