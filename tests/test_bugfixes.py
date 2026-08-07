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
    assert result.errors, "потерю журнала отмены надо показать, а не проглотить"


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
