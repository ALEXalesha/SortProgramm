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


def test_apply_does_not_sort_the_folder_the_window_stopped_naming(window, shown):
    """«Применить» двигало файлы из папки, которой в окне уже не было.

    План строит «🧹 Очистить», а поле пути правится руками — и никого об этом
    не спрашивает: `preview` вызывают «Обзор…» и обе галочки, а набранный
    текст не вызывает ничего. Между двумя нажатиями окно поэтому спокойно
    показывает папку B, таблицу с планом для папки A и кнопку, которая
    применит именно A. Подтверждение спрашивает «Переместить 5 файлов?» и
    папку не называет, так что заметить подмену не по чему.

    Хуже последствий вторая половина. Журнал отмены ложится в ту папку, из
    которой унесли файлы, — в A; следом `_save_settings` записывает в
    config.json уже B, и «🕘 История» смотрит в B. То есть только что
    сделанную сортировку окном не отменить вовсе.

    План теперь пересобирается сам, если поля перестали ему соответствовать.
    """
    win, downloads = window
    other = downloads.parent / "другая папка"
    other.mkdir()
    (other / "отчёт.pdf").write_text("x", encoding="utf-8")
    win.preview()
    assert len(win.moves) == 2

    win.path_edit.setText(str(other))
    win.do_apply()

    assert (downloads / "клип.mp4").is_file(), "файл из папки, которой в окне нет"
    assert (other / "Документы" / "Documents" / "отчёт.pdf").is_file()
    assert (other / ".sorter").is_dir(), "журнал должен лечь туда, где сортировали"


def test_typed_path_refreshes_the_table(window):
    """Поле пути теперь пересобирает план само, как это делает «Обзор…»."""
    win, downloads = window
    other = downloads.parent / "другая папка"
    other.mkdir()
    (other / "отчёт.pdf").write_text("x", encoding="utf-8")
    win.preview()

    win.path_edit.setText(str(other))
    win.path_edit.editingFinished.emit()

    assert [mv.src.name for mv in win.moves] == ["отчёт.pdf"]


def test_window_says_when_a_folder_could_not_be_read(window, monkeypatch):
    """«План готов: 0 шт.» на нечитаемой папке — это неверный ответ уверенным тоном.

    Про ненайденную папку окно говорит давно («Папка не найдена»), а папка,
    которая на месте и не открывается — права, отключённый сетевой диск,
    вынутая флешка, — давала обычный ноль, неотличимый от прибранных загрузок.
    """
    from pathlib import Path

    win, downloads = window
    real = Path.iterdir

    def guard(self):
        if self == downloads:
            raise PermissionError(13, "Отказано в доступе")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", guard)

    win.preview()

    assert win.moves == []
    assert "не прочитано" in win.status.text().lower(), win.status.text()
    assert str(downloads) in win.status.toolTip()


def test_window_stays_quiet_on_a_readable_folder(window):
    """Обычная папка подсказку не рождает — иначе строка состояния зашумится."""
    win, _ = window
    win.preview()

    assert "не прочитано" not in win.status.text().lower()
    assert win.status.toolTip() == ""


def test_the_line_left_after_applying_still_names_the_unread_folder(
        window, shown, monkeypatch):
    """«Перемещено: 1, ошибок: 0» — и ни слова о папке, которую не открыли.

    Строку про непрочитанную папку ставит `preview`, а `do_apply` зовёт его и
    тут же затирает итогом. Вместе со строкой уезжала и жалоба: в тот самый
    момент, когда человек читает отчёт, окно докладывает о безупречном
    прогоне, а файлы непрочитанной папки лежат неразобранными и в списке
    неудач их нет — они в план не попадали вовсе.
    """
    from sorter import scanner

    win, downloads = window
    (downloads / "Медиа").mkdir()

    def unreadable(folder, config, visited, problems=None):
        scanner._unreadable(folder, OSError(5, "Отказано в доступе"), problems)
        return []

    monkeypatch.setattr(scanner, "_walk_managed", unreadable)
    win.resort.setChecked(True)
    win.preview()
    assert "Не прочитано папок: 1" in win.status.text(), win.status.text()

    win.do_apply()

    assert win.status.text().startswith("Перемещено:"), win.status.text()
    # Счёт после уборки другой: разложенное создало ещё одну папку программы,
    # и обход спотыкается уже о две. Важно, что жалоба вообще осталась.
    assert "Не прочитано папок" in win.status.text(), (
        "после «Применить» окно молчит про папку, которую не открыло: "
        f"{win.status.text()!r}")


def test_the_window_greets_a_notice_calmly_and_a_breakage_loudly(app, tmp_path, monkeypatch):
    """Окно PyQt на старте: уведомление — спокойным окном, поломка — тревожным.

    Значок человек читает раньше заголовка, а выбирает его каждое окно у себя.
    Пока оба списка жалоб были одним, «правило в Others пропущено» встречало
    пользователя восклицательным знаком при каждом запуске — навсегда, потому
    что такие строки нарочно остаются в файле.
    """
    from sorter.util import SETTINGS_ALARM

    said = []
    for kind in ("warning", "information"):
        monkeypatch.setattr(
            ui_qt.QMessageBox, kind,
            staticmethod(lambda parent, title, text, *a, _k=kind, **kw:
                         said.append((_k, title, text))))

    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")

    (tmp_path / "overrides.json").write_text(
        '{"0001-0250.mp4": "Others"}', encoding="utf-8")
    ui_qt.GlassWindow(tmp_path / "config.json").deleteLater()
    kind, title, text = said[-1]
    assert kind == "information", f"уведомление показано как поломка: {title!r}"
    assert title != SETTINGS_ALARM and "неверной" not in text, text

    (tmp_path / "overrides.json").write_text(
        '{"0001-0250.mp4": "Others", "клип.mp4": "Медиа "}', encoding="utf-8")
    ui_qt.GlassWindow(tmp_path / "config.json").deleteLater()
    kind, title, text = said[-1]
    assert kind == "warning", f"поломка показана как уведомление: {title!r}"
    assert title == SETTINGS_ALARM
    assert "Медиа " in text and "0001-0250.mp4" in text, text
