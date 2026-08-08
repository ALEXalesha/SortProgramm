"""Проверки на найденные баги. Каждый тест — воспроизведение конкретной поломки."""
import json
import os
from pathlib import Path

import pytest

from sorter.ai import load_api_key, parse_ai_response, useful_rules
from sorter.config import Config, usable_3d_path
from sorter.classifier import explain_category, match_category, match_type
from sorter.history import list_operations
from sorter.mover import apply, undo, Result
from sorter.planner import build_plan, external_3d_warning, Move
from sorter.scanner import scan
from sorter.util import report


def make_config(root):
    return Config(
        downloads_path=str(root),
        categories={"Медиа": ["клип"], "Программы": [".exe"], "Код": [".json"]},
        type_map={"Videos": ["mp4"], "Installers": ["exe"], "Documents": ["pdf", "txt"]},
        managed_folders=["Others", "Медиа", "Программы", "Код",
                         "Videos", "Installers", "Documents", "Misc"],
        ignore=["desktop.ini"],
        fallback_category="Others",
        fallback_type="Misc",
    )


def touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- откат при занятом исходном пути ---


def test_undo_does_not_nest_folder_into_itself(tmp_path):
    """`shutil.move` кладёт папку ВНУТРЬ одноимённой: claude-usage/claude-usage.

    Ровно так и потерялся репозиторий: снаружи откат выглядел успешным.
    """
    moved = touch(tmp_path / "куда" / "репо" / "файл.txt").parent
    back = tmp_path / "репо"
    touch(back / "другое.txt")  # исходный путь снова занят
    log = tmp_path / "undo.json"
    log.write_text(json.dumps([{"src": str(back), "dst": str(moved)}]), encoding="utf-8")

    notes = undo(log)

    assert not (back / "репо").exists(), "папка вложилась сама в себя"
    assert (tmp_path / "репо (1)" / "файл.txt").exists()
    assert notes and "занят" in notes[0][1]


def test_undo_reports_nothing_when_path_is_free(tmp_path):
    moved = touch(tmp_path / "куда" / "файл.txt")
    log = tmp_path / "undo.json"
    log.write_text(
        json.dumps([{"src": str(tmp_path / "файл.txt"), "dst": str(moved)}]),
        encoding="utf-8",
    )
    assert undo(log) == []
    assert (tmp_path / "файл.txt").exists()


def test_undo_does_not_clobber_existing_file(tmp_path):
    moved = touch(tmp_path / "куда" / "отчёт.pdf", "перемещённый")
    back = touch(tmp_path / "отчёт.pdf", "другой файл с тем же именем")
    log = tmp_path / "undo.json"
    log.write_text(json.dumps([{"src": str(back), "dst": str(moved)}]), encoding="utf-8")

    undo(log)

    assert back.read_text(encoding="utf-8") == "другой файл с тем же именем"
    assert (tmp_path / "отчёт (1).pdf").read_text(encoding="utf-8") == "перемещённый"


def test_undo_numbers_returned_file_before_its_extension(tmp_path):
    """Номер надо ставить по природе того, что возвращаем, а не того, что мешает.

    На исходном пути оказалась папка `клип.mp4` — и видео вернулось под именем
    `клип.mp4 (1)`: расширение больше не последнее, файл перестал открываться
    двойным щелчком. При перемещении этот случай уже разобран (`as_dir`), а
    откат по-прежнему смотрел на занявшего место.
    """
    moved = touch(tmp_path / "куда" / "клип.mp4", "видео")
    (tmp_path / "клип.mp4").mkdir()  # исходный путь занят папкой
    log = tmp_path / "undo.json"
    log.write_text(
        json.dumps([{"src": str(tmp_path / "клип.mp4"), "dst": str(moved)}]),
        encoding="utf-8")

    undo(log)

    assert (tmp_path / "клип (1).mp4").read_text(encoding="utf-8") == "видео"
    assert (tmp_path / "клип.mp4").is_dir()


# --- ручные подпапки внутри папок программы ---


def test_resort_leaves_handmade_subfolder_alone(tmp_path):
    """`Others/Documents/9 класс/` создал пользователь — это чужая раскладка."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "Others" / "Documents" / "9 класс" / "конспект.pdf")
    assert build_plan(cfg, deep=True) == []


def test_resort_still_refiles_files_in_own_folders(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "Others" / "Videos" / "клип.mp4")
    moves = build_plan(cfg, deep=True)
    assert [m.dst for m in moves] == [tmp_path / "Медиа" / "Videos" / "клип.mp4"]


def test_scan_skips_handmade_subfolder(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "Documents" / "2026 год" / "внутри.pdf")
    touch(tmp_path / "Documents" / "снаружи.pdf")
    assert scan(tmp_path, cfg, deep=True) == [tmp_path / "Documents" / "снаружи.pdf"]


# --- слова-расширения и содержимое файла ---


def test_extension_keyword_ignores_file_content():
    """Заметка со строкой «installer.exe» — не программа."""
    categories = {"Программы": [".exe"], "Код": [".json"]}
    body = "надо скачать installer.exe и поправить config.json"
    assert match_category("заметка.txt", body, categories) is None


def test_plain_keyword_still_matches_content():
    """Обычные слова по содержимому искать по-прежнему нужно."""
    categories = {"Учёба": ["экзамен"]}
    assert match_category("заметка.txt", "готовлюсь к экзамену", categories) == "Учёба"


def test_extension_keyword_matches_filename(tmp_path):
    cfg = make_config(tmp_path)
    assert explain_category("setup.exe", "", cfg)[0] == "Программы"


# --- чистка пустых папок ---


def test_cleanup_keeps_untouched_user_folder(tmp_path):
    """Пустую папку, которой прогон не касался, удалять нельзя."""
    cfg = make_config(tmp_path)
    mine = tmp_path / "Documents"
    mine.mkdir()
    touch(tmp_path / "клип.mp4")
    apply(build_plan(cfg, deep=False), cfg, dry_run=False)
    assert mine.is_dir()


def test_cleanup_removes_folder_emptied_by_this_run(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "Others" / "Videos" / "клип.mp4")
    apply(build_plan(cfg, deep=True), cfg, dry_run=False)
    assert not (tmp_path / "Others").exists()


def test_cleanup_keeps_folder_that_still_has_files(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "Others" / "Videos" / "клип.mp4")
    touch(tmp_path / "Others" / "Videos" / "заметка.pdf")
    apply(build_plan(cfg, deep=True), cfg, dry_run=False)
    assert (tmp_path / "Others" / "Documents" / "заметка.pdf").exists()


# --- журнал отмены ---


def test_two_sorts_in_one_second_keep_both_logs(tmp_path):
    """Имя журнала — метка времени с точностью до секунды.

    Две сортировки подряд укладываются в одну секунду легко: нажал «Применить»,
    поправил галочку, нажал снова. Одинаковое имя означало, что второй журнал
    затирает первый, и ту сортировку уже никогда не откатить.
    """
    cfg = make_config(tmp_path)
    touch(tmp_path / "клип.mp4")
    first = apply(build_plan(cfg), cfg, dry_run=False)
    touch(tmp_path / "второй клип.mp4")
    second = apply(build_plan(cfg), cfg, dry_run=False)

    assert first.undo_log != second.undo_log
    assert first.undo_log.exists(), "первый журнал затёрт вторым"
    assert len(list_operations(tmp_path)) == 2


def test_history_keeps_order_of_sorts_within_one_second(tmp_path):
    """Свежая сортировка стоит первой, даже если секунда та же."""
    cfg = make_config(tmp_path)
    names = ["клип1.mp4", "клип2.mp4", "клип3.mp4"]
    for name in names:
        touch(tmp_path / name)
        apply(build_plan(cfg), cfg, dry_run=False)

    moved = [Path(op.entries[0]["src"]).name for op in list_operations(tmp_path)]
    assert moved == list(reversed(names))


def test_nothing_moved_leaves_no_record_in_history(tmp_path):
    """Пустой журнал — запись «0 файлов», которая ничего не откатывает."""
    cfg = make_config(tmp_path)
    ghost = Move(tmp_path / "нет.mp4", tmp_path / "Медиа" / "Videos" / "нет.mp4")

    result = apply([ghost], cfg, dry_run=False)

    assert result.undo_log is None
    assert list_operations(tmp_path) == []


def test_apply_survives_unwritable_downloads_folder(tmp_path):
    """`main.py --path X:/нет --apply` падал стеком на создании `.sorter`.

    Съёмный диск вынули, папку переименовали, путь указали с опечаткой —
    журнал записать некуда. Ронять программу на этом нельзя: файлы уже
    переехали, и про это надо доложить, а не показать трассировку.
    """
    занято = touch(tmp_path / "не папка")  # под файлом каталог не создать
    cfg = make_config(занято / "загрузки")
    src = touch(tmp_path / "клип.mp4")

    result = apply([Move(src, tmp_path / "куда" / "клип.mp4")], cfg, dry_run=False)

    assert result.moved == 1
    assert result.undo_log is None
    assert result.undo_failed, "потерю журнала отмены надо показать, а не проглотить"


# --- испорченные настройки не ломают запуск ---


def test_broken_config_does_not_block_startup(tmp_path):
    """config.json программа пишет сама при каждом закрытии окна.

    Оборванная запись превращала программу в кирпич: стек вместо окна, и
    поправить путь через интерфейс уже нельзя.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"downloads_path": "X:/z", "extern', encoding="utf-8")

    cfg = Config.load(cfg_path)

    assert cfg.downloads_path, "должен быть путь по умолчанию"
    assert any("config.json" in p for p in cfg.problems)


def test_config_without_downloads_path_uses_default(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"external_3d": {}}), encoding="utf-8")

    cfg = Config.load(cfg_path)

    assert cfg.downloads_path == str(Path.home() / "Downloads")
    assert cfg.problems


