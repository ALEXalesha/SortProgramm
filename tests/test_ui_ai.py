"""Кнопка «✨ ИИ» в окне PyQt: что она отдаёт модели и когда молчит.

Окно поднимается в offscreen-режиме, сеть не трогается: `_AiWorker` подменён
заглушкой, которая только запоминает список имён.
"""
import json
import os
import time
from pathlib import Path

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


def test_ai_counts_names_the_model_never_answered_about(window):
    """«Без решения» считалось по ответу, а не по вопросу.

    `_ai_done` видел только то, что вернулось, поэтому имена, про которые модель
    промолчала (или ответила чужим ключом, и сверка его отбросила), не попадали
    никуда: ни в правила, ни в счёт. Окно отчитывалось «ИИ разложил 1 шт.» —
    и человек, спросивший про два файла и заплативший за оба, не узнавал, что
    второй остался неразобранным.
    """
    window.run_ai()
    assert window._ai_asked == 1, "проверка написана под один файл в корне"
    window._ai_asked = 2  # как будто спрашивали про два имени

    window._ai_done({"новый.mp4": "Медиа"})

    assert "без решения: 1" in window.status.text(), (
        f"молчание модели не посчитано: {window.status.text()!r}")


def test_ai_does_not_overwrite_a_rule_made_by_hand(window):
    """Ответ модели затирал правило, поставленное руками.

    `overrides.update(...)` не спрашивает, было ли там что-то: решение, которое
    человек принял сам, молча заменялось мнением модели. README разбирает ровно
    этот случай — деталь `Puck_Launcher.step` модель уносит в «Игры» по слову
    launcher, — и починить его руками можно было только до следующего нажатия
    «✨ИИ». Отменить это нечем: у overrides.json истории нет.
    """
    window.config.overrides["новый.mp4"] = "Игры"

    window._ai_done({"новый.mp4": "Медиа"})

    assert window.config.overrides["новый.mp4"] == "Игры", (
        "ИИ затёр правило, поставленное руками")
    assert "сохранены: 1" in window.status.text(), (
        f"о нетронутом правиле не сказано: {window.status.text()!r}")


def test_ai_still_writes_rules_for_files_without_one(window):
    """Файлы без правила ИИ по-прежнему разбирает."""
    window.config.overrides["новый.mp4"] = "Игры"

    window._ai_done({"новый.mp4": "Медиа", "старый.mp4": "Медиа"})

    assert window.config.overrides["старый.mp4"] == "Медиа"
    assert "ИИ разложил 1 шт." in window.status.text()


def test_ai_asks_each_name_once(window, tmp_path):
    """Одинаковые имена из разных папок уходили в запрос по разу на файл.

    Ключ в overrides.json — имя без пути, поэтому второй `клип.mp4` не добавляет
    вопросу ничего: ответ будет тот же и распространится на оба файла. Платить
    за него дважды незачем, а счёт «спрашиваю по N именам» из-за повторов врал.
    """
    downloads = Path(window.config.downloads_path)
    (downloads / "новый.mp4").write_text("x", encoding="utf-8")
    (downloads / "Медиа" / "Videos" / "новый.mp4").write_text("x", encoding="utf-8")
    window.resort.setChecked(True)

    window.run_ai()

    assert FakeWorker.seen.count("новый.mp4") == 1, (
        f"одно имя ушло в запрос дважды: {FakeWorker.seen}")


def test_ai_does_not_ask_about_names_that_already_have_a_rule(window):
    """За имена с готовым правилом платили, а ответы про них выбрасывали.

    Ответ модели больше не затирает правило, поставленное руками, — и это
    правильно. Но спрашивать про такие имена кнопка не перестала: они уходили
    в запрос вместе со всеми, за них шли деньги и минуты ожидания, а `_ai_done`
    отбрасывал ответ целиком. На разобранной папке второе нажатие «✨ИИ» стало
    оплаченной пустышкой: «ИИ разложил 0 шт.» после запроса на сотню имён.
    """
    window.config.overrides["новый.mp4"] = "Игры"

    window.run_ai()

    assert FakeWorker.seen is None, (
        f"спросили про имя, у которого уже есть правило: {FakeWorker.seen}")
    assert "правил" in window.status.text(), (
        f"почему не спрашивали — не сказано: {window.status.text()!r}")


def test_ai_still_asks_about_the_rest(window):
    """Имена без правила должны уходить в запрос как раньше."""
    window.config.overrides["новый.mp4"] = "Игры"
    window.resort.setChecked(True)

    window.run_ai()

    assert FakeWorker.seen == ["старый.mp4"]


def test_ai_skips_names_covered_by_a_rule_without_the_dedup_number(window):
    """Правило `клип.mp4` покрывает и `клип (1).mp4` — спрашивать не о чем.

    Номер приписывает сама программа при конфликте имён, и `explain_category`
    ищет правило по имени без него. Значит, и вопрос про такое имя уже оплачен.
    """
    downloads = Path(window.config.downloads_path)
    (downloads / "новый.mp4").unlink()
    (downloads / "новый (1).mp4").write_text("x", encoding="utf-8")
    window.config.overrides["новый.mp4"] = "Игры"

    window.run_ai()

    assert FakeWorker.seen is None, (
        f"спросили про имя, накрытое правилом без номера: {FakeWorker.seen}")


