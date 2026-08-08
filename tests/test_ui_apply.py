"""Что окно PyQt показывает после «Применить».

Сеть и файловый диалог не трогаются: окно поднимается в offscreen-режиме на
временной папке, а `QMessageBox` подменяется — от него нужен только текст.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication, QMessageBox

from sorter import ui_qt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    (downloads / "отчёт.pdf").write_text("x", encoding="utf-8")

    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(
        json.dumps({
            "categories": {"Медиа": ["клип"], "Документы": ["отчёт"]},
            "type_map": {"Videos": ["mp4"], "Documents": ["pdf"]},
            "managed_folders": ["Медиа", "Документы", "Videos", "Documents",
                                "Others", "Misc"],
            "fallback_category": "Others",
            "fallback_type": "Misc",
        }, ensure_ascii=False),
        encoding="utf-8")

    win = ui_qt.GlassWindow(tmp_path / "config.json")
    win.move_enabled.setChecked(True)
    yield win, downloads
    win.deleteLater()


@pytest.fixture
def shown(monkeypatch):
    """Перехватывает текст последнего показанного окна сообщения."""
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


def test_apply_names_the_file_that_did_not_move(window, shown):
    """Окно говорило «ошибок: 1» и ни слова о том, какой файл и почему.

    Между «🧹 Очистить» и «Применить» проходит сколько угодно времени: файл
    успевают открыть, переименовать, удалить. Такой файл остаётся лежать в
    загрузках, и повторить попытку человек может только зная, о ком речь.
    В консоли эта строка печатается давно (`run_cli`), а в окне терялась.
    """
    win, downloads = window
    win.preview()
    assert len(win.moves) == 2
    (downloads / "клип.mp4").unlink()  # файл увели из-под плана

    win.do_apply()

    assert "клип.mp4" in shown["text"], "какой файл не переехал — не сказано"
    assert shown["kind"] == "warning"


def test_apply_stays_quiet_when_everything_moved(window, shown):
    """Когда всё прошло гладко, окно остаётся коротким и спокойным."""
    win, _ = window
    win.preview()

    win.do_apply()

    assert shown["kind"] == "information"
    assert shown["text"] == "Перемещено: 2, ошибок: 0"


def test_apply_result_stays_in_the_status_line(window, shown):
    """Итог применения затирался планом, который строится следом.

    `do_apply` писал «Перемещено: 7, ошибок: 0», а потом звал `preview()`, и
    тот менял строку на «План готов: 0 шт.» — оба вызова внутри одного слота,
    перерисовки между ними нет. Окно сообщений итог показывало, но после «ОК»
    от него не оставалось ничего: сколько файлов переехало, приходилось
    вспоминать. Последнее слово должно быть за тем, что случилось с файлами.
    """
    win, _ = window
    win.preview()

    win.do_apply()

    assert win.status.text() == "Перемещено: 2, ошибок: 0"


def test_empty_path_field_does_not_erase_the_saved_folder(window):
    """Очищенное поле пути стирало настройку насовсем.

    Поле убирается одним Ctrl+A и Delete — промахнулся мимо «Обзор…», начал
    править и передумал. Окно после этого честно говорит «Папка не найдена», и
    это выглядит ошибкой одного прогона; на деле закрытие писало в config.json
    пустую строку поверх единственной копии пути. Следующий запуск подставлял
    `~/Downloads` и жаловался на ненайденную настройку — возвращать было
    неоткуда.
    """
    win, downloads = window
    win.path_edit.setText("")

    win.close()

    saved = json.loads((downloads.parent / "config.json").read_text(encoding="utf-8"))
    assert saved["downloads_path"] == str(downloads)


def test_a_folder_that_is_gone_is_still_saved(window):
    """Диск отключили, флешку вынули — путь всё равно настроен и нужен."""
    win, downloads = window
    win.path_edit.setText("Z:/нет такой папки")

    win.close()

    saved = json.loads((downloads.parent / "config.json").read_text(encoding="utf-8"))
    assert saved["downloads_path"] == "Z:/нет такой папки"