def test_external_3d_not_an_object_does_not_crash_planning(tmp_path):
    """Правка руками: `"external_3d": "C:/All_3d"` вместо объекта."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(downloads), "external_3d": "C:/All_3d"}),
        encoding="utf-8")

    cfg = Config.load(cfg_path)

    assert build_plan(cfg, send_3d_external=True) == []
    assert cfg.problems


def test_external_3d_without_extensions_still_sends_models(tmp_path):
    """Окно сохраняет только `enabled` и `path` — список расширений терялся.

    Галочка «3D-модели → отдельная папка» после этого молча ничего не делала.
    """
    downloads = tmp_path / "загрузки"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d")},
    }), encoding="utf-8")
    touch(downloads / "деталь.stl")

    cfg = Config.load(cfg_path)
    moves = build_plan(cfg, send_3d_external=True)

    assert [m.dst for m in moves] == [tmp_path / "All_3d" / "stl" / "деталь.stl"]


def test_saved_config_keeps_3d_extensions(tmp_path):
    """Сохранение из окна не должно ронять список расширений обратно в пустоту."""
    cfg_path = tmp_path / "config.json"
    cfg = Config(downloads_path="X:/z", external_3d={"enabled": True, "path": "Y:/m"})
    cfg.save(cfg_path)

    assert Config.load(cfg_path).external_3d["extensions"]


# --- служебный номер ` (1)` в имени ---


def test_dedup_suffix_does_not_change_category(tmp_path):
    """`abc-fon.png` ловится словом `-fon.`, а `abc-fon (1).png` уже нет.

    Номер приписывает сама программа при конфликте имён. После этого
    переразложение считало файл другим и уносило его в Others.
    """
    cfg = make_config(tmp_path)
    cfg.categories = {"3D": ["-fon."]}
    assert explain_category("abc-fon (1).png", "", cfg)[0] == "3D"


def test_resort_does_not_move_file_it_renamed_itself(tmp_path):
    """Переразложение должно быть устойчивым: второй прогон ничего не двигает."""
    cfg = make_config(tmp_path)
    cfg.categories = {"3D": ["-fon."]}
    cfg.type_map = {"Images": ["png"]}
    cfg.managed_folders = ["3D", "Images", "Others", "Misc"]
    touch(tmp_path / "abc-fon.png")
    touch(tmp_path / "3D" / "Images" / "abc-fon.png")

    apply(build_plan(cfg, deep=True), cfg, dry_run=False)

    assert build_plan(cfg, deep=True) == []


def test_override_still_matches_exact_name_with_number(tmp_path):
    """Правило под конкретное имя с номером важнее правила под имя без него."""
    cfg = make_config(tmp_path)
    cfg.overrides = {"отчёт (1).pdf": "Учёба", "отчёт.pdf": "Медиа"}
    assert explain_category("отчёт (1).pdf", "", cfg)[0] == "Учёба"


# --- занятая цель при перемещении ---


def test_apply_does_not_clobber_file_at_destination(tmp_path):
    """`shutil.move` молча затирает файл, если в цели уже лежит такое имя.

    План строится один раз, а применяется позже: между «Очистить» и
    «Применить» проходит сколько угодно времени, и за это время в целевой
    папке мог появиться файл с тем же именем — руками, второй копией
    программы, докачкой браузера. Свободное имя planner подбирал по состоянию
    на момент плана, и к моменту перемещения оно уже не свободно.

    Снаружи это выглядело как обычная успешная сортировка: «перемещено 1,
    ошибок 0». Файла при этом больше нет.
    """
    cfg = make_config(tmp_path)
    touch(tmp_path / "клип.mp4", "новый")
    moves = build_plan(cfg)
    touch(tmp_path / "Медиа" / "Videos" / "клип.mp4", "старый и важный")

    result = apply(moves, cfg, dry_run=False)

    assert (tmp_path / "Медиа" / "Videos" / "клип.mp4").read_text(
        encoding="utf-8") == "старый и важный", "файл в цели затёрт"
    assert (tmp_path / "Медиа" / "Videos" / "клип (1).mp4").read_text(
        encoding="utf-8") == "новый"
    assert result.notes, "переименование надо показать, а не проглотить"


def test_apply_does_not_move_file_inside_folder_with_same_name(tmp_path):
    """Если в цели папка с таким именем, `shutil.move` кладёт файл ВНУТРЬ неё."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "клип.mp4")
    moves = build_plan(cfg)
    (tmp_path / "Медиа" / "Videos" / "клип.mp4").mkdir(parents=True)

    apply(moves, cfg, dry_run=False)

    assert not (tmp_path / "Медиа" / "Videos" / "клип.mp4" / "клип.mp4").exists()
    assert (tmp_path / "Медиа" / "Videos" / "клип (1).mp4").is_file()


def test_undo_returns_file_to_place_freed_by_dedup(tmp_path):
    """Журнал пишет то имя, под которым файл реально лёг, иначе откат промахнётся."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "клип.mp4", "новый")
    moves = build_plan(cfg)
    touch(tmp_path / "Медиа" / "Videos" / "клип.mp4", "старый")

    result = apply(moves, cfg, dry_run=False)
    undo(result.undo_log)

    assert (tmp_path / "клип.mp4").read_text(encoding="utf-8") == "новый"
    assert not (tmp_path / "Медиа" / "Videos" / "клип (1).mp4").exists()


# --- нечитаемая папка ---


def test_scan_survives_unreadable_folder(tmp_path, monkeypatch):
    """Права на папку могут не дать её прочитать — окно не должно падать.

    Внутри управляемых папок этот случай уже обработан (`_walk_managed`),
    а в корне тот же самый вызов шёл без защиты.
    """
    cfg = make_config(tmp_path)

    def denied(self):
        raise PermissionError("нет доступа")

    monkeypatch.setattr(Path, "iterdir", denied)
    assert scan(tmp_path, cfg, deep=False) == []


# --- расширения, записанные заглавными ---


def test_type_map_matches_extension_written_in_capitals():
    """Список расширений в rules.json правят руками, и заглавные там неизбежны.

    Ключевые слова категорий приводятся к нижнему регистру, расширения внешней
    папки 3D — тоже, а карта типов сравнивала как есть. `"Documents": ["PDF"]`
    молча переставала совпадать, и все документы уезжали в `Misc`: раскладка
    неверная, а жалоб никаких — самый неприятный вид поломки.
    """
    assert match_type("pdf", {"Documents": ["PDF"]}) == "Documents"
    assert match_type("PDF", {"Documents": ["pdf"]}) == "Documents"


# --- испорченный журнал отмены ---


def test_undo_survives_broken_journal(tmp_path):
    """Оборванная запись журнала роняла откат стеком вместо сообщения.

    Записи внутри журнала уже разбираются осторожно (`entries_of`), а сам файл
    читался напрямую: `json.loads` на обрезанном файле бросает ValueError, а
    окно истории ловит только OSError — и программа падала целиком.
    """
    log = tmp_path / "undo_20260101_010101.json"
    log.write_text('[{"src": "a", "dst"', encoding="utf-8")

    notes = undo(log)

    assert notes, "о нечитаемом журнале надо сказать, а не падать"
    assert "журнал" in notes[0][1].lower()


# --- пустые папки после отката ---


def test_undo_removes_folders_it_emptied(tmp_path):
    """После отката в загрузках оставалась гора пустых папок программы.

    Сортировка за собой убирает (`_cleanup_emptied`), а откат — нет, хотя
    опустошает ровно те же папки. Снаружи «отменить» выглядело как половина
    отмены: файлы на месте, а созданный программой каркас никуда не делся.
    """
    cfg = make_config(tmp_path)
    touch(tmp_path / "клип.mp4")
    result = apply(build_plan(cfg), cfg, dry_run=False)
    assert (tmp_path / "Медиа" / "Videos").is_dir()

    undo(result.undo_log, cfg)

    assert (tmp_path / "клип.mp4").is_file()
    assert not (tmp_path / "Медиа").exists(), "пустая папка программы осталась"


def test_undo_keeps_folder_that_still_has_files(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "клип.mp4")
    result = apply(build_plan(cfg), cfg, dry_run=False)
    touch(tmp_path / "Медиа" / "Videos" / "чужое.mp4")

    undo(result.undo_log, cfg)

    assert (tmp_path / "Медиа" / "Videos" / "чужое.mp4").is_file()


def test_undo_keeps_foreign_folder(tmp_path):
    """Папку не из `managed_folders` откат не сносит, даже если она опустела."""
    cfg = make_config(tmp_path)
    src = touch(tmp_path / "своя папка" / "клип.mp4")
    log = tmp_path / "undo.json"
    log.write_text(
        json.dumps([{"src": str(tmp_path / "клип.mp4"), "dst": str(src)}]),
        encoding="utf-8")

    undo(log, cfg)

    assert (tmp_path / "своя папка").is_dir()


# --- категория, уводящая файлы из загрузок ---


def test_override_with_absolute_path_is_rejected(tmp_path):
    """Категория уходит в `root / категория / тип`, а абсолютный кусок в `Path`
    отбрасывает всё слева: `C:/Windows/Temp` вместо категории уносит файл из
    загрузок совсем в другое место, и об этом никто не узнаёт.
    """
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"секрет.pdf": "C:/Windows/Temp"}, ensure_ascii=False),
        encoding="utf-8")

    cfg = Config.load(tmp_path / "config.json")

    assert "секрет.pdf" not in cfg.overrides
    assert cfg.problems


def test_override_with_parent_reference_is_rejected(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"секрет.pdf": "../../чужое"}, ensure_ascii=False),
        encoding="utf-8")

    cfg = Config.load(tmp_path / "config.json")

    assert "секрет.pdf" not in cfg.overrides


def test_nested_category_still_allowed(tmp_path):
    """`Учёба/2026` — обычная вложенная раскладка, ломать её незачем."""
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"конспект.pdf": "Учёба/2026"}, ensure_ascii=False),
        encoding="utf-8")

    cfg = Config.load(tmp_path / "config.json")

    assert cfg.overrides["конспект.pdf"] == "Учёба/2026"


def test_category_named_by_absolute_path_is_dropped(tmp_path):
    """То же самое, но категория объявлена путём в самих правилах."""
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(
        json.dumps({"categories": {"C:/Windows/Temp": ["секрет"], "Медиа": ["клип"]}},
                   ensure_ascii=False),
        encoding="utf-8")

    cfg = Config.load(tmp_path / "config.json")

    assert list(cfg.categories) == ["Медиа"]
    assert cfg.problems


# --- запасная категория, переименованная в правилах ---


def test_ai_unknown_category_falls_back_to_the_configured_name():
    """Разбор ответа ИИ подставлял «Others» буквально, мимо настройки.

    `fallback_category` в rules.json переименовывают — README прямо называет
    его настраиваемым. Но выдумку модели («Музыка») разбор заменял строкой
    «Others», а фильтр «не сохранять незнание» отсекает ответы по имени
    запасной категории из конфига. Имена не совпадали, поэтому в overrides.json
    уезжало правило `файл → Others`: категории с таким именем в правилах нет,
    её нет и в `managed_folders`, значит папка `Загрузки/Others` больше никогда
    не разбирается и не убирается. И это ещё правило с наивысшим приоритетом —
    файлу закрыта дорога в любую новую категорию навсегда.
    """
    mapping = parse_ai_response(
        '{"x.bin": "Музыка"}', ["Медиа", "Разное"], fallback="Разное")

    assert mapping == {"x.bin": "Разное"}
    assert useful_rules(mapping, "Разное") == {}


# --- ключ ИИ, сохранённый в чужой кодировке ---


def test_api_key_saved_as_utf16_is_read(tmp_path):
    """Блокнот умеет сохранять `deepseek_key.txt` в UTF-16 — и чтение падало.

    `read_text(encoding="utf-8")` бросает на таком файле UnicodeDecodeError.
    Это ValueError, а не OSError, поэтому мимо него проходили все проверки в
    программе: ключ читается без единого `try`. В окне исключение прилетало
    внутрь слота PyQt, а там необработанное исключение гасит процесс целиком —
    нажатие «✨ИИ» закрывало программу молча, без сообщения и без journal'а.
    """
    (tmp_path / "deepseek_key.txt").write_text("sk-abc123", encoding="utf-16")

    assert load_api_key(tmp_path) == "sk-abc123"


def test_api_key_with_utf8_bom_is_read(tmp_path):
    """Тот же Блокнот, режим «UTF-8 с BOM»: метка приклеивалась к ключу.

    Программа не падала, но ключ уезжал в заголовок Authorization вместе с
    невидимым символом — DeepSeek отвечал «неверный ключ», и понять почему
    было нельзя: в файле на вид ровно то, что выдал сайт.
    """
    (tmp_path / "deepseek_key.txt").write_text("sk-abc123", encoding="utf-8-sig")

    assert load_api_key(tmp_path) == "sk-abc123"


def test_unreadable_api_key_file_gives_no_key_instead_of_crash(tmp_path):
    """Файл, который не разобрать ничем: ответ — «ключа нет», а не падение.

    Окно на пустой ответ показывает понятное «Положи ключ в deepseek_key.txt».
    Это лучше любого исключения: подсказка на месте, программа жива.
    """
    (tmp_path / "deepseek_key.txt").write_bytes(b"\xff\xfe\x41")

    assert load_api_key(tmp_path) is None


def test_env_key_still_wins_over_file(tmp_path, monkeypatch):
    """Переменная окружения остаётся главнее файла."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    (tmp_path / "deepseek_key.txt").write_text("sk-from-file", encoding="utf-8")

    assert load_api_key(tmp_path) == "sk-from-env"


# --- отчёт о применении: какие файлы не переехали ---


def test_report_names_files_that_did_not_move():
    """Окно сообщало только число ошибок, а не то, какие файлы и почему.

    «Перемещено: 7, ошибок: 3» — и всё. Какие три, что с ними, повторять ли
    попытку, узнать было неоткуда: в консоль такой запуск ничего не пишет, а
    список ошибок `Result.errors` до человека не доезжал. CLI печатает каждую
    строку давно — окно должно говорить то же самое.
    """
    result = Result(
        planned=2, moved=1,
        errors=[(r"C:\Загрузки\клип.mp4", "нет файла: C:\\Загрузки\\клип.mp4")])

    text = report(result)

    assert "клип.mp4" in text, "надо назвать файл, а не только посчитать"
    assert "нет файла" in text, "надо сказать причину"