# --- модели, которым место выбирает расширение ---


@pytest.fixture
def window_3d(app, tmp_path, monkeypatch):
    """Окно с включённым выносом 3D: рядом с моделями лежит обычный файл."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (tmp_path / "All_3d").mkdir()
    for name in ("деталь.stl", "корпус.gcode", "ЗагадочныйФайл.bin"):
        (downloads / name).write_text("x", encoding="utf-8")

    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {
            "enabled": True,
            "path": str(tmp_path / "All_3d"),
            "extensions": ["stl", "gcode"],
        },
    }), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"3D": [".stl", ".gcode"]},
        "type_map": {"3D": ["stl", "gcode"]},
        "managed_folders": ["3D", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(ui_qt.ai, "load_api_key", lambda base: "sk-test")
    monkeypatch.setattr(ui_qt, "_AiWorker", FakeWorker)
    monkeypatch.setattr(ui_qt.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(ui_qt.QMessageBox, "warning", lambda *a, **k: None)
    FakeWorker.seen = None

    win = ui_qt.GlassWindow(tmp_path / "config.json")
    yield win
    win.deleteLater()


def test_ai_does_not_pay_for_files_routed_by_extension(window_3d):
    """С включённым выносом 3D место модели выбирает расширение, а не категория.

    Ответ модели про такое имя оседает в overrides.json и не делает ничего:
    `plan` до категории просто не доходит. Заметить это было нельзя — окно
    отчитывалось «ИИ разложил 30 шт.», а в плане те же тридцать строк стояли с
    пометкой «по расширению», то есть отчёт спорил с планом, лежащим рядом.
    Правило вдобавок пустое по смыслу: каждое расширение из
    `external_3d.extensions` и так стоит словом в категории «3D».
    """
    window_3d.run_ai()

    assert FakeWorker.seen == ["ЗагадочныйФайл.bin"]


def test_ai_says_why_the_rest_was_not_asked_about(window_3d):
    """Молчать про пропущенные имена нельзя: список выглядел бы потерянным."""
    window_3d.run_ai()

    assert "2 поедут по расширению" in window_3d.status.text()


def test_ai_asks_about_models_again_when_3d_is_off(window_3d):
    """Снятая галочка возвращает моделям категорию — и вопрос про них снова к месту."""
    window_3d.to_3d.setChecked(False)

    window_3d.run_ai()

    assert sorted(FakeWorker.seen) == [
        "ЗагадочныйФайл.bin", "деталь.stl", "корпус.gcode"]


def test_ai_is_silent_when_everything_goes_by_extension(app, tmp_path, monkeypatch):
    """Папка из одних моделей: спрашивать не о чем, и это надо сказать словами."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (tmp_path / "All_3d").mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")

    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {
            "enabled": True,
            "path": str(tmp_path / "All_3d"),
            "extensions": ["stl"],
        },
    }), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"3D": [".stl"]},
        "type_map": {"3D": ["stl"]},
        "managed_folders": ["3D", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(ui_qt.ai, "load_api_key", lambda base: "sk-test")
    monkeypatch.setattr(ui_qt, "_AiWorker", FakeWorker)
    monkeypatch.setattr(ui_qt.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(ui_qt.QMessageBox, "warning", lambda *a, **k: None)
    FakeWorker.seen = None

    win = ui_qt.GlassWindow(tmp_path / "config.json")
    win.run_ai()

    assert FakeWorker.seen is None
    assert "по расширению" in win.status.text()
    win.deleteLater()


def test_unreadable_overrides_are_not_overwritten_by_the_answer(window, tmp_path):
    """Ответ модели затирал файл правил, который не смогли прочитать.

    `overrides.json` пишет только эта кнопка, а живут в нём решения, принятые
    руками, — сотни строк, накопленных за годы, без истории и журнала отмены.
    Разбор настроек нечитаемый файл пропускает («Файл пропущен») и работает с
    пустым словарём; звучит это как «в этот раз без правил», а на деле первое
    же нажатие «✨ИИ» записывало на его место свой ответ — и от прежнего
    содержимого не оставалось ничего.

    Дорога сюда короткая: недописанная скобка при правке руками, оборванная
    запись, кончившееся место. Файл при этом почти всегда цел и чинится в
    редакторе за минуту — если он ещё есть.
    """
    win = window
    overrides = tmp_path / "overrides.json"
    broken = json.dumps({f"файл{i}.pdf": "Медиа" for i in range(50)},
                        ensure_ascii=False)[:-20]
    overrides.write_text(broken, encoding="utf-8")
    win.config = ui_qt.Config.load(tmp_path / "config.json")
    assert win.config.overrides == {}

    win._ai_done({"новый.mp4": "Медиа"})

    assert overrides.read_text(encoding="utf-8") == broken
    assert "не сохранены" in win.status.text()


def test_readable_overrides_are_still_written(window, tmp_path):
    """Обычный файл правил кнопка по-прежнему дополняет."""
    win = window
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"старое.pdf": "Медиа"}, ensure_ascii=False),
                         encoding="utf-8")
    win.config = ui_qt.Config.load(tmp_path / "config.json")

    win._ai_done({"новый.mp4": "Медиа"})

    assert json.loads(overrides.read_text(encoding="utf-8")) == {
        "старое.pdf": "Медиа", "новый.mp4": "Медиа"}
