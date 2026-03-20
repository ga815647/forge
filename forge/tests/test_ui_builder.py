from unittest.mock import MagicMock, patch

from forge import ui_builder


def test_default_project_path_uses_cwd(tmp_path):
    with patch("forge.ui_builder.Path.cwd", return_value=tmp_path):
        assert ui_builder._default_project_path() == str(tmp_path)


def test_resolve_initial_directory_prefers_existing_directory(tmp_path):
    assert ui_builder._resolve_initial_directory(str(tmp_path)) == str(tmp_path)


def test_resolve_initial_directory_uses_existing_parent(tmp_path):
    existing_parent = tmp_path / "missing"
    existing_parent.mkdir()
    missing_child = existing_parent / "repo"
    assert ui_builder._resolve_initial_directory(str(missing_child)) == str(existing_parent)


def test_pick_directory_returns_selected_path(tmp_path):
    selected = str(tmp_path / "repo")
    with patch("forge.ui_builder._open_directory_dialog", return_value=selected) as open_dialog:
        result = ui_builder._pick_directory(str(tmp_path))

    assert result == selected
    open_dialog.assert_called_once_with(str(tmp_path))


def test_pick_directory_keeps_current_path_when_cancelled(tmp_path):
    current_path = str(tmp_path)
    with patch("forge.ui_builder._open_directory_dialog", return_value=""):
        assert ui_builder._pick_directory(current_path) == current_path


def test_pick_directory_keeps_current_path_when_dialog_fails(tmp_path):
    current_path = str(tmp_path)
    with patch("forge.ui_builder._open_directory_dialog", side_effect=RuntimeError("dialog failed")):
        assert ui_builder._pick_directory(current_path) == current_path


def test_open_directory_dialog_destroys_root(tmp_path):
    root = MagicMock()
    tk_module = MagicMock(Tk=MagicMock(return_value=root))
    tk_module.filedialog = MagicMock(askdirectory=MagicMock(return_value=str(tmp_path)))

    with patch.dict(
        "sys.modules",
        {
            "tkinter": tk_module,
            "tkinter.filedialog": tk_module.filedialog,
        },
    ):
        result = ui_builder._open_directory_dialog(str(tmp_path))

    assert result == str(tmp_path)
    root.withdraw.assert_called_once()
    root.destroy.assert_called_once()