def test_report_keeps_both_errors_and_notes():
    """Оговорки показывались, ошибки — нет. Нужны обе половины сразу."""
    result = Result(
        planned=2, moved=1,
        errors=[("клип.mp4", "занят другой программой")],
        notes=[("отчёт.pdf", "в цели уже есть «отчёт.pdf»")])

    text = report(result)

    assert "занят другой программой" in text
    assert "в цели уже есть" in text


def test_report_stays_short_when_everything_moved():
    """Когда всё прошло гладко, лишних разделов в отчёте быть не должно."""
    assert report(Result(planned=1, moved=1)) == "Перемещено: 1, ошибок: 0"


# --- категория, которую забыли в managed_folders ---


def _write_rules(tmp_path, rules):
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(
        json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "config.json"


def test_category_missing_from_managed_folders_is_reported(tmp_path):
    """Категория без записи в `managed_folders` — чёрная дыра, и молча.

    Файлы в такую папку уезжают нормально, а обратно программа в неё уже не
    заходит: `scan` обходит только своё, и «Переразложить старое» эту папку не
    видит. Опустевшей её тоже никто не уберёт. То есть новая категория
    применяется ровно один раз, а дальше файлы в ней заморожены навсегда —
    именно этот исход README называет худшим, разбирая переименованную
    `fallback_category`. Проверка на путь вместо имени уже есть, а на забытую
    строку в `managed_folders` — не было ни одной жалобы.
    """
    cfg_path = _write_rules(tmp_path, {
        "categories": {"Медиа": ["клип"], "Рецепты": ["борщ"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    })

    cfg = Config.load(cfg_path)

    assert any("Рецепты" in p for p in cfg.problems)
    assert cfg.categories["Рецепты"] == ["борщ"], "правило работает, просто с жалобой"


def test_type_missing_from_managed_folders_is_reported(tmp_path):
    """Тип — такая же папка, как категория: `Категория/Тип/файл`."""
    cfg_path = _write_rules(tmp_path, {
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"], "Книги": ["fb2"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    })

    cfg = Config.load(cfg_path)

    assert any("Книги" in p for p in cfg.problems)


def test_fallback_category_missing_from_managed_folders_is_reported(tmp_path):
    """Запасная папка собирает весь неопознанный хвост — её забыть больнее всего."""
    cfg_path = _write_rules(tmp_path, {
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Misc"],
        "fallback_category": "Прочее",
        "fallback_type": "Misc",
    })

    cfg = Config.load(cfg_path)

    assert any("Прочее" in p for p in cfg.problems)


def test_override_category_missing_from_managed_folders_is_reported(tmp_path):
    """Правило из overrides.json создаёт папку так же, как правило из rules.json."""
    _write_rules(tmp_path, {
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    })
    (tmp_path / "overrides.json").write_text(
        json.dumps({"смета.mp4": "Работа"}, ensure_ascii=False), encoding="utf-8")

    cfg = Config.load(tmp_path / "config.json")

    assert any("Работа" in p for p in cfg.problems)


def test_full_managed_folders_stay_silent(tmp_path):
    """Полный список жалоб не вызывает — иначе предупреждение обесценится."""
    cfg_path = _write_rules(tmp_path, {
        "categories": {"Медиа": ["клип"]},
        "patterns": {"Медиа": [r"^\d{4}\.mp4$"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    })

    assert Config.load(cfg_path).problems == []


def test_empty_managed_folders_is_not_nagged_about(tmp_path):
    """Списка нет вовсе — это не «забыли строку», а другой способ настройки.

    Жаловаться на каждую категорию в таком конфиге значит завалить окно
    предупреждениями там, где человек ничего не забывал.
    """
    cfg_path = _write_rules(tmp_path, {
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
    })

    assert Config.load(cfg_path).problems == []


# --- пустые папки расширений во внешней папке 3D ---


def test_undo_removes_extension_folder_it_emptied_in_3d(tmp_path):
    """Откат убирал каркас в загрузках, но не во внешней папке 3D.

    Подпапки с именем расширения (`All_3d/gcode`) создаёт сама программа, и
    после отката они оставались пустыми навсегда: в `managed_folders` таких
    имён нет, а чистка смотрит только туда. Получалась ровно та половина
    отмены, из-за которой уборку за откатом и добавили.
    """
    downloads = tmp_path / "Загрузки"
    all_3d = tmp_path / "All_3d"
    all_3d.mkdir()
    cfg = make_config(downloads)
    cfg.external_3d = {"enabled": True, "path": str(all_3d), "extensions": ["gcode"]}
    touch(downloads / "деталь.gcode")
    result = apply(build_plan(cfg, send_3d_external=True), cfg, dry_run=False)
    assert (all_3d / "gcode" / "деталь.gcode").is_file()

    undo(result.undo_log, cfg)

    assert (downloads / "деталь.gcode").is_file()
    assert not (all_3d / "gcode").exists(), "пустая папка расширения осталась"
    assert all_3d.is_dir(), "саму внешнюю папку 3D трогать нельзя"


def test_cleanup_keeps_3d_folder_that_still_has_files(tmp_path):
    """Непустую подпапку расширения чистка не трогает."""
    downloads = tmp_path / "Загрузки"
    all_3d = tmp_path / "All_3d"
    all_3d.mkdir()
    cfg = make_config(downloads)
    cfg.external_3d = {"enabled": True, "path": str(all_3d), "extensions": ["gcode"]}
    touch(downloads / "деталь.gcode")
    result = apply(build_plan(cfg, send_3d_external=True), cfg, dry_run=False)
    touch(all_3d / "gcode" / "чужое.gcode")

    undo(result.undo_log, cfg)

    assert (all_3d / "gcode" / "чужое.gcode").is_file()


# --- ответ ИИ не про те имена ---


def test_ai_answer_about_a_name_we_did_not_ask_is_dropped():
    """Модель дописывает в ответ имена, которых ей не присылали.

    Правило из `overrides.json` живёт вечно и стоит выше всего остального,
    поэтому выдуманное имя — не безобидный мусор: стоит появиться в загрузках
    файлу с таким именем, и он поедет по решению, принятому вслепую, про
    другую папку и год назад. Спрашивали про одно — записываем только про то
    же самое.
    """
    got = parse_ai_response(
        '{"клип.mp4": "Медиа", "выдуманный.exe": "Программы"}',
        ["Медиа", "Программы"], "Others", requested=["клип.mp4"])

    assert got == {"клип.mp4": "Медиа"}


def test_ai_answer_returns_to_the_spelling_we_asked_about():
    """Модель отвечает про тот же файл, но пишет имя по-своему.

    Промт просит повторять ключ символ в символ, и модель это правило
    нарушает регулярно: приводит имя к нижнему регистру, теряет служебный
    номер. Правило ищется точным совпадением, поэтому такой ответ не совпадёт
    ни с одним файлом — а окно всё равно отчитается «ИИ разложил N шт.».
    Счёт врёт, файл остаётся неразобранным, и понять это неоткуда.
    """
    got = parse_ai_response(
        '{"отчёт.pdf": "Документы"}', ["Документы"], "Others",
        requested=["Отчёт.PDF"])

    assert got == {"Отчёт.PDF": "Документы"}


def test_ai_answer_without_a_list_of_names_is_taken_as_is():
    """Без списка присланных имён фильтровать не по чему — разбор как раньше."""
    assert parse_ai_response('{"a.pdf": "Учёба"}', ["Учёба"]) == {"a.pdf": "Учёба"}


# --- откату нечего возвращать ---


def test_undo_says_when_there_is_nothing_to_return(tmp_path):
    """Откат, которому нечего вернуть, молчал и стирал запись из истории.

    Файл после сортировки унесли руками, переименовали, удалили — по новому
    пути его больше нет. Окно спрашивало «Вернуть 7 файлов?», человек
    соглашался, дальше не происходило ничего: ни файлов на местах, ни единого
    слова. Запись при этом из истории пропадала, то есть и разбираться потом
    было уже не с чем. Ровно тот молчаливый «успешный» откат, из-за которого
    оговорки и показывают.
    """
    log = tmp_path / "undo.json"
    log.write_text(json.dumps([
        {"src": str(tmp_path / "клип.mp4"),
         "dst": str(tmp_path / "Медиа" / "Videos" / "клип.mp4")},
    ]), encoding="utf-8")

    notes = undo(log)

    assert notes, "откат ничего не вернул и об этом не сказал"
    assert "нет" in notes[0][1]


def test_undo_stays_quiet_when_everything_came_back(tmp_path):
    """Оговорка — это исключение. Успешный откат по-прежнему молчит."""
    moved = touch(tmp_path / "Медиа" / "Videos" / "клип.mp4")
    log = tmp_path / "undo.json"
    log.write_text(json.dumps([
        {"src": str(tmp_path / "клип.mp4"), "dst": str(moved)},
    ]), encoding="utf-8")

    assert undo(log) == []


# --- сломанная ссылка внутри папки программы ---


def broken_link(path):
    """Ссылка в никуда. Пропускает тест, если система их не даёт создавать."""
    try:
        path.symlink_to(path.parent / "нет-такой-папки", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"символические ссылки недоступны: {exc}")
    return path


def test_scan_skips_broken_link_inside_managed_folder(tmp_path):
    """Битая ссылка внутри своей папки уезжала в план как обычный файл.

    Корень загрузок отбирает файлы по `is_file()`, а обход управляемых папок
    проверял только «это не папка» — и всё, что не ответило `is_dir()`,
    записывал в файлы. Оборванный ярлык на снятую флешку, ссылка на удалённую
    папку, стык на путь длиннее предела Windows: `is_dir()` у всех False,
    `is_file()` тоже False.

    Дальше такая запись доезжала до плана, а `apply` спотыкался о неё каждый
    раз — «нет файла» в отчёте, и так при любой уборке. Убрать её из отчёта
    было нельзя ничем, кроме как найти и удалить ссылку руками, а имя в
    списке ошибок выглядело как настоящая потеря файла.

    Ровно тот же случай описан в `_walk_managed` про стык Windows: на
    последнем витке `is_dir()` не отвечал, и стык уезжал в список файлов.
    Петлю тогда починили, а вход в список файлов остался открытым.
    """
    config = make_config(tmp_path)
    touch(tmp_path / "Медиа" / "Videos" / "живой.mp4")
    broken_link(tmp_path / "Медиа" / "Videos" / "битая")

    found = scan(tmp_path, config, deep=True)

    assert [p.name for p in found] == ["живой.mp4"]


def test_broken_link_inside_managed_folder_does_not_become_an_error(tmp_path):
    """Та же ссылка не должна превращаться в ошибку «нет файла» при уборке."""
    config = make_config(tmp_path)
    touch(tmp_path / "Медиа" / "Videos" / "клип.mp4")
    broken_link(tmp_path / "Медиа" / "Videos" / "битая")

    result = apply(build_plan(config, deep=True), config, dry_run=False)

    assert result.errors == []


def test_scan_still_walks_real_folders_inside_managed_ones(tmp_path):
    """Проверка на файл не должна закрыть обход настоящих подпапок."""
    config = make_config(tmp_path)
    touch(tmp_path / "Медиа" / "Videos" / "клип.mp4")

    found = scan(tmp_path, config, deep=True)

    assert [p.name for p in found] == ["клип.mp4"]


# --- вынос 3D включён, а пути нет ---


def write_rules(root):
    """Минимальные правила рядом с config.json — иначе всё уедет в Others."""
    (root / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"], "3D": ["gcode"]},
        "managed_folders": ["Медиа", "3D", "Others", "Videos", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")


def test_3d_enabled_without_path_is_reported(tmp_path):
    """Галочка «3D → отдельная папка» стоит, а путь пустой — и молчание.

    `external_3d_path` на пустой строке отдаёт None, поэтому планировщик
    ведёт себя ровно так, будто галочки нет: модели едут в обычные категории.
    Снаружи это неотличимо от исправной работы — настройка включена, план
    построен, жалоб нет. Ровно тот же исход, что у забытого `extensions`,
    который здесь уже чинили: настройка выглядит рабочей, но не выносит ничего.
    """
    write_rules(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path / "dl"),
        "external_3d": {"enabled": True, "path": ""},
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert any("3D" in p and "путь" in p for p in config.problems), (
        f"о включённом выносе без пути не сказано ни слова: {config.problems}")


def test_3d_enabled_with_path_stays_quiet(tmp_path):
    """Настроенный вынос 3D не должен ворчать на ровном месте."""
    write_rules(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path / "dl"),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d")},
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert config.problems == []


def test_3d_switched_off_without_path_stays_quiet(tmp_path):
    """Выключенный вынос без пути — обычное дело, жаловаться не на что."""
    write_rules(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path / "dl"),
        "external_3d": {"enabled": False, "path": ""},
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert config.problems == []


# --- своя папка, отличающаяся регистром ---

case_insensitive = pytest.mark.skipif(
    os.path.normcase("A") == "A", reason="файловая система различает регистр")


@case_insensitive
def test_deep_scan_enters_managed_folder_written_in_other_case(tmp_path):
    """Windows считает `Медиа` и `медиа` одной папкой, а сортировщик — разными.

    `mkdir` не переименовывает уже существующую папку: стоит ей появиться
    раньше в другом регистре — от прошлой версии правил, от руки, от другой
    программы, — и файлы молча укладываются в неё. Снаружи всё в порядке:
    план показан, файлы разложены. Но `managed_folders` сверялся строка в
    строку, поэтому такая папка переставала быть своей: «Переразложить старое»
    в неё не заходило, новые категории до лежащего внутри не доезжали никогда,
    и пустой её никто не убирал. Чёрная дыра ровно того вида, о котором
    предупреждает `_check_managed`, — только заметить её нечем.
    """
    config = make_config(tmp_path)
    touch(tmp_path / "медиа" / "videos" / "клип.mp4")

    found = scan(tmp_path, config, deep=True)

    assert [p.name for p in found] == ["клип.mp4"], (
        "переразложение не увидело файл в своей же папке, "
        "написанной другим регистром")


@case_insensitive
def test_cleanup_removes_emptied_folder_written_in_other_case(tmp_path):
    """Уборка пустых папок спотыкалась о регистр так же, как обход."""
    config = make_config(tmp_path)
    src = touch(tmp_path / "медиа" / "videos" / "отчёт.pdf")

    apply([Move(src, tmp_path / "Код" / "Documents" / "отчёт.pdf")],
          config, dry_run=False)

    assert not (tmp_path / "медиа").exists(), (
        "опустевшая папка программы осталась лежать из-за регистра")


@case_insensitive
def test_foreign_folder_is_still_not_entered(tmp_path):
    """Смягчение сравнения не должно открыть дорогу в чужие папки."""
    config = make_config(tmp_path)
    touch(tmp_path / "мир minecraft" / "level.dat")

    found = scan(tmp_path, config, deep=True)

    assert found == []


# --- опечатка в регулярке ---


def write_rules_with_patterns(root, patterns):
    (root / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "patterns": patterns,
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Скриншоты", "Others", "Videos", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (root / "config.json").write_text(json.dumps({
        "downloads_path": str(root / "dl")}, ensure_ascii=False), encoding="utf-8")


def test_broken_regular_expression_is_reported(tmp_path):
    """Опечатка в шаблоне не роняла ничего — и в этом вся беда.

    `match_pattern` ловит `re.error` и идёт дальше, поэтому битое выражение
    выглядит как исправное правило, которое почему-то ни разу не сработало:
    скриншоты уезжают в Others, план построен, жалоб нет. Проверить нечем —
    пометку «шаблон» в предпросмотре пишут только когда шаблон подошёл.
    """
    write_rules_with_patterns(tmp_path, {"Скриншоты": [r"^(Снимок экрана \d{4}-\d{2}-\d{2}"]})

    config = Config.load(tmp_path / "config.json")

    assert any("Снимок экрана" in p for p in config.problems), (
        f"о сломанном шаблоне не сказано ни слова: {config.problems}")
    assert config.patterns["Скриншоты"] == []


def test_working_expression_next_to_a_broken_one_survives(tmp_path):
    """Одна опечатка не должна уносить с собой соседние шаблоны."""
    write_rules_with_patterns(
        tmp_path, {"Скриншоты": [r"^(Снимок экрана \d{4}-\d{2}-\d{2}", r"^screenshot"]})

    config = Config.load(tmp_path / "config.json")

    assert config.patterns["Скриншоты"] == [r"^screenshot"]
    assert explain_category("screenshot_01.png", "", config) == ("Скриншоты", "шаблон")


def test_correct_patterns_stay_quiet(tmp_path):
    """Исправные шаблоны не должны собирать жалобы на ровном месте."""
    write_rules_with_patterns(tmp_path, {"Медиа": [r"^\d{4}-\d{4}\.mp4$"]})

    config = Config.load(tmp_path / "config.json")

    assert config.problems == []


# --- вынос 3D в никуда ---


def make_3d_config(root, path, enabled=True):
    config = make_config(root)
    config.type_map = {"Models": ["stl"], **config.type_map}
    config.managed_folders = [*config.managed_folders, "Models"]
    config.external_3d = {"enabled": enabled, "path": path, "extensions": ["stl"]}
    return config


def test_incomplete_3d_path_does_not_take_files_out_of_downloads(tmp_path, monkeypatch):
    r"""Неполный путь уносил модели в рабочую папку программы.

    Категорию от пути программа бережёт (`_is_folder_name`): полный путь,
    вписанный вместо имени папки, уносит файлы из загрузок неизвестно куда.
    С путём внешней папки 3D всё зеркально: имя без диска (`All_3d`, опечатка,
    правка руками) — это путь от рабочей папки, а она у ярлыка какая угодно.
    План при этом показывает `All_3d\stl\деталь.stl` — строку, неотличимую
    от папки внутри загрузок. Файл уезжает не туда, куда обещано, и сказать
    об этом некому.
    """
    workdir = tmp_path / "рабочая папка программы"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    touch(tmp_path / "деталь.stl")
    config = make_3d_config(tmp_path, "All_3d")

    moves = build_plan(config, send_3d_external=True)

    assert [mv.dst for mv in moves] == [tmp_path / "Others" / "Models" / "деталь.stl"], (
        "неполный путь увёл модель из загрузок")


def test_incomplete_3d_path_is_reported(tmp_path):
    """О таком пути надо сказать вслух: сам по себе он выглядит исправным."""
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]}, "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": True, "path": "All_3d"},
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert any("All_3d" in p for p in config.problems), (
        f"о неполном пути не сказано ни слова: {config.problems}")


def test_full_3d_path_stays_quiet(tmp_path):
    """Исправный путь не должен собирать жалобы на ровном месте."""
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]}, "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d")},
    }, ensure_ascii=False), encoding="utf-8")

    assert Config.load(tmp_path / "config.json").problems == []


