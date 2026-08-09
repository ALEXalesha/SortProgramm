"""Окно «🕘 История»: список прошлых сортировок и откат любой из них.

Тестов у него до сих пор не было вовсе — только разовые пробники, — при том
что оно единственное умеет вернуть файлы назад. Окно поднимается в
offscreen-режиме, подтверждение и сообщения подменяются: от них нужен текст.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication, QMessageBox

from sorter import ui_qt
from sorter.config import Config
from sorter.mover import apply
from sorter.planner import build_plan

RULES = {
    "categories": {"Учёба": ["лекция"], "Медиа": ["клип"]},
    "type_map": {"Documents": ["txt"], "Videos": ["mp4"]},
    "managed_folders": ["Учёба", "Медиа", "Documents", "Videos",
                        "Others", "Misc"],
    "fallback_category": "Others",
    "fallback_type": "Misc",
}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sandbox(tmp_path):
    """Папка загрузок с правилами рядом. Возвращает (config_path, загрузки)."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (tmp_path / "rules.json").write_text(
        json.dumps(RULES, ensure_ascii=False), encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    return config_path, downloads


@pytest.fixture
def answers(monkeypatch):
    """Согласие на все вопросы и перехват текста последнего сообщения."""
    seen = {}
    monkeypatch.setattr(
        ui_qt.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(
            ui_qt.QMessageBox, kind,
            staticmethod(lambda parent, title, text, *a, _k=kind, **kw:
                         seen.update(kind=_k, title=title, text=text)))
    return seen


def sort_once(config_path):
    config = Config.load(config_path)
    return apply(build_plan(config, deep=True), config, dry_run=False)


def layout(root):
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*"))
            if p.is_file() and ".sorter" not in p.parts}


def test_empty_history_says_so_and_locks_the_button(app, sandbox, answers):
    """Пустая история не должна предлагать откатывать несуществующее."""
    config_path, _ = sandbox

    dlg = ui_qt.HistoryDialog(Config.load(config_path))

    assert dlg.ops == []
    assert dlg.table.rowCount() == 0
    assert not dlg.undo_btn.isEnabled()
    assert "пуста" in dlg.hint.text()
    dlg._undo_selected()  # не падает и ничего не делает
    assert answers.get("title") == "Не выбрано"


def test_history_shows_the_sort_and_its_files(app, sandbox, answers):
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    sort_once(config_path)

    dlg = ui_qt.HistoryDialog(Config.load(config_path))

    assert len(dlg.ops) == 1
    assert dlg.table.rowCount() == 1
    assert dlg.table.item(0, 1).text() == "1"
    # Первая строка выбрана сразу, и подробности показаны про неё.
    assert dlg.details.rowCount() == 1
    assert dlg.details.item(0, 0).text() == "клип.mp4"
    assert dlg.details.item(0, 1).text().endswith("клип.mp4")


def test_undo_returns_files_and_drops_the_record(app, sandbox, answers):
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    before = layout(downloads)
    sort_once(config_path)

    dlg = ui_qt.HistoryDialog(Config.load(config_path))
    dlg.table.selectRow(0)
    dlg._undo_selected()

    assert layout(downloads) == before
    assert dlg.ops == [], "откатанная сортировка осталась в списке"
    assert not dlg.undo_btn.isEnabled()


def test_undoing_the_older_sort_first_keeps_the_newer_one(app, sandbox, answers):
    """Две сортировки подряд, откатывают нижнюю — цепочка рваться не должна.

    Обычный порядок работы: прибрались, поправили правила, нажали
    «Переразложить старое». В списке такие сортировки стоят двумя строками, и
    выбрать нижнюю (ту, с которой всё началось) — дело житейское. Запись о
    второй сортировке при этом обязана уцелеть: иначе вернуть файлы будет
    нечем.
    """
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("один", encoding="utf-8")
    sort_once(config_path)
    (downloads / "лекция.txt").write_text("два", encoding="utf-8")
    sort_once(config_path)

    dlg = ui_qt.HistoryDialog(Config.load(config_path))
    assert len(dlg.ops) == 2
    # Самые свежие — первыми, значит старая сортировка внизу.
    assert dlg.ops[0].when >= dlg.ops[1].when

    dlg.table.selectRow(1)
    dlg._undo_selected()

    assert dlg.ops, "после отката старой сортировки история опустела"
    # Файл второй сортировки на месте — вернуть его по-прежнему есть чем.
    assert any(entry["dst"].endswith("лекция.txt")
               for op in dlg.ops for entry in op.entries)


def test_unreadable_log_is_named_not_hidden(app, sandbox, answers):
    """Журнал с нашим именем, который не разобрался, надо назвать вслух.

    Молчаливый пропуск делает окно неотличимым от «такой сортировки и не
    было»: файлы разложены, а вернуть их отсюда уже нельзя. Сам файл почти
    всегда цел и чинится в редакторе за минуту — если знать, что он есть.
    """
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    sort_once(config_path)
    log = next((downloads / ".sorter").glob("undo_*.json"))
    log.write_text("{оборвалось", encoding="utf-8")

    dlg = ui_qt.HistoryDialog(Config.load(config_path))

    assert dlg.ops == [], "битый журнал попал в список как рабочий"
    assert "Не прочитано журналов" in dlg.hint.text()
    assert log.name in dlg.hint.toolTip()


def test_undo_speaks_up_when_there_is_nothing_to_return(app, sandbox, answers):
    """Файл унесли руками — молчаливый «успешный» откат хуже любой ошибки."""
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    sort_once(config_path)
    (downloads / "Медиа" / "Videos" / "клип.mp4").unlink()

    dlg = ui_qt.HistoryDialog(Config.load(config_path))
    dlg.table.selectRow(0)
    dlg._undo_selected()

    assert answers.get("kind") == "warning"
    assert "возвращать нечего" in answers["text"]


def test_refusing_the_question_changes_nothing(app, sandbox, monkeypatch):
    """«Нет» в подтверждении обязано означать «ничего не трогали»."""
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    sort_once(config_path)
    after_sort = layout(downloads)
    monkeypatch.setattr(
        ui_qt.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    dlg = ui_qt.HistoryDialog(Config.load(config_path))
    dlg.table.selectRow(0)
    dlg._undo_selected()

    assert layout(downloads) == after_sort
    assert len(dlg.ops) == 1


def test_details_survive_a_row_that_is_gone(app, sandbox, answers):
    """Строка выбрана, список обновился — подробности не должны падать."""
    config_path, downloads = sandbox
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    sort_once(config_path)

    dlg = ui_qt.HistoryDialog(Config.load(config_path))
    dlg._show_details(-1)
    assert dlg.details.rowCount() == 0
    dlg._show_details(99)
    assert dlg.details.rowCount() == 0
