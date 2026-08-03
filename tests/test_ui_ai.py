"""Кнопка «✨ ИИ» в окне PyQt: что она отдаёт модели и когда молчит.

Окно поднимается в offscreen-режиме, сеть не трогается: `_AiWorker` подменён
заглушкой, которая только запоминает список имён.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication

from sorter import ui_qt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeSignal:
    def connect(self, *_):
        pass


class FakeWorker:
    """Вместо запроса к DeepSeek — запись того, что ушло бы в запрос."""

    seen: list[str] | None = None

    def __init__(self, filenames, categories, api_key, hints=None, fallback="Others"):
        FakeWorker.seen = list(filenames)

    done = FakeSignal()
    failed = FakeSignal()
    progress = FakeSignal()

    def start(self):
        pass


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    """Окно на временной папке: файл в корне и файл в уже разложенном."""
    downloads = tmp_path / "загрузки"
    (downloads / "Медиа" / "Videos").mkdir(parents=True)
    (downloads / "новый.mp4").write_text("x", encoding="utf-8")
    (downloads / "Медиа" / "Videos" / "старый.mp4").write_text("x", encoding="utf-8")

    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(
        json.dumps({
            "categories": {"Медиа": ["клип"]},
            "type_map": {"Videos": ["mp4"]},
            "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
            "fallback_category": "Others",
            "fallback_type": "Misc",
        }, ensure_ascii=False),
        encoding="utf-8")

    monkeypatch.setattr(ui_qt.ai, "load_api_key", lambda base: "sk-test")
    monkeypatch.setattr(ui_qt, "_AiWorker", FakeWorker)
    monkeypatch.setattr(ui_qt.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(ui_qt.QMessageBox, "warning", lambda *a, **k: None)
    FakeWorker.seen = None

    win = ui_qt.GlassWindow(tmp_path / "config.json")
    yield win
    win.deleteLater()


def test_ai_asks_only_about_new_files_by_default(window):
    """Кнопка «✨ ИИ» отправляла модели вообще всё, включая разложенное.

    Обычная уборка («🧹 Очистить») смотрит только в корень загрузок, а ИИ
    молча уходил вглубь. Отсюда два следствия. Первое: на разобранной папке
    запрос раздувался с десятка имён до тысячи — это и деньги, и минуты
    ожидания. Второе хуже: правила для уже разложенных файлов записывались в
    overrides.json, и следующая же уборка перетасовывала папки, которые никто
    не просил трогать. Глубину должна задавать галочка, а не кнопка.
    """
    window.run_ai()

    assert FakeWorker.seen == ["новый.mp4"]


def test_ai_asks_about_old_files_when_resort_is_checked(window):
    """Галочка «Переразложить старое» распространяется и на ИИ.

    Это ровно тот случай, ради которого глубокий разбор и нужен: спросить
    модель про старые загрузки, чтобы новые категории применились к ним.
    """
    window.resort.setChecked(True)

    window.run_ai()

    assert sorted(FakeWorker.seen) == ["новый.mp4", "старый.mp4"]


def test_open_folder_does_nothing_when_field_is_empty(window, monkeypatch):
    """Та же ловушка у кнопки «📂»: пустое поле открывало папку программы.

    Проверка `Path(путь).is_dir()` пропускает пустую строку, потому что
    `Path("")` — это «текущая папка».
    """
    opened = []
    monkeypatch.setattr(ui_qt.os, "startfile", lambda p: opened.append(p), raising=False)
    window.path_edit.setText("")

    window.open_downloads()

    assert opened == []


def test_ai_does_nothing_when_folder_field_is_empty(window):
    """Пустое поле пути — это `Path("")`, а он значит «текущая папка».

    `is_dir()` на нём отвечает True, поэтому проверка пропускала пустое поле
    дальше, и ИИ разбирал папку самой программы: её имена уходили в DeepSeek,
    а ответы записывались правилами в overrides.json. Предпросмотр и история
    пустой путь отбивают отдельной проверкой — кнопка ИИ должна вести себя так же.
    """
    window.path_edit.setText("")

    window.run_ai()

    assert FakeWorker.seen is None