def test_3d_without_path_is_explained_to_every_interface(tmp_path):
    """Окно про пустой путь говорило, консоль — нет.

    Предупреждало не окно и не консоль, а `Config.load` — по галочке,
    сохранённой в файле. Флаг `--to3d` включает вынос поверх выключенной
    галочки, и тогда не предупреждал никто: модели молча ехали в обычные
    категории. Текст теперь общий, чтобы все трое говорили одно и то же.
    """
    config = make_3d_config(tmp_path, "", enabled=False)

    assert "не указан" in external_3d_warning(config, send_3d_external=True)
    assert external_3d_warning(config, send_3d_external=False) == ""


def test_incomplete_3d_path_is_explained_to_every_interface(tmp_path):
    config = make_3d_config(tmp_path, "All_3d")

    warning = external_3d_warning(config, send_3d_external=True)

    assert "All_3d" in warning


def test_incomplete_3d_path_is_reported_even_with_the_box_unticked(tmp_path):
    """Разбор корня All_3d идёт всегда, когда путь задан, — и его тоже отменяет.

    Галочка отвечает только за вынос моделей из загрузок. Негодный путь
    выключает заодно и раскладку самой All_3d по подпапкам расширений, а об
    этом при снятой галочке не говорил никто.
    """
    config = make_3d_config(tmp_path, "All_3d", enabled=False)

    assert "All_3d" in external_3d_warning(config, send_3d_external=False)


def test_working_3d_path_says_nothing(tmp_path):
    config = make_3d_config(tmp_path, str(tmp_path / "All_3d"))

    assert external_3d_warning(config, send_3d_external=True) == ""
    assert external_3d_warning(config, send_3d_external=False) == ""


# --- чем выбрано место, куда поедет модель ---


def test_3d_move_is_marked_by_extension(tmp_path):
    r"""Пометка причины врала про модели, уезжающие во внешнюю папку.

    Место им выбрало расширение, а в `note` уезжала причина выбора категории,
    которая тут ни при чём. В предпросмотре это выглядело как
    `All_3d\stl\деталь.stl   (не опознан)`, хотя «не опознан» по таблице в
    README значит «едет в Others». Просматривать план README советует именно
    по этой пометке — то есть врала она ровно там, где на неё смотрят.
    """
    touch(tmp_path / "деталь.stl")
    config = make_3d_config(tmp_path, str(tmp_path / "All_3d"))

    moves = build_plan(config, send_3d_external=True)

    assert [mv.note for mv in moves] == ["по расширению"]


def test_3d_folder_move_is_marked_the_same_way(tmp_path):
    """Одна и та же раскладка по расширениям — одна и та же пометка.

    Файлы из корня All_3d ехали в те же подпапки вовсе без пометки: две
    соседние строки плана с одинаковым назначением объясняли себя по-разному.
    """
    external = tmp_path / "All_3d"
    touch(external / "рассыпуха.stl")
    config = make_3d_config(tmp_path, str(external))

    moves = build_plan(config, send_3d_external=True)

    assert [mv.note for mv in moves] == ["по расширению"]


# --- правило без категории ---


def test_rule_without_category_is_dropped(tmp_path):
    """Пустая категория в overrides.json — правило, которого нет.

    `explain_category` берёт правило через `or`, поэтому пустая строка
    проваливается дальше к шаблонам и словам: файл едет так, будто правила и
    не было. А `_ai_done` смотрит на само наличие ключа — и ответ модели про
    такое имя выбрасывает, «сберегая» правило, которого нет. Кнопка «✨ИИ»
    из-за этого снова превращалась в оплаченную пустышку, но уже точечно:
    имя уходит в запрос (`_has_rule` пустую строку правилом не считает),
    деньги платятся, ответ выбрасывается, а окно отчитывается «свои правила
    сохранены». Файл при этом остаётся в Others навсегда.

    Разбираем это там же, где разбирают все прочие записи, которые выглядят
    правилом и им не являются, — при чтении, один раз и вслух.
    """
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]}, "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path)}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(json.dumps({
        "пустое.pdf": "", "нормальное.pdf": "Документы",
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert config.overrides == {"нормальное.pdf": "Документы"}
    assert any("пустое.pdf" in p for p in config.problems), (
        f"о правиле без категории не сказано ни слова: {config.problems}")


# --- правил нет вовсе ---


def test_missing_rules_file_is_reported(tmp_path):
    """Без `rules.json` программа молча сметала всё в Others.

    Файл лежит рядом с программой отдельно от `config.json`, и пропасть ему
    просто: из архива скопировали один `.exe`, антивирус унёс файл в карантин,
    установку перенесли на другую машину наполовину. Категорий тогда ноль,
    `managed_folders` пуст — и уборка выглядит совершенно обычной: план
    построен, файлы разложены по `Others/Misc`, жалоб никаких.

    Отличить это от честно неопознанных загрузок нельзя ничем, а последствия
    расходятся: `Others` в пустом `managed_folders` не значится, значит
    «Переразложить старое» в неё не зайдёт и разгрести не поможет, пока файл
    правил не вернётся на место.
    """
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path)}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert any("rules.json" in p for p in config.problems), (
        f"о пропавшем файле правил не сказано ни слова: {config.problems}")


