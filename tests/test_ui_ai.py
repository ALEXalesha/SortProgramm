"""Кнопка «✨ ИИ» в окне PyQt: что она отдаёт модели и когда молчит.

Окно поднимается в offscreen-режиме, сеть не трогается: `_AiWorker` подменён
заглушкой, которая только запоминает список имён.
"""
import json
import os
import time

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


def test_ai_result_survives_the_plan_it_rebuilds(window):
    """Итог работы ИИ показывался и тут же затирался — увидеть его было нельзя.

    `_ai_done` писал «ИИ разложил N шт.» в строку состояния, а следом звал
    `preview()`, который пишет туда же «План готов: N шт.». Оба вызова идут
    внутри одного слота, между ними окно не перерисовывается, поэтому итог не
    успевал появиться на экране ни на кадр.

    Другого канала у ИИ нет: окна сообщений он не показывает, в отличие от
    «Применить». То есть человек нажимал кнопку, ждал запроса, платил за него —
    и не узнавал ни сколько правил записано, ни сколько имён модель оставила
    без решения. Последнее слово должно оставаться за тем, ради чего кнопку и
    нажимали.
    """
    window._ai_done({"новый.mp4": "Медиа"})

    assert "ИИ" in window.status.text(), (
        f"итог ИИ затёрт планом: {window.status.text()!r}")
    assert "1" in window.status.text()


def test_ai_says_how_many_names_were_left_without_a_decision(window):
    """«Не знаю» от модели правилом не пишется — но сказать об этом надо."""
    window._ai_done({"новый.mp4": "Медиа", "старый.mp4": "Others"})

    assert "без решения: 1" in window.status.text()


def test_ai_rules_that_could_not_be_saved_are_not_passed_over_in_silence(
        window, tmp_path, monkeypatch):
    """Ответ ИИ не записался в overrides.json, и окно об этом молчало.

    Файл держит открытым редактор, папка только на чтение, кончилось место,
    диск сняли — `_save_overrides` глотал OSError целиком. Снаружи всё выглядело
    как удачный разбор: правила есть, план перестроен, плана и правил хватает до
    закрытия окна. А после закрытия ответ, за который заплачено, исчезал, и
    следующий запуск начинал с чистого листа — без единого слова о том, что
    что-то пошло не так.
    """
    said = []
    monkeypatch.setattr(
        ui_qt.QMessageBox, "warning",
        staticmethod(lambda parent, title, text, *a, **k: said.append((title, text))))
    # Папка вместо файла: запись обязана упасть на любой системе.
    (tmp_path / "overrides.json").mkdir()

    window._ai_done({"новый.mp4": "Медиа"})

    assert said, "правила не сохранились, а окно промолчало"
    assert "новый.mp4" in window.config.overrides, "правило должно жить хотя бы в памяти"


@pytest.fixture
def live_worker_window(app, tmp_path, monkeypatch):
    """То же окно, но с настоящим `_AiWorker`: нужен живой фоновый поток."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "новый.mp4").write_text("x", encoding="utf-8")

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
    monkeypatch.setattr(ui_qt.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(ui_qt.QMessageBox, "warning", lambda *a, **k: None)
    # Вместо сети — задержка: поток должен быть ещё жив к моменту закрытия.
    monkeypatch.setattr(ui_qt.ai, "classify_many", lambda *a, **k: time.sleep(0.5) or {})

    win = ui_qt.GlassWindow(tmp_path / "config.json")
    yield win
    win.deleteLater()


def test_closing_window_waits_for_the_ai_request(live_worker_window):
    """Закрытие окна во время запроса к ИИ убивало процесс целиком.

    `_AiWorker` — это `QThread`, и Qt обрывает процесс (`abort`), если объект
    потока уничтожается на ходу. Окно держит поток полем, поэтому цепочка
    получалась короткая: нажал «✨ИИ» на большой папке, передумал, закрыл окно —
    и вместо тихого выхода Windows показывал падение. Настройки к тому моменту
    уже сохранены, но ответ модели, за который заплачено, пропадал молча.
    """
    live_worker_window.run_ai()
    assert live_worker_window._worker.isRunning()

    live_worker_window.close()

    assert not live_worker_window._worker.isRunning(), (
        "окно закрылось, не дождавшись потока — Qt уронит процесс")
