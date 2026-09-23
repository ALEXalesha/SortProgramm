"""Диалог «Правила раскладки» и правила из контекстного меню окна."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QDialog

from sorter import ui_qt, ui_rules, user_rules as ur
from sorter.config import Config


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folder(tmp_path):
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    for name in ("клип.mp4", "отчёт.pdf", "видео-урок.mp4", "рецепт борща.pdf"):
        (downloads / name).write_text("x", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"], "Документы": ["отчёт"]},
        "type_map": {"Videos": ["mp4"], "Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Документы", "Videos", "Documents", "Others", "Misc"],
        "fallback_category": "Others", "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def dialog(app, folder):
    cfg = Config.load(folder / "config.json")
    dlg = ui_rules.RulesDialog(cfg, ["клип.mp4", "видео-урок.mp4", "рецепт борща.pdf"])
    yield dlg
    dlg.deleteLater()


def typed(monkeypatch, text):
    monkeypatch.setattr(ui_rules.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (text, True)))


def test_dialog_lists_program_categories(dialog):
    assert dialog.category_names() == ["Медиа", "Документы"]
    assert dialog.selected() == "Медиа"
    assert dialog.word_names() == ["клип"]


def test_add_category_from_the_dialog(dialog, monkeypatch):
    typed(monkeypatch, "Рецепты")
    dialog.add_category()
    assert dialog.category_names()[-1] == "Рецепты"
    assert dialog.selected() == "Рецепты"


def test_refused_name_is_shown_and_changes_nothing(dialog, monkeypatch):
    typed(monkeypatch, "медиа" if os.name == "nt" else "Медиа")
    dialog.add_category()
    assert "уже есть" in dialog.hint.text()
    assert dialog.edits == ur.empty()


def test_cancelled_name_input_changes_nothing(dialog, monkeypatch):
    monkeypatch.setattr(ui_rules.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Рецепты", False)))
    dialog.add_category()
    assert dialog.edits == ur.empty()


def test_word_preview_counts_files_it_would_take(dialog):
    dialog.select("Медиа")
    dialog.word_edit.setText("видео")
    assert dialog.hint.text() == "Заберёт из плана: 1 (Others: 1)."
    dialog.word_edit.setText("zzz")
    assert dialog.hint.text() == "В текущем плане таких файлов нет."
    dialog.word_edit.setText("отчёт")
    assert "уже есть в категории «Документы»" in dialog.hint.text()


def test_word_that_matches_but_moves_nothing_says_so(dialog, monkeypatch):
    """Нашлось картинкой: «таких файлов нет» про слово, которое в плане есть -
    просто файл с ним и так едет в эту категорию."""
    typed(monkeypatch, "Рецепты")
    dialog.add_category()
    dialog.word_edit.setText("рецепт")
    dialog.add_word()
    dialog.word_edit.setText("борщ")
    assert dialog.hint.text() == (
        "Файлов с этим словом в плане: 1, но куда они едут, не изменится.")


def test_add_and_remove_word(dialog):
    dialog.select("Медиа")
    dialog.word_edit.setText("видео")
    dialog.add_word()
    assert "видео" in dialog.word_names() and dialog.word_edit.text() == ""
    assert dialog.is_mine("видео") and not dialog.is_mine("клип")
    dialog.select_word("видео")
    dialog.remove_word()
    assert dialog.edits == ur.empty()


def test_rename_and_remove_category(dialog, monkeypatch):
    dialog.select("Медиа")
    typed(monkeypatch, "Видео")
    dialog.rename_category()
    assert dialog.category_names() == ["Видео", "Документы"]
    assert dialog.selected() == "Видео"
    dialog.remove_category()
    assert dialog.category_names() == ["Документы"]
    dialog.remove_category()
    assert "последняя" in dialog.hint.text()
    assert dialog.category_names() == ["Документы"]


def test_created_and_renamed_categories_are_marked(dialog, monkeypatch):
    typed(monkeypatch, "Рецепты")
    dialog.add_category()
    marks = {dialog.cats.item(r).data(ui_rules.Qt.ItemDataRole.UserRole):
             dialog.cats.item(r).text().endswith(ui_rules.MINE)
             for r in range(dialog.cats.count())}
    assert marks == {"Медиа": False, "Документы": False, "Рецепты": True}


def test_cancel_writes_nothing(dialog, folder, monkeypatch):
    typed(monkeypatch, "Рецепты")
    dialog.add_category()
    dialog.reject()
    assert not ur.path_for(folder / "config.json").exists()


# --- окно ---

@pytest.fixture
def window(app, folder, monkeypatch):
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(ui_qt.QMessageBox, kind, staticmethod(lambda *a, **k: None))
    win = ui_qt.GlassWindow(folder / "config.json")
    win.preview()
    yield win
    win.deleteLater()


def row_of(win, name):
    return next(i for i, mv in enumerate(win.moves) if mv.src.name == name)


def category_of(win, name):
    return win.moves[row_of(win, name)].dst.parent.parent.name


def test_context_menu_puts_a_file_into_a_category(window, folder):
    menu = window.rule_menu(row_of(window, "рецепт борща.pdf"))
    into = next(a.menu() for a in menu.actions() if a.text() == "Всегда класть в")
    next(a for a in into.actions() if a.text() == "Медиа").trigger()
    saved = ur.read(ur.path_for(folder / "config.json"), [])[0]
    assert saved["files"] == {"рецепт борща.pdf": "Медиа"}
    assert category_of(window, "рецепт борща.pdf") == "Медиа"


def test_context_menu_offers_removal_only_when_there_is_a_rule(window):
    row = row_of(window, "рецепт борща.pdf")
    assert "Убрать моё правило" not in [a.text() for a in window.rule_menu(row).actions()]
    window.set_file_rule("рецепт борща.pdf", "Медиа")
    menu = window.rule_menu(row_of(window, "рецепт борща.pdf"))
    next(a for a in menu.actions() if a.text() == "Убрать моё правило").trigger()
    assert category_of(window, "рецепт борща.pdf") == "Others"


def test_no_menu_outside_the_rows(window):
    assert window.rule_menu(-1) is None
    assert window.rule_menu(len(window.moves)) is None


def test_rules_button_saves_and_rebuilds_the_plan(window, folder, monkeypatch):
    def accept(self):
        self.edits = ur.add_category(self.edits, self.config.base, "Рецепты")
        self.edits = ur.add_word(self.edits, self.config.base, "Рецепты", "рецепт")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui_rules.RulesDialog, "exec", accept)
    window.show_rules()
    assert "Рецепты" in window.config.categories
    assert category_of(window, "рецепт борща.pdf") == "Рецепты"
    assert ur.path_for(folder / "config.json").exists()


def test_rules_button_cancel_writes_nothing(window, folder, monkeypatch):
    monkeypatch.setattr(ui_rules.RulesDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    window.show_rules()
    assert not ur.path_for(folder / "config.json").exists()


def test_unreadable_rules_file_is_not_overwritten(window, folder):
    path = ur.path_for(folder / "config.json")
    path.write_text("{", encoding="utf-8")
    window.config = Config.load(folder / "config.json")
    window.set_file_rule("отчёт.pdf", "Медиа")
    assert path.read_text(encoding="utf-8") == "{"