def test_rules_without_a_single_rule_are_reported(tmp_path):
    """Файл на месте, а правил в нём нет — тот же исход, тот же разговор."""
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {}, "patterns": {}, "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Others", "Documents"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path)}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(tmp_path / "config.json")

    assert any("Others" in p and "правил" in p.lower() for p in config.problems), (
        f"о пустых правилах не сказано ни слова: {config.problems}")


def test_rules_from_patterns_alone_are_enough(tmp_path):
    """Раскладка на одних шаблонах — рабочая настройка, а не поломка."""
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {}, "patterns": {"Скриншоты": [r"^screenshot"]},
        "type_map": {"Images": ["png"]},
        "managed_folders": ["Скриншоты", "Others", "Images", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path)}, ensure_ascii=False), encoding="utf-8")

    assert Config.load(tmp_path / "config.json").problems == []


def test_network_folder_is_a_full_path(tmp_path):
    r"""Сетевая папка (`\\сервер\общая\All_3d`) — законная настройка.

    Проверка полноты пути не должна отрезать её заодно с `All_3d`: у UNC-пути
    нет буквы диска, но место он задаёт однозначно, и от рабочей папки он не
    считается.
    """
    config = make_3d_config(tmp_path, r"\\сервер\общая\All_3d")

    assert external_3d_warning(config, send_3d_external=True) == ""
    assert usable_3d_path(r"\\сервер\общая\All_3d")


def test_path_from_the_root_of_the_current_drive_is_not_enough(tmp_path):
    r"""`\All_3d` — папка в корне того диска, на котором программа сейчас.

    Какого именно — зависит от того, откуда её запустили. Это ровно та же
    неопределённость, что и у `All_3d`, только выглядит убедительнее.
    """
    config = make_3d_config(tmp_path, r"\All_3d")

    assert "All_3d" in external_3d_warning(config, send_3d_external=True)


# --- пустая строка там, где ждут слово или выражение ---


