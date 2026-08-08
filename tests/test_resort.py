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


# --- переразложение внешней папки 3D ---


def make_3d_config(root: Path, external: Path) -> Config:
    cfg = make_config(root)
    cfg.external_3d = {"enabled": True, "path": str(external),
                       "extensions": ["stl", "obj", "gcode"]}
    return cfg


def test_resort_fixes_a_model_in_the_wrong_extension_folder(tmp_path):
    """Модель, уехавшая не в ту подпапку All_3d, лежала там навсегда.

    Корень All_3d разбирался при каждой уборке, а внутрь программа не
    заглядывала ни разу: «Переразложить старое» до папок расширений не
    доходило. Попасть туда просто — расширение убрали из настройки, файл
    переименовали, папку набили руками из проводника.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    external = tmp_path / "All_3d"
    touch(external / "gcode" / "деталь.stl")
    touch(external / "gcode" / "печать.gcode")

    cfg = make_3d_config(downloads, external)

    assert build_plan(cfg, send_3d_external=True, deep=False) == []

    moves = build_plan(cfg, send_3d_external=True, deep=True)
    assert [(mv.src.name, mv.dst.parent.name) for mv in moves] == [
        ("деталь.stl", "stl")]


def test_resort_leaves_models_that_already_lie_right(tmp_path):
    """Прибранная папка 3D не должна давать ни одной строки плана."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    external = tmp_path / "All_3d"
    touch(external / "stl" / "деталь.stl")
    touch(external / "gcode" / "печать.gcode")

    cfg = make_3d_config(downloads, external)

    assert build_plan(cfg, send_3d_external=True, deep=True) == []


def test_resort_does_not_enter_a_folder_made_by_hand(tmp_path):
    """`All_3d/корпус` человек разложил сам — разбирать его нельзя.

    Имени мало: `корпус`, `проекты`, `запчасти` выглядят так же, как папка
    расширения. Своей папка считается, только если внутри лежит файл ровно с
    тем расширением, которым она названа.
    """
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    external = tmp_path / "All_3d"
    touch(external / "корпус" / "деталь.stl")
    touch(external / "корпус" / "чертёж.obj")

    cfg = make_3d_config(downloads, external)

    assert build_plan(cfg, send_3d_external=True, deep=True) == []


def test_resort_does_not_go_deeper_than_the_extension_folder(tmp_path):
    """Внутри `All_3d/stl/корпус` начинается уже чужая раскладка."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    external = tmp_path / "All_3d"
    touch(external / "stl" / "деталь.stl")
    touch(external / "stl" / "корпус" / "крышка.obj")

    cfg = make_3d_config(downloads, external)

    assert build_plan(cfg, send_3d_external=True, deep=True) == []


def test_root_of_the_3d_folder_is_still_sorted_without_resort(tmp_path):
    """Разбор корня All_3d от галочки не зависел и зависеть не должен."""
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    external = tmp_path / "All_3d"
    touch(external / "деталь.stl")

    cfg = make_3d_config(downloads, external)

    moves = build_plan(cfg, send_3d_external=True, deep=False)
    assert [mv.dst.parent.name for mv in moves] == ["stl"]
