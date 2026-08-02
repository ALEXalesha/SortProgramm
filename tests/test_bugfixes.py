"""Проверки на найденные баги. Каждый тест — воспроизведение конкретной поломки."""
import json
from pathlib import Path

from sorter.config import Config
from sorter.classifier import explain_category, match_category, match_type
from sorter.history import list_operations
from sorter.mover import apply, undo
from sorter.planner import build_plan, Move
from sorter.scanner import scan


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