def write_rules_with_words(root, categories, patterns=None, type_map=None):
    (root / "rules.json").write_text(json.dumps({
        "categories": categories,
        "patterns": patterns or {},
        "type_map": type_map or {"Videos": ["mp4"], "Installers": ["exe"]},
        "managed_folders": ["Медиа", "Программы", "Скриншоты", "Others",
                            "Videos", "Installers", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (root / "config.json").write_text(json.dumps({
        "downloads_path": str(root / "dl")}, ensure_ascii=False), encoding="utf-8")
    return Config.load(root / "config.json")


def test_empty_keyword_does_not_swallow_every_file(tmp_path):
    """Пустая строка в списке слов забирала себе всю папку загрузок.

    `match_category` ищет вхождение подстроки, а пустая строка входит в любое
    имя. Одна такая запись — недописанная правка, стёртое слово, список,
    собранный скриптом, — и первая же категория забирает вообще всё: и
    `setup.exe`, и `договор.pdf`, и то, для чего правила писались.

    Заметить это нечем. План построен, файлы разложены, жалоб нет, а в
    предпросмотре у каждой строки честная пометка «слово» — та самая, которой
    README велит доверять. От правильной раскладки отличить нельзя ничем.

    Это ровно тот случай, ради которого `_rule_map` выбрасывает список слов,
    записанный строкой: там `"Медиа": "клип"` перебирался по буквам и в «Медиа»
    уезжало всё. Пустая строка внутри списка делает то же самое, а проверки на
    неё не было.
    """
    config = write_rules_with_words(tmp_path, {"Медиа": ["клип", ""], "Программы": ["setup"]})

    assert explain_category("setup.exe", "", config) == ("Программы", "слово"), (
        "пустое слово забрало файл у категории, которая его честно опознаёт")
    assert explain_category("непонятно.xyz", "", config) == ("Others", "не опознан")


def test_empty_keyword_is_reported(tmp_path):
    """Молча выбросить нельзя: человек написал слово и ждёт, что оно работает."""
    config = write_rules_with_words(tmp_path, {"Медиа": ["клип", ""]})

    assert any("Медиа" in p for p in config.problems), (
        f"о пустом слове не сказано ни слова: {config.problems}")
    assert config.categories["Медиа"] == ["клип"]


def test_empty_pattern_does_not_swallow_every_file(tmp_path):
    """Пустая регулярка подходит к любому имени — и уносит всю папку.

    Шаблоны сильнее слов: они проверяются раньше. Пустое выражение поэтому
    забирает файлы даже у категорий, которые опознают их по слову, и делает
    это с пометкой «шаблон».
    """
    config = write_rules_with_words(
        tmp_path, {"Медиа": ["клип"]}, patterns={"Скриншоты": ["", r"^screenshot"]})

    assert explain_category("клип.mp4", "", config) == ("Медиа", "слово"), (
        "пустой шаблон забрал файл у категории, которая его честно опознаёт")
    assert explain_category("screenshot_01.png", "", config) == ("Скриншоты", "шаблон"), (
        "соседнее рабочее выражение должно было уцелеть")


def test_empty_pattern_is_reported(tmp_path):
    """О пустом выражении говорим так же, как о битом."""
    config = write_rules_with_words(tmp_path, {"Медиа": ["клип"]}, patterns={"Скриншоты": [""]})

    assert any("Скриншоты" in p for p in config.problems), (
        f"о пустом шаблоне не сказано ни слова: {config.problems}")
    assert config.patterns["Скриншоты"] == []


def test_empty_extension_is_not_a_type(tmp_path):
    """Пустая строка в `type_map` делала своим типом файлы без расширения.

    `match_type` сравнивает расширение на равенство, и у файла без расширения
    оно пустое. Такой файл получал настоящий тип вместо запасного — то есть
    уезжал в чужую папку, и снова молча.
    """
    config = write_rules_with_words(
        tmp_path, {"Медиа": ["клип"]}, type_map={"Videos": ["mp4", ""]})

    assert match_type("", config.type_map, config.fallback_type) == "Misc"


# --- отчёт, который спорит сам с собой ---


class _Boom:
    """Подменяет `shutil.move`: перемещение упало уже после проверки имени."""

    message = "файл занят другой программой"

    def __call__(self, src, dst):
        raise OSError(self.message)


def test_failed_move_is_not_reported_as_moved_under_another_name(tmp_path, monkeypatch):
    """Отчёт называл один и тот же файл и непереехавшим, и переехавшим.

    Оговорку «в цели уже есть X, положили как Y» `apply` ставил ДО
    `shutil.move`. Между проверкой имени и перемещением падать есть от чего:
    файл открыт другой программой, кончилось место, сняли диск. Тогда файл
    попадал сразу в оба списка отчёта — «Не переехали» с причиной и «Легли под
    другим именем» с именем `клип (1).mp4`, которого на диске нет.

    Отчёт этот пишут ровно затем, чтобы человек знал, где искать свой файл.
    Здесь он отправлял искать несуществующее имя, и заодно противоречил
    соседней строке о том же файле.
    """
    import shutil

    src = touch(tmp_path / "клип.mp4")
    dst = touch(tmp_path / "Медиа" / "Videos" / "клип.mp4", "чужой")
    monkeypatch.setattr(shutil, "move", _Boom())

    result = apply([Move(src, dst)], make_config(tmp_path), dry_run=False)

    assert result.moved == 0
    assert len(result.errors) == 1
    assert result.notes == [], (
        "файл не переехал, а отчёт обещает искать его под другим именем: "
        f"{result.notes}")
    assert "клип (1).mp4" not in report(result)


def test_successful_dedup_still_says_the_new_name(tmp_path):
    """Оговорка не должна пропасть там, где файл и правда лёг рядом."""
    src = touch(tmp_path / "клип.mp4")
    dst = touch(tmp_path / "Медиа" / "Videos" / "клип.mp4", "чужой")

    result = apply([Move(src, dst)], make_config(tmp_path), dry_run=False)

    assert result.moved == 1
    assert (tmp_path / "Медиа" / "Videos" / "клип (1).mp4").exists()
    assert "клип (1).mp4" in report(result)


def test_failed_undo_does_not_claim_the_file_came_back(tmp_path, monkeypatch):
    """То же самое в откате: «вернули как X» при упавшем возврате.

    Молчаливый успешный откат README называет худшим из исходов — но откат,
    который называет имя, которого нет, ничем не лучше: файл остался на новом
    месте, а человек ищет его на старом под номером.
    """
    import shutil

    home = touch(tmp_path / "клип.mp4", "занято руками")
    moved = touch(tmp_path / "Медиа" / "Videos" / "клип.mp4", "переехавший")
    log = tmp_path / "undo.json"
    log.write_text(
        json.dumps([{"src": str(home), "dst": str(moved)}]), encoding="utf-8")
    monkeypatch.setattr(shutil, "move", _Boom())

    notes = undo(log)

    assert not any("вернули как" in why for _, why in notes), (
        f"откат упал, а отчёт говорит, что файл вернулся: {notes}")
    assert any(_Boom.message in why for _, why in notes)


def test_successful_undo_still_names_the_place_it_used(tmp_path):
    """А удавшийся возврат под другим именем по-прежнему называет это имя."""
    home = touch(tmp_path / "клип.mp4", "занято руками")
    moved = touch(tmp_path / "Медиа" / "Videos" / "клип.mp4", "переехавший")
    log = tmp_path / "undo.json"
    log.write_text(
        json.dumps([{"src": str(home), "dst": str(moved)}]), encoding="utf-8")

    notes = undo(log)

    assert any("клип (1).mp4" in why for _, why in notes), notes
    assert (tmp_path / "клип (1).mp4").exists()


# --- настройки 3D нет в файле вовсе ---


def test_missing_3d_setting_still_knows_the_extensions(tmp_path):
    """У нового пользователя ключа `external_3d` в config.json нет.

    `_clean_3d` отвечал на это голым `{}` — без списка расширений. Дальше
    всё выглядело исправно: человек ставит галочку «3D → отдельная папка»,
    выбирает папку через «Обзор…», путь годный, предупреждения нет, план
    построен — и ни одна модель в эту папку не едет, потому что совпадать
    расширению не с чем. Само чинилось только после закрытия и повторного
    открытия окна: список расширений дописывает `save` на выходе.

    Ровно тот же исход, что у забытого `extensions` внутри объекта, — там его
    подставляли давно, а здесь ветка возвращала пустоту.
    """
    downloads = tmp_path / "загрузки"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    touch(downloads / "деталь.stl")

    config = Config.load(cfg_path)
    # окно: галочка + путь через «Обзор…», ещё до первого сохранения
    config.external_3d["enabled"] = True
    config.external_3d["path"] = str(tmp_path / "All_3d")

    moves = build_plan(config, send_3d_external=True)

    assert [m.dst for m in moves] == [tmp_path / "All_3d" / "stl" / "деталь.stl"]


def test_broken_3d_setting_also_keeps_the_extensions(tmp_path):
    """`"external_3d": "C:/All_3d"` после правки руками — то же самое."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(tmp_path / "загрузки"),
        "external_3d": "C:/All_3d",
    }), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.problems
    assert config.external_3d["extensions"]


# --- пометка причины: слово в имени или слово внутри файла ---


def test_word_found_inside_the_file_is_named_apart(tmp_path):
    """`README.md` уезжал в «Учёбу» с пометкой «слово», которого в имени нет.

    Содержимое читается у `txt`, `md` и `csv`, и совпадение по нему получало ту
    же пометку, что совпадение по имени. Проверить её было нечем: смотришь на
    строку плана, ищешь в имени слово «Учёбы» и не находишь ни одного. README
    при этом советует просматривать в первую очередь именно строки «слово» —
    то есть непроверяемой оказывалась ровно та пометка, на которую велено
    смотреть. Тот же случай, что у выноса 3D: причина названа, но не та.
    """
    config = make_config(tmp_path)
    config.categories["Учёба"] = ["экзамен"]
    config.managed_folders.append("Учёба")
    touch(tmp_path / "README.md", "готовлюсь к экзамену")

    moves = build_plan(config)

    assert [m.note for m in moves] == ["слово в файле"]
    assert moves[0].dst.parent.parent.name == "Учёба"


def test_word_found_in_the_name_keeps_the_old_note(tmp_path):
    """А совпадение по имени называется по-прежнему — пометку не переименовали."""
    config = make_config(tmp_path)
    touch(tmp_path / "клип.mp4")

    moves = build_plan(config)

    assert [m.note for m in moves] == ["слово"]


def test_extension_word_still_ignored_inside_the_content(tmp_path):
    """Строка «скачай installer.exe» в заметке программой её не делает."""
    config = make_config(tmp_path)
    touch(tmp_path / "заметка.txt", "скачай installer.exe и запусти")

    moves = build_plan(config)

    assert [m.note for m in moves] == ["не опознан"]


# --- расширения выноса 3D пишут то с точкой, то без ---


def test_3d_extensions_written_with_a_dot_still_work(tmp_path):
    """`"extensions": [".stl"]` после правки руками — вынос молча переставал работать.

    В одном проекте живут три написания одного и того же: слова категорий
    пишутся с точкой (`".stl"`), `type_map` — без, а `external_3d.extensions`
    сверяется с расширением файла, то есть тоже без. Сверка шла строка в
    строку, `".stl"` не совпадало с `"stl"` ни разу, и галочка «3D → отдельная
    папка» переставала выносить. Заметить нечем: путь годный, предупреждения
    нет, план построен.
    """
    cfg_path = tmp_path / "config.json"
    downloads = tmp_path / "загрузки"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {
            "enabled": True,
            "path": str(tmp_path / "All_3d"),
            "extensions": [".STL", " .obj "],
        },
    }), encoding="utf-8")
    touch(downloads / "деталь.stl")

    config = Config.load(cfg_path)

    assert config.external_3d["extensions"] == ["stl", "obj"]
    assert not [p for p in config.problems if "extensions" in p], config.problems
    moves = build_plan(config, send_3d_external=True)
    assert [m.dst for m in moves] == [tmp_path / "All_3d" / "stl" / "деталь.stl"]


def test_empty_3d_extension_would_have_taken_every_file_without_one(tmp_path):
    """Пустая строка в списке равна расширению файла без расширения.

    `extension_of` отдаёт для такого файла пустую строку, значит одна пустая
    запись увела бы во внешнюю папку 3D все файлы без расширения разом. Список
    после чистки пуст, поэтому берём список по умолчанию — но говорим об этом
    вслух: человек ограничивал вынос нарочно.
    """
    cfg_path = tmp_path / "config.json"
    downloads = tmp_path / "загрузки"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {
            "enabled": True,
            "path": str(tmp_path / "All_3d"),
            "extensions": ["", "  "],
        },
    }), encoding="utf-8")
    touch(downloads / "LICENSE")

    config = Config.load(cfg_path)

    assert config.external_3d["extensions"] == ["3mf", "obj", "stl", "gcode"]
    assert any("extensions" in p for p in config.problems), config.problems
    moves = build_plan(config, send_3d_external=True)
    assert all("All_3d" not in str(m.dst) for m in moves), moves


# --- расширение, названное в двух типах ---


def test_extension_named_in_two_types_is_reported(tmp_path):
    """Вторая запись не работает никогда, а выглядит как обычное правило.

    `match_type` отдаёт первый подошедший тип. Понять, почему `.exr` лёг в
    `Images`, если в `type_map["3D"]` он тоже написан, можно было только зная
    про этот порядок. Тот же случай, что у битой регулярки: запись похожа на
    рабочее правило, но правилом не является.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"3D": [".exr"]},
        "type_map": {"Images": ["png", "exr"], "3D": ["stl", "EXR"]},
        "managed_folders": ["3D", "Images", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert any("«EXR»" in p and "Images" in p for p in config.problems), config.problems
    assert match_type("exr", config.type_map) == "Images"


def test_extension_repeated_inside_one_type_is_not_reported(tmp_path):
    """Повтор внутри одного типа ничего не ломает — о нём молчим."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Код": [".py"]},
        "type_map": {"Code": ["py", "py"]},
        "managed_folders": ["Код", "Code", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert not [p for p in config.problems if "названо и в типе" in p], config.problems


# --- правило, записанное другим регистром ---


@case_insensitive
def test_override_matches_name_written_in_other_case(tmp_path):
    """Windows считает `Отчёт.pdf` и `отчёт.pdf` одним файлом, разбор — разными.

    Ровно та же поломка, что у папки `Медиа`/`медиа`, только этажом ниже.
    Ручное правило пишут руками, глядя на файл в проводнике, а проводник
    показывает имя как хочет — с заглавной, целиком капсом после переименования
    из другой программы. Ключ, набранный не тем регистром, не совпадает ни с
    чем: правило есть, выглядит рабочим, но не срабатывает никогда, а файл
    молча уезжает по ключевым словам или в `Others`.
    """
    config = make_config(tmp_path)
    config.overrides = {"отчёт.pdf": "Учёба"}

    assert explain_category("Отчёт.pdf", "", config) == ("Учёба", "правило")
    assert explain_category("ОТЧЁТ.PDF", "", config) == ("Учёба", "правило")


@case_insensitive
def test_override_matches_dedup_number_in_other_case(tmp_path):
    """Правило без номера покрывает и `(1)`, набранный другим регистром."""
    config = make_config(tmp_path)
    config.overrides = {"отчёт.pdf": "Учёба"}

    assert explain_category("Отчёт (1).pdf", "", config) == ("Учёба", "правило")


def test_exact_override_still_wins_over_the_one_differing_by_case(tmp_path):
    """Точное совпадение важнее: две записи — берём ту, что написана как файл."""
    config = make_config(tmp_path)
    config.overrides = {"отчёт.pdf": "Учёба", "Отчёт.pdf": "Программы"}

    assert explain_category("Отчёт.pdf", "", config) == ("Программы", "правило")
    assert explain_category("отчёт.pdf", "", config) == ("Учёба", "правило")


# --- имя папки, которое файловая система запишет иначе ---


def test_category_with_trailing_space_is_rejected(tmp_path):
    """`"Учёба "` выглядит категорией, а перемещения падают все до одного.

    Windows отрезает у имени папки хвостовые пробелы и точки. План при этом
    строится обычный — `Учёба \\Documents\\отчёт.pdf`, глазами от исправной
    строки не отличить, — а `mkdir` создаёт `Учёба`, перемещение ищет
    `Учёба \\Documents` и падает с `[WinError 3]`. В загрузках остаётся
    неразобранный файл и пустая папка, которую никто не просил.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Учёба ": ["класс"], "Медиа": ["клип"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Учёба", "Медиа", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert "Учёба " not in config.categories
    assert "Медиа" in config.categories, "соседняя категория не должна пострадать"
    assert any("Учёба " in p for p in config.problems), config.problems


def test_category_with_trailing_dot_is_rejected(tmp_path):
    """`"Учёба."` хуже пробела: перемещение проходит, но не туда, куда обещало.

    Windows отрезает точку, файлы ложатся в `Учёба`, отчёт рапортует успех — а
    в `managed_folders` записана `Учёба.`, которой на диске нет. Настоящая
    папка своей не считается: «Переразложить старое» в неё не заходит, пустой
    её никто не убирает. Чёрная дыра, о которой предупредить нечем: имя-то в
    правилах написано, проверка молчит.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Учёба.": ["класс"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Учёба.", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert "Учёба." not in config.categories
    assert any("Учёба." in p for p in config.problems), config.problems


def test_category_of_spaces_alone_is_rejected(tmp_path):
    """Имя из одних пробелов не создаётся вовсе — а проверку проходило."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"   ": ["класс"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.categories == {}
    assert any("имя папки" in p for p in config.problems), config.problems


def test_override_category_with_trailing_space_is_rejected(tmp_path):
    """Ручное правило `"файл.pdf": "Учёба "` — тот же тупик, тот же ответ."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"отчёт.pdf": "Учёба "}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.overrides == {}
    assert any("Учёба " in p for p in config.problems), config.problems


def test_ordinary_names_are_still_allowed(tmp_path):
    """Смягчать нечего, но и лишнего запрещать не надо."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Учёба/2026": ["класс"], "3D": ["blender"], "C++": ["gcc"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Учёба", "2026", "3D", "C++", "Documents",
                            "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert set(config.categories) == {"Учёба/2026", "3D", "C++"}
    assert not [p for p in config.problems if "имя папки" in p], config.problems


# --- ответ ИИ про имена, различающиеся только регистром ---


def test_ai_answer_about_two_names_differing_by_case_keeps_both():
    """`readme.md` и `README.md` — на Windows разные файлы в разных папках.

    Сверка ответа со списком спрошенных складывала имена в словарь по нижнему
    регистру, и второе имя затирало первое. Ответ про один файл пропадал
    молча — деньги за вопрос заплачены, правило не записано, — а второму
    доставалась чужая категория: пары «ключ → имя» перепутаны.
    """
    got = parse_ai_response(
        '{"readme.md": "Код", "README.md": "Учёба"}',
        ["Код", "Учёба"], "Others",
        requested=["readme.md", "README.md"])

    assert got == {"readme.md": "Код", "README.md": "Учёба"}


def test_ai_answer_in_other_case_is_still_matched_when_it_is_unambiguous():
    """Модель нарушает «повторяй символ в символ» — одно имя вернуть можно."""
    got = parse_ai_response(
        '{"readme.md": "Учёба"}', ["Учёба"], "Others", requested=["README.md"])

    assert got == {"README.md": "Учёба"}


def test_ai_answer_in_other_case_is_dropped_when_it_could_be_either():
    """Два кандидата — угадывать нельзя: правило встанет не на тот файл."""
    got = parse_ai_response(
        '{"Readme.md": "Учёба"}', ["Учёба"], "Others",
        requested=["readme.md", "README.md"])

    assert got == {}


def test_ai_category_written_in_other_case_is_accepted():
    """`3d` вместо `3D` — ответ верный, а выбрасывался как незнакомая категория.

    Разбор сверял название категории строка в строку, поэтому такой ответ
    становился запасной категорией, а фильтр «не сохранять незнание» его
    выбрасывал. Снаружи это «без решения»: вопрос задан и оплачен, файл
    остался неразобранным, и понять, что модель ответила верно, неоткуда.
    """
    got = parse_ai_response(
        '{"деталь.stl": "3d", "клип.mp4": " Медиа "}',
        ["3D", "Медиа"], "Others", requested=["деталь.stl", "клип.mp4"])

    assert got == {"деталь.stl": "3D", "клип.mp4": "Медиа"}


def test_ai_answer_with_a_truly_unknown_category_still_falls_back():
    """Смягчение регистра не должно пропускать выдумку модели."""
    got = parse_ai_response(
        '{"a.bin": "Криптовалюта"}', ["3D"], "Others", requested=["a.bin"])

    assert got == {"a.bin": "Others"}


# --- галочка выноса 3D, записанная не булевым значением ---


def test_3d_enabled_written_as_a_string_does_not_switch_the_export_on(tmp_path):
    """`"enabled": "false"` — строка, и она истинна: вынос включался наоборот.

    Все читатели настройки берут её через `bool(...)`, поэтому непустая строка
    значит «включено», как её ни напиши. Человек, поправивший `config.json`
    руками, получает ровно обратное тому, что написал: модели уезжают из
    загрузок, галочка в окне стоит, жалоб нет. Остальные поля этой настройки
    (`path`, `extensions`) разбор проверяет по типу и о чужом говорит вслух —
    `enabled` проверять забыли.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": "false", "path": str(tmp_path / "All_3d")},
    }), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.external_3d["enabled"] is False
    assert any("enabled" in p for p in config.problems), config.problems


def test_3d_enabled_written_properly_stays_quiet(tmp_path):
    """Настоящее булево значение проверку не замечает."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d")},
    }), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.external_3d["enabled"] is True
    assert not [p for p in config.problems if "enabled" in p], config.problems


# --- пробел на конце пути ---


def test_downloads_path_with_trailing_space_still_finds_the_files(tmp_path):
    """Пробел в конце пути — и папка выглядит уже прибранной.

    Windows отрезает хвостовые пробелы при проверке `is_dir()`, поэтому «Папка
    не найдена» не срабатывает, а обход такой папки не возвращает ничего.
    Наружу это выходит как «План готов: 0 шт.» — ровно то, что README разбирает
    на опечатке в `--path`, только там спасает проверка существования, а здесь
    она отвечает «папка на месте». Пробел попадает в путь легко: скопировали из
    письма, зацепили при правке `config.json` руками.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "клип.mp4").write_text("x", encoding="utf-8")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(downloads) + " "}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)
    moves = build_plan(config)

    assert [m.src.name for m in moves] == ["клип.mp4"]
    result = apply(moves, config, dry_run=False)
    assert result.moved == 1, result.errors
    assert (downloads / "Медиа" / "Videos" / "клип.mp4").exists()


def test_3d_path_with_trailing_space_still_moves_the_models(tmp_path):
    """Тот же пробел во внешней папке 3D валит все перемещения разом.

    Путь проходит проверку («полный»), предупреждения нет, план показывает
    `All_3d \\stl\\деталь.stl` — от исправной строки не отличить. А `All_3d ` в
    середине пути Windows не находит, и каждая модель остаётся в загрузках с
    `[WinError 3]` в отчёте.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {"enabled": True,
                        "path": str(tmp_path / "All_3d") + " ",
                        "extensions": ["stl"]},
    }), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"3D": [".stl"]},
        "type_map": {"3D": ["stl"]},
        "managed_folders": ["3D", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)
    result = apply(build_plan(config, send_3d_external=True), config, dry_run=False)

    assert result.moved == 1, result.errors
    assert (tmp_path / "All_3d" / "stl" / "деталь.stl").exists()


