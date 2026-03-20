"""ui_builder.py - Gradio UI layout builders for Forge."""
from __future__ import annotations

from .main_config import detect_engines, load_config, save_config


def build_combined_ui(chat_fn, stop_fn, rollback_fn, commits_fn, cost_summary_fn):
    """Single Blocks UI with settings, chat, and live backend log."""
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

    engine_choices = [name for name, meta in engines.items() if meta.get("installed")] or [
        "claude",
        "codex",
    ]
    default_engine = cfg.get("default_engine", engine_choices[0])
    if default_engine not in engine_choices:
        default_engine = engine_choices[0]

    initial_history = []
    if initial_cfg is None:
        initial_history = [
            {
                "role": "assistant",
                "content": (
                    f"Claude CLI: {'installed' if engines['claude']['installed'] else 'missing'}\n"
                    f"Codex CLI: {'installed' if engines['codex']['installed'] else 'missing'}\n\n"
                    f"Default engine: **{default_engine}**"
                ),
            }
        ]

    with gr.Blocks(title="Forge") as app:
        with gr.Row():
            project_path_box = gr.Textbox(
                label="Project Path",
                placeholder="e.g. C:/Users/me/myproject",
                scale=4,
            )
            engine_dd = gr.Dropdown(engine_choices, value=default_engine, label="Engine", scale=2)
            mode_dd = gr.Dropdown(
                ["forge", "direct"],
                value=cfg.get("default_mode", "forge"),
                label="Mode",
                scale=2,
            )
            review_chk = gr.Checkbox(
                value=cfg.get("review_mode", False),
                label="Review Mode",
                scale=1,
            )
            settings_toggle = gr.Button("Settings", scale=1, min_width=80)

        with gr.Row():
            with gr.Column(scale=4):
                chatbot = gr.Chatbot(value=initial_history, height=460, label="Forge")

                rollback_radio = gr.Radio(choices=[], label="Rollback Target", visible=False)
                with gr.Row(visible=False) as rollback_action_row:
                    rollback_confirm_btn = gr.Button("Confirm Rollback", variant="primary")
                    rollback_cancel_btn = gr.Button("Cancel")

                with gr.Row():
                    msg_box = gr.Textbox(
                        placeholder="Describe the task...",
                        label="",
                        scale=6,
                        lines=2,
                    )
                    submit_btn = gr.Button("Send", variant="primary", scale=1)

                with gr.Row():
                    stop_btn = gr.Button("Stop", scale=1)
                    rollback_btn = gr.Button("Rollback", scale=1)

                cost_display = gr.Textbox(label="Token Summary", interactive=False)
                live_log_display = gr.Textbox(
                    label="Live Log",
                    interactive=False,
                    lines=14,
                    max_lines=18,
                    autoscroll=True,
                )

            with gr.Column(scale=1, visible=initial_cfg is None) as settings_col:
                gr.Markdown("## Settings")
                gr.Markdown(
                    f"- Claude: {'installed' if engines['claude']['installed'] else 'missing'}\n"
                    f"- Codex: {'installed' if engines['codex']['installed'] else 'missing'}"
                )

                s_engine = gr.Dropdown(
                    ["claude", "codex"],
                    value=cfg.get("default_engine", "claude"),
                    label="Default Engine",
                )
                s_mode = gr.Dropdown(
                    ["forge", "direct"],
                    value=cfg.get("default_mode", "forge"),
                    label="Default Mode",
                )
                s_review = gr.Checkbox(
                    value=cfg.get("review_mode", False),
                    label="Review Mode",
                )
                s_warn = gr.Slider(
                    50,
                    99,
                    value=cfg.get("token_warning_pct", 85),
                    step=1,
                    label="Token Warning %",
                )
                s_kill = gr.Slider(
                    60,
                    100,
                    value=cfg.get("token_kill_pct", 95),
                    step=1,
                    label="Token Kill %",
                )
                save_btn = gr.Button("Save", variant="primary")
                save_msg = gr.Textbox(label="", interactive=False, lines=1)

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
                "engines": detect_engines(),
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
                "Saved.",
            )

        save_btn.click(
            do_save,
            inputs=[s_engine, s_mode, s_review, s_warn, s_kill],
            outputs=[
                engine_dd,
                mode_dd,
                review_chk,
                settings_col,
                settings_visible_state,
                save_msg,
            ],
        )

        def submit(message, history, path, engine, mode, review):
            gen = chat_fn(message, history, path, engine, mode, review)
            for updated_history, live_log in gen:
                yield updated_history, cost_summary_fn(), "", live_log

        submit_btn.click(
            submit,
            inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
            outputs=[chatbot, cost_display, msg_box, live_log_display],
        )
        msg_box.submit(
            submit,
            inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
            outputs=[chatbot, cost_display, msg_box, live_log_display],
        )

        stop_btn.click(stop_fn, outputs=cost_display)

        def show_rollback(path, history):
            commits_text = commits_fn(path)
            lines = [line.strip() for line in commits_text.splitlines() if line.strip()]
            choices = [line.replace("`", "").strip() for line in lines]
            msg = "**Rollback candidates**\n\n" + commits_text
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
                new_history = history + [
                    {"role": "assistant", "content": "Select a commit before rollback."}
                ]
                return new_history, gr.update(visible=True), gr.update(visible=True)

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
            new_history = history + [{"role": "assistant", "content": "Rollback cancelled."}]
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

    with gr.Blocks(title="Forge Setup") as setup_ui:
        gr.Markdown("# Forge Setup")
        gr.Markdown(
            f"- Claude: {'installed' if engines['claude']['installed'] else 'missing'}\n"
            f"- Codex: {'installed' if engines['codex']['installed'] else 'missing'}"
        )

        with gr.Row():
            default_engine = gr.Dropdown(["claude", "codex"], value="claude", label="Default Engine")
            default_mode = gr.Dropdown(["forge", "direct"], value="forge", label="Default Mode")

        review_chk = gr.Checkbox(value=False, label="Review Mode")

        with gr.Row():
            warn_pct = gr.Slider(50, 99, value=85, step=1, label="Token Warning %")
            kill_pct = gr.Slider(60, 100, value=95, step=1, label="Token Kill %")

        save_btn = gr.Button("Save and Continue", variant="primary")
        status_msg = gr.Textbox(label="Status", interactive=False)

        def do_save(eng, mode, review, warn, kill):
            cfg_to_save.update(
                {
                    "default_engine": eng,
                    "default_mode": mode,
                    "review_mode": review,
                    "token_warning_pct": int(warn),
                    "token_kill_pct": int(kill),
                }
            )
            save_config(cfg_to_save)
            return "Saved. Restart Forge to pick up the new defaults."

        save_btn.click(
            do_save,
            inputs=[default_engine, default_mode, review_chk, warn_pct, kill_pct],
            outputs=status_msg,
        )

    return setup_ui


