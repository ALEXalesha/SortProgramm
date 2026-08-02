"""Переразложение: новые правила применяются к уже разложенному.

Программа двигает только то, что создала сама. Чужие папки — распакованные
архивы, миры игр, репозитории — не трогаются ни в одном режиме.
"""
from pathlib import Path

from sorter.config import Config
from sorter.planner import build_plan


def make_config(root: Path) -> Config:
    return Config(
        downloads_path=str(root),
        categories={"Медиа": ["клип"], "Программы": ["setup"]},
        patterns={"Скриншоты": [r"^screenshot"]},
        type_map={"Images": ["png"], "Videos": ["mp4"], "Installers": ["exe"]},
        managed_folders=["Others", "Скриншоты", "Медиа", "Программы", "Images", "Videos"],
        ignore=["desktop.ini"],
        fallback_category="Others",
        fallback_type="Misc",
    )


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def test_resort_refiles_old_download_by_new_pattern(tmp_path):
    """Файл лежит в Others с прошлой сортировки; новый шаблон забирает его."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "Others" / "Images" / "screenshot-1.png")
    moves = build_plan(cfg, deep=True)
    assert [m.dst for m in moves] == [tmp_path / "Скриншоты" / "Images" / "screenshot-1.png"]


def test_shallow_run_leaves_sorted_files_alone(tmp_path):
    """Обычная уборка не ворошит разложенное — иначе каждый запуск всё двигает."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "Others" / "Images" / "screenshot-1.png")
    assert build_plan(cfg, deep=False) == []


def test_resort_does_not_touch_foreign_folder(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "fluga" / "level.dat")
    touch(tmp_path / "fluga" / "screenshot-inside.png")
    assert build_plan(cfg, deep=True) == []


def test_foreign_folder_itself_is_never_moved(tmp_path):
    """Ни один план не должен содержать саму чужую папку как источник."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "MalumMenu_v3" / "winhttp.dll")
    touch(tmp_path / "клип.mp4")
    moves = build_plan(cfg, deep=True)
    sources = [m.src for m in moves]
    assert tmp_path / "MalumMenu_v3" not in sources
    assert sources == [tmp_path / "клип.mp4"]


def test_move_carries_reason(tmp_path):
    """В предпросмотре важно «почему», а не только «куда»."""
    cfg = make_config(tmp_path)
    touch(tmp_path / "screenshot-2.png")
    touch(tmp_path / "клип.mp4")
    notes = {m.src.name: m.note for m in build_plan(cfg, deep=False)}
    assert notes["screenshot-2.png"] == "шаблон"
    assert notes["клип.mp4"] == "слово"


def test_override_reason_is_reported(tmp_path):
    cfg = make_config(tmp_path)
    cfg.overrides = {"нечто.bin": "Медиа"}
    touch(tmp_path / "нечто.bin")
    moves = build_plan(cfg, deep=False)
    assert moves[0].note == "правило"