def test_3d_path_with_leading_space_is_not_called_incomplete(tmp_path):
    """Пробел спереди объявлял полный путь «неполным» — жалоба не про то.

    `Path(" C:/All_3d").is_absolute()` — False, и человек читал, что по его
    пути «не видно ни диска», глядя на путь, где диск написан.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": True, "path": " " + str(tmp_path / "All_3d")},
    }), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.external_3d["path"] == str(tmp_path / "All_3d")
    assert not [p for p in config.problems if "неполный" in p], config.problems


def test_path_of_spaces_alone_is_treated_as_no_path(tmp_path):
    """Путь из одних пробелов — это «пути нет», а не рабочая настройка."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": "   ",
        "external_3d": {"enabled": True, "path": "   "},
    }), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.downloads_path != "   "
    assert config.external_3d["path"] == ""
    assert any("путь к папке не указан" in p for p in config.problems), config.problems


# --- расширение в type_map, записанное с точкой ---


def test_type_map_extension_written_with_a_dot_still_matches():
    """`".pdf"` в `type_map` — и все документы молча уезжают в `Misc`.

    Три написания одного и того же живут в этом проекте рядом: слова категорий
    пишутся с точкой (`".pdf"`), `type_map` — без, `external_3d.extensions` —
    тоже без. Точку `external_3d.extensions` научились прощать, а `type_map`
    остался сверкой строка в строку: `".pdf"` не совпадает с `"pdf"` ни разу.
    Наружу это выходит ровно так же, как выходил `"PDF"` заглавными до починки
    регистра — раскладка неверная, жалоб никаких.
    """
    assert match_type("pdf", {"Documents": [".pdf"]}) == "Documents"
    assert match_type("pdf", {"Documents": ["  PDF  "]}) == "Documents"
    assert match_type("pdf", {"Documents": ["docx"]}) == "Misc"