def build_main_ui(cfg: dict, chat_fn, stop_fn, rollback_fn, commits_fn, cost_summary_fn):
    """Main chat UI."""
    import gradio as gr

    engine_choices = [name for name, meta in cfg.get("engines", {}).items() if meta.get("installed")]
    if not engine_choices:
        engine_choices = ["claude", "codex"]
    default_engine = cfg.get("default_engine", engine_choices[0])
    if default_engine not in engine_choices:
        default_engine = engine_choices[0]

    with gr.Blocks(title="Forge") as main_ui:
        with gr.Row():
            project_path_box = gr.Textbox(
                label="Project Path",
                placeholder="e.g. C:/Users/me/myproject",
                scale=4,
            )

        with gr.Row():
            engine_dd = gr.Dropdown(engine_choices, value=default_engine, label="Engine", scale=2)
            mode_dd = gr.Dropdown(
                ["forge", "direct"],
                value=cfg.get("default_mode", "forge"),
                label="Mode",
                scale=2,
            )
            review_chk = gr.Checkbox(value=cfg.get("review_mode", False), label="Review Mode", scale=1)

        chatbot = gr.Chatbot(height=500, label="Forge")

        with gr.Row():
            msg_box = gr.Textbox(placeholder="Describe the task...", label="", scale=6, lines=2)
            submit_btn = gr.Button("Send", variant="primary", scale=1)

        with gr.Row():
            stop_btn = gr.Button("Stop", scale=1)
            rollback_box = gr.Textbox(placeholder="commit hash...", label="Rollback Hash", scale=3)
            rollback_btn = gr.Button("Rollback", scale=1)
            commits_btn = gr.Button("List Commits", scale=1)

        cost_display = gr.Textbox(label="Token Summary", interactive=False)
        live_log_display = gr.Textbox(
            label="Live Log",
            interactive=False,
            lines=14,
            max_lines=18,
            autoscroll=True,
        )

        def submit(message, history, path, engine, mode, review):
            gen = chat_fn(message, history, path, engine, mode, review)
            for updated_history, live_log in gen:
                yield updated_history, cost_summary_fn(), "", live_log

        submit_btn.click(
            submit,
            inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
            outputs=[chatbot, cost_display, msg_box, live_log_display],
        )
        msg_box.submit(
            submit,
            inputs=[msg_box, chatbot, project_path_box, engine_dd, mode_dd, review_chk],
            outputs=[chatbot, cost_display, msg_box, live_log_display],
        )

        stop_btn.click(stop_fn, outputs=cost_display)
        rollback_btn.click(rollback_fn, inputs=[project_path_box, rollback_box], outputs=cost_display)
        commits_btn.click(commits_fn, inputs=[project_path_box], outputs=cost_display)

    return main_ui