def test_type_map_extension_written_with_a_dot_counts_as_the_same_ghost(tmp_path):
    """Проверка правил-призраков тоже сверялась строка в строку.

    `".pdf"` в одном типе и `"pdf"` в другом — одно и то же расширение, вторая
    запись мертва. Молчать о ней нельзя по той же причине, по какой не молчат о
    паре `"exr"`/`"EXR"`.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Документы": ["договор"]},
        "type_map": {"Documents": [".pdf"], "Images": ["pdf"]},
        "managed_folders": ["Документы", "Documents", "Images", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert any("«pdf»" in p and "Documents" in p for p in config.problems), (
        config.problems)
    assert match_type("pdf", config.type_map) == "Documents"


# --- слово из одних пробелов ---


def test_keyword_of_spaces_alone_does_not_swallow_every_file(tmp_path):
    """Пробел вместо слова забирает почти всю папку — как пустая строка.

    Пустую строку разбор выбрасывает давно: она входит в любое имя, и первая
    же категория с ней забирает себе все загрузки. Строка из одних пробелов
    приходит тем же путём — недописанная правка, стёртое слово, список,
    собранный скриптом, — и делает почти то же самое: пробел есть в имени
    большинства скачанных файлов. Отличить это от исправной работы нельзя
    ничем, и в предпросмотре у каждой строки честная пометка «слово».
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["  "], "Документы": ["договор"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Документы", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.categories["Медиа"] == []
    assert any("Медиа" in p and "пуст" in p for p in config.problems), config.problems
    assert explain_category("договор об аренде.pdf", "", config)[0] == "Документы"


def test_pattern_of_spaces_alone_does_not_swallow_every_file(tmp_path):
    """У шаблонов пробел бьёт ещё раньше: они проверяются до слов."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Документы": ["договор"]},
        "patterns": {"Медиа": [" "]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Документы", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.patterns["Медиа"] == []
    assert explain_category("договор об аренде.pdf", "", config)[0] == "Документы"


def test_type_of_spaces_alone_is_not_a_type(tmp_path):
    """Пробел в `type_map` равен расширению файла, у которого его нет.

    Такая запись выдаёт файлам без расширения настоящий тип вместо запасного —
    ровно то, ради чего оттуда выбрасывают пустую строку.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Код": ["licen"]},
        "type_map": {"Documents": [" "]},
        "managed_folders": ["Код", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.type_map["Documents"] == []
    assert match_type("", config.type_map) == "Misc"


def test_match_type_ignores_an_empty_extension_written_by_hand():
    """Конфиг собирают и напрямую — из тестов, из CLI. Тут проверять некому."""
    assert match_type("", {"Documents": [""]}) == "Misc"
    assert match_type("", {"Documents": ["   "]}) == "Misc"
    assert match_type("", {"Documents": ["."]}) == "Misc"


def test_keyword_with_spaces_around_a_word_still_works(tmp_path):
    """Пробелы внутри слова — приём, а не опечатка: их не трогаем.

    `" фон "` — способ потребовать границы слова ключевым словом, и обрезать
    его значило бы менять правило, которое человек написал нарочно.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"3D": [" фон "]},
        "type_map": {"Images": ["png"]},
        "managed_folders": ["3D", "Images", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.categories["3D"] == [" фон "]
    assert explain_category("студийный фон .png", "", config)[0] == "3D"
    assert explain_category("телефон.png", "", config)[0] == "Others"


# --- правило в запасную категорию ---


def test_rule_into_the_fallback_category_is_dropped(tmp_path):
    """`0001-0250.mp4 → Others` не давало шаблону опознать рендер Blender.

    Ручное правило стоит выше шаблонов и слов, поэтому такая запись — это
    записанное «не знаю», которое закрывает файлу дорогу навсегда. Без неё файл
    уехал бы в ту же запасную папку, но по текущим правилам; с ней — мимо всего,
    что появилось потом. Отличить это от исправной работы нельзя ничем: план
    построен, жалоб нет, в предпросмотре честная пометка «правило».

    `ai.useful_rules` не даёт записать такое правило от модели, но записи,
    попавшие в файл раньше, читались как обычные: проверка стояла только на
    записи.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "patterns": {"3D": [r"^\d{4}-\d{4}\.(mp4|png)$"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "3D", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(json.dumps({
        "0001-0250.mp4": "Others",
        "клип.mp4": "Медиа",
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert "0001-0250.mp4" not in config.overrides
    assert config.overrides == {"клип.mp4": "Медиа"}, "чужие правила не трогаем"
    assert explain_category("0001-0250.mp4", "", config) == ("3D", "шаблон")
    assert any("0001-0250.mp4" in p for p in config.problems), config.problems


def test_rule_into_the_fallback_written_in_other_case_is_dropped_too(tmp_path):
    """`others` и `Others` на Windows — одна папка, и морозят одинаково."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"клип.mp4": "others"}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.overrides == {}
    assert explain_category("клип.mp4", "", config) == ("Медиа", "слово")


def test_rules_into_real_categories_stay_quiet(tmp_path):
    """Обычные ручные правила не трогаем и молчим о них."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "3D", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"деталь.mp4": "3D"}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.overrides == {"деталь.mp4": "3D"}
    assert not [p for p in config.problems if "запасную категорию" in p]


# --- символы, запрещённые в имени папки ---


def test_category_with_a_forbidden_character_is_rejected(tmp_path):
    """`"Отчёты?"` — план строится, а не переезжает ни один файл.

    `?`, `*`, `"`, `<`, `>`, `|` Windows в имени не разрешает вовсе: `mkdir`
    падает с `[WinError 123]` на каждом файле подряд. Ошибки в отчёте называют
    папку назначения, а не строку в правилах, из-за которой её нельзя создать.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Отчёты?": ["отчёт"], "Медиа": ["клип"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert "Отчёты?" not in config.categories
    assert "Медиа" in config.categories, "соседняя категория не должна пострадать"
    assert any("Отчёты?" in p for p in config.problems), config.problems


def test_colon_inside_a_nested_category_is_rejected(tmp_path):
    """Двоеточие в первой части ловилось как буква диска, во второй — ничем."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Учёба/2026: год": ["класс"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.categories == {}
    assert any("2026" in p for p in config.problems), config.problems


def test_forbidden_character_in_a_type_and_in_a_rule_is_rejected(tmp_path):
    """Тип и ручное правило создают папку так же — проверка одна на всех."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Video|Audio": ["mp4"]},
        "managed_folders": ["Медиа", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"отчёт.pdf": 'Уч"ёба'}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.type_map == {}
    assert config.overrides == {}
    assert any("Video|Audio" in p for p in config.problems), config.problems


# --- список расширений 3D, написанный не списком ---


def test_3d_extensions_written_as_a_string_are_reported(tmp_path):
    """`"extensions": "stl"` молча превращалось в полный список по умолчанию.

    То есть делало обратное написанному: человек сужал вынос до одного
    расширения, а из загрузок уезжали все четыре.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": True, "path": str(tmp_path / "All_3d"),
                        "extensions": "stl"},
    }), encoding="utf-8")

    config = Config.load(cfg_path)

    assert any("не список" in p for p in config.problems), config.problems


# --- слово, найденное внутри текста ---


def test_word_inside_a_longer_english_word_is_not_a_match():
    """`obs` ловилось в «observed», `demo` — в «demonstrating».

    В имени файла подстрока это приём: `задач` ловит и `задачи`, и `задачник`.
    В тексте на две тысячи знаков тот же приём находит слово случайно, и
    заметка про ИИ уезжает в «Программы» из-за OBS Studio. Проверить пометку
    «слово в файле» глазами нельзя — искомого слова в имени нет.
    """
    categories = {"Программы": ["obs"], "Учёба": ["demo"]}
    text = "these are observed patterns, demonstrating the problem"

    assert match_category("заметка.md", text, categories) is None


def test_whole_english_word_inside_the_text_still_matches():
    """Ради этого содержимое и читается — целое слово ловиться обязано."""
    categories = {"Программы": ["obs"]}

    assert match_category("заметка.md", "запись экрана в obs studio",
                          categories) == "Программы"


def test_russian_stem_inside_the_text_still_matches():
    """`решени` обязано ловить «решения»: русское слово склоняется.

    Русские слова в этих правилах написаны основами нарочно, поэтому граница
    требуется только спереди. Латинские — названия целиком, им нужны обе.
    """
    categories = {"Учёба": ["решени", "задач"]}

    assert match_category("конспект.md", "разбор задачи и решения к ней",
                          categories) == "Учёба"


def test_word_with_a_non_letter_edge_still_matches_inside_the_text():
    """`-fon.` и `счёт-` границу несут в себе — вторую требовать нельзя."""
    categories = {"Документы": ["счёт-"]}

    assert match_category("письмо.md", "приложен счёт-фактура за май",
                          categories) == "Документы"


def test_name_still_matches_by_substring():
    """Имя короткое, и подстрока в нём — приём, а не лотерея."""
    categories = {"Учёба": ["задач"]}

    assert match_category("задачник_9.pdf", "", categories) == "Учёба"


# --- имя папки, начинающееся с пробела ---


def test_category_with_leading_space_is_rejected(tmp_path):
    """`" Учёба"` — хвостовой пробел наоборот, и заметить его ещё труднее.

    Хвостовой Windows отрезает, поэтому перемещения падают или файлы уезжают в
    соседнюю папку. Ведущий пробел файловая система сохраняет как есть — то
    есть создаёт настоящую отдельную папку ` Учёба`, на вид неотличимую от
    `Учёба`. В `managed_folders` её нет: «Переразложить старое» в неё не
    заходит, пустой её никто не убирает, новые правила до лежащего внутри не
    доезжают никогда. Та самая чёрная дыра, только без единого способа её
    увидеть — в проводнике две такие папки стоят рядом и выглядят одинаково.

    Проверка смотрела лишь на `rstrip`, поэтому пропускала это молча.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {" Учёба": ["класс"], "Медиа": ["клип"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Учёба", "Медиа", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert " Учёба" not in config.categories
    assert "Медиа" in config.categories, "соседняя категория не должна пострадать"
    assert any(" Учёба" in p for p in config.problems), config.problems


def test_type_ending_with_a_nonbreaking_space_is_rejected(tmp_path):
    """`"Documents\\xa0"` — неразрывный пробел, которого `rstrip(" .")` не видит.

    Приходит он копипастом из письма или с веб-страницы. Windows его не
    отрезает, значит папка создаётся отдельная и на вид та же самая.
    """
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Documents ": ["pdf"], "Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Documents", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert "Documents " not in config.type_map
    assert "Videos" in config.type_map, "соседний тип не должен пострадать"
    assert any("Documents" in p and "пробел" in p for p in config.problems), config.problems


def test_override_category_with_leading_space_is_rejected(tmp_path):
    """Ручное правило `"отчёт.pdf": " Учёба"` — тот же тупик, тот же ответ."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(
        json.dumps({"отчёт.pdf": " Учёба"}, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.overrides == {}
    assert any(" Учёба" in p for p in config.problems), config.problems


def test_nested_category_with_a_space_after_the_slash_is_rejected(tmp_path):
    """`"Учёба/ 2026"` — вторая половина имени тоже уходит в `mkdir`."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Учёба/ 2026": ["класс"]},
        "type_map": {"Documents": ["pdf"]},
        "managed_folders": ["Учёба", "2026", "Documents", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert config.categories == {}
    assert any("2026" in p for p in config.problems), config.problems


# --- своя папка, записанная в managed_folders с лишним пробелом ---


def test_managed_folder_written_with_a_trailing_space_still_counts(tmp_path):
    """`"Медиа "` в managed_folders превращало настоящую `Медиа` в чёрную дыру.

    Сверка идёт строка в строку по правилам файловой системы, а имени с
    хвостовым пробелом на диске не бывает: Windows его отрезает. Совпадения
    нет никогда, поэтому «Переразложить старое» в `Медиа` не заходит и пустой
    её не убирает.

    Хуже последствий сама жалоба. `_check_managed` говорит «категория «Медиа»
    не указана в managed_folders», а человек смотрит в файл и видит там
    `Медиа`: сообщение выглядит враньём, и искать в нём невидимый пробел
    никому в голову не придёт. Отличить одно от другого можно только сравнив
    длины строк.

    Пробелы по краям поэтому срезаем, как у пути к загрузкам, и говорим об
    этом вслух.
    """
    downloads = tmp_path / "загрузки"
    (downloads / "Медиа" / "Videos").mkdir(parents=True)
    (downloads / "Медиа" / "Videos" / "клип.mp4").write_text("x", encoding="utf-8")

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"], "Игры": ["quest"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа ", "Игры", "Videos", "Others", "Misc"],
    }, ensure_ascii=False), encoding="utf-8")

    config = Config.load(cfg_path)

    assert "Медиа" in config.managed_folders
    assert any("Медиа " in p for p in config.problems), config.problems
    assert not [p for p in config.problems if "не указан" in p], (
        "жалоба про managed_folders противоречила бы файлу", config.problems)
    # А главное — папка снова своя: переразложение в неё заходит и видит,
    # что файл уже лежит правильно.
    assert build_plan(config, deep=True) == []


# --- текстовый файл не в UTF-8 ---


def test_keyword_inside_a_cp1251_text_is_found(tmp_path):
    """Содержимое читалось только как UTF-8, то есть русский cp1251 — никак.

    Так сохраняют .txt старые программы и .csv из Excel на русской Windows.
    `errors="ignore"` выбрасывал каждый нечитаемый байт, от текста оставались
    крохи латиницы, и ни одно русское слово в нём не находилось.

    Заметить это нельзя ничем: пометка у такого файла — честное «не опознан»,
    неотличимое от «слова в тексте и правда нет». Ключ DeepSeek давно читается
    во всех кодировках, которые предлагает Блокнот, — содержимое читалось
    в одной.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "заметка.txt").write_bytes(
        "смотри клип вечером".encode("cp1251"))

    moves = build_plan(make_config(downloads))

    assert [(str(mv.dst.relative_to(downloads)), mv.note) for mv in moves] == [
        (str(Path("Медиа") / "Documents" / "заметка.txt"), "слово в файле")]


def test_keyword_inside_a_utf16_text_is_found(tmp_path):
    """UTF-16 из Блокнота — второй вариант, который он предлагает сам."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "заметка.txt").write_bytes(
        "смотри клип вечером".encode("utf-16"))

    moves = build_plan(make_config(downloads))

    assert [str(mv.dst.relative_to(downloads)) for mv in moves] == [
        str(Path("Медиа") / "Documents" / "заметка.txt")]


def test_keyword_inside_a_utf8_text_with_bom_is_found(tmp_path):
    """«UTF-8 с BOM» — третий, и метка в начале не должна ничего ломать."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "заметка.txt").write_bytes(
        "смотри клип вечером".encode("utf-8-sig"))

    moves = build_plan(make_config(downloads))

    assert [str(mv.dst.relative_to(downloads)) for mv in moves] == [
        str(Path("Медиа") / "Documents" / "заметка.txt")]


def test_binary_file_named_txt_does_not_crash_the_plan(tmp_path):
    """Что угодно с именем .txt — план всё равно должен строиться."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    (downloads / "мусор.txt").write_bytes(bytes(range(256)) * 4)

    moves = build_plan(make_config(downloads))

    assert [mv.src.name for mv in moves] == ["мусор.txt"]


# --- незаписанный журнал отмены — не файл, оставшийся в загрузках ---


def _sorted_with_blocked_log(downloads):
    """Раскладывает папку так, что журнал отмены записать некуда.

    `.sorter` занимает файл, появившийся после построения плана: до плана он
    уехал бы в `Others` вместе со всеми и ничему не помешал.
    """
    config = make_config(downloads)
    moves = build_plan(config)
    (downloads / ".sorter").write_text("не папка", encoding="utf-8")
    return apply(moves, config, dry_run=False)


def test_unwritten_undo_log_is_not_counted_as_a_failed_move(tmp_path):
    """«Перемещено: 1, ошибок: 1» при одном файле, который переехал.

    Незаписанный журнал складывался в тот же список, что и файлы, оставшиеся
    в загрузках. Список этот отчёт печатает под заголовком «Не переехали», и
    оба его слова были неправдой: переехали все до одного, а «журнал отмены» —
    не файл и никуда не собирался. Заодно врал и счёт ошибок, по которому окно
    решает, показывать спокойное окно или тревожное.

    Настоящее последствие при этом терялось: файлы разложены, а вернуть их
    назад больше нечем — «🕘 История» об этой сортировке не знает. Это стоит
    сказать своими словами, а не прятать среди неудавшихся перемещений.
    """
    downloads = tmp_path / "загрузки"
    touch(downloads / "клип.mp4")

    result = _sorted_with_blocked_log(downloads)

    assert result.moved == 1
    assert result.errors == [], "переехали все — списку неудач взяться неоткуда"
    assert result.undo_failed, "о пропавшем откате надо сказать отдельно"


def test_report_names_the_lost_undo_apart_from_failed_moves(tmp_path):
    """В отчёте у пропавшего отката свой заголовок, а не «Не переехали»."""
    downloads = tmp_path / "загрузки"
    touch(downloads / "клип.mp4")

    text = report(_sorted_with_blocked_log(downloads))

    assert "Не переехали" not in text
    assert "ошибок: 0" in text
    assert "отменить" in text.lower()


def test_written_undo_log_keeps_the_report_short(tmp_path):
    """Обычная сортировка про журнал не говорит ни слова."""
    downloads = tmp_path / "загрузки"
    touch(downloads / "клип.mp4")
    config = make_config(downloads)

    result = apply(build_plan(config), config, dry_run=False)

    assert result.undo_failed == ""
    assert report(result) == "Перемещено: 1, ошибок: 0"


# --- файл, занявший путь папки, в которую едут другие ---


def test_file_named_like_a_category_does_not_block_the_whole_run(tmp_path):
    """Файл `Медиа` без расширения ронял все перемещения в `Медиа`.

    План на такой папке правильный: файл `Медиа` уезжает в `Others/Misc`, а
    `клип.mp4` — в `Медиа/Videos`. Но выполнялся план в том порядке, в каком
    его построили, и `mkdir` для `Медиа/Videos` натыкался на файл, который
    ещё не успел уехать: `[WinError 183]` на каждом файле этой категории.
    Отчёт при этом честный, но говорит про папку назначения, а про виновника —
    файл, лежащий рядом, — не говорит ничего.

    Порядок теперь такой: сначала уезжает то, что занимает чужой путь.
    """
    downloads = tmp_path / "загрузки"
    touch(downloads / "клип.mp4")
    touch(downloads / "Медиа", "это файл, а не папка")
    config = make_config(downloads)

    result = apply(build_plan(config), config, dry_run=False)

    assert result.errors == []
    assert result.moved == 2
    assert (downloads / "Медиа" / "Videos" / "клип.mp4").is_file()
    assert (downloads / "Others" / "Misc" / "Медиа").is_file()


def test_ordinary_plan_keeps_its_order(tmp_path):
    """Перестановка касается только виновников — остальные идут как шли."""
    downloads = tmp_path / "загрузки"
    for name in ("а.mp4", "б.mp4", "в.mp4"):
        touch(downloads / name)
    config = make_config(downloads)
    moves = build_plan(config)

    result = apply(moves, config, dry_run=False)

    assert result.moved == 3
    log = json.loads(result.undo_log.read_text(encoding="utf-8"))
    assert [Path(e["src"]).name for e in log] == [mv.src.name for mv in moves]


# --- пустое слово в конфиге, собранном не из файла ---


def test_empty_keyword_written_by_hand_does_not_swallow_every_file():
    """Пустая строка входит в любое имя — категория забрала бы всё.

    Разбор правил такую запись выбрасывает с жалобой, но конфиг собирают и
    напрямую — из тестов, из CLI, — и там проверять некому. `match_type` от
    пустого расширения закрылся давно и по той же причине; поиск по словам,
    который решает не тип, а категорию, оставался открытым.
    """
    categories = {"Пусто": ["", "   "], "Медиа": ["клип"]}

    assert match_category("клип.mp4", "", categories) == "Медиа"
    assert match_category("отчёт.pdf", "", categories) is None
