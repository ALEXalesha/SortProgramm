from pathlib import Path

from sorter.planner import plan, plan_3d_folder, build_plan, Move
from sorter.config import Config


def make_config(root):
    return Config(
        downloads_path=str(root),
        categories={"Учёба": ["задач", "класс"]},
        type_map={"Documents": ["pdf", "txt"], "3D": ["obj", "3mf", "stl", "gcode"]},
        managed_folders=["Учёба", "Documents", "Others", "3D"],
        ignore=[],
        external_3d={
            "enabled": False,
            "path": str(root / "All_3d"),
            "extensions": ["3mf", "obj", "stl", "gcode"],
        },
    )


def touch(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- структура Категория/Тип/файл (без подпапки расширения) ---

def test_destination_is_category_type_only(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "задачи 9 класс.pdf"
    touch(f)
    moves = plan([f], cfg)
    assert moves == [Move(f, tmp_path / "Учёба" / "Documents" / "задачи 9 класс.pdf")]


def test_unmatched_goes_to_others(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "random.pdf"
    touch(f)
    moves = plan([f], cfg)
    assert moves[0].dst == tmp_path / "Others" / "Documents" / "random.pdf"


def test_name_collision_gets_suffix(tmp_path):
    cfg = make_config(tmp_path)
    a = tmp_path / "A" / "задача.pdf"
    b = tmp_path / "B" / "задача.pdf"
    touch(a)
    touch(b)
    moves = plan([a, b], cfg)
    dsts = [m.dst.name for m in moves]
    assert dsts == ["задача.pdf", "задача (1).pdf"]


def test_file_already_in_place_is_skipped(tmp_path):
    cfg = make_config(tmp_path)
    dst = tmp_path / "Учёба" / "Documents" / "задача.pdf"
    touch(dst)
    moves = plan([dst], cfg)
    assert moves == []


def test_content_decides_category_for_text(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "notes.txt"
    touch(f, "тут про задачу")
    moves = plan([f], cfg)
    assert moves[0].dst == tmp_path / "Учёба" / "Documents" / "notes.txt"


def test_file_without_extension(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "README"
    touch(f)
    moves = plan([f], cfg)
    assert moves[0].dst == tmp_path / "Others" / "Misc" / "README"


# --- галочка All_3d ---

def test_3d_model_stays_in_downloads_when_toggle_off(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "part.3mf"
    touch(f)
    moves = plan([f], cfg, send_3d_external=False)
    assert moves[0].dst == tmp_path / "Others" / "3D" / "part.3mf"


def test_3d_model_goes_to_external_ext_subfolder_when_toggle_on(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "part.obj"
    touch(f)
    moves = plan([f], cfg, send_3d_external=True)
    assert moves[0].dst == tmp_path / "All_3d" / "obj" / "part.obj"


def test_non_3d_file_unaffected_by_toggle(tmp_path):
    cfg = make_config(tmp_path)
    f = tmp_path / "задача.pdf"
    touch(f)
    moves = plan([f], cfg, send_3d_external=True)
    assert moves[0].dst == tmp_path / "Учёба" / "Documents" / "задача.pdf"


def test_external_3d_name_collision_gets_suffix(tmp_path):
    cfg = make_config(tmp_path)
    a = tmp_path / "A" / "model.stl"
    b = tmp_path / "B" / "model.stl"
    touch(a)
    touch(b)
    moves = plan([a, b], cfg, send_3d_external=True)
    dsts = sorted(m.dst.name for m in moves)
    assert dsts == ["model (1).stl", "model.stl"]
    assert all(m.dst.parent == tmp_path / "All_3d" / "stl" for m in moves)


# --- разбор внешней папки All_3d по расширениям ---

def test_3d_folder_sorts_every_file_by_extension(tmp_path):
    cfg = make_config(tmp_path)
    all3d = tmp_path / "All_3d"
    loose = [all3d / "part.gcode", all3d / "cube.3mf", all3d / "preview.png"]
    moves = plan_3d_folder(loose, cfg)
    by_name = {m.src.name: m.dst for m in moves}
    assert by_name["part.gcode"] == all3d / "gcode" / "part.gcode"
    assert by_name["cube.3mf"] == all3d / "3mf" / "cube.3mf"
    assert by_name["preview.png"] == all3d / "png" / "preview.png"


def test_3d_folder_leaves_extensionless_file(tmp_path):
    cfg = make_config(tmp_path)
    moves = plan_3d_folder([tmp_path / "All_3d" / "README"], cfg)
    assert moves == []


def test_3d_folder_file_already_in_subfolder_is_skipped(tmp_path):
    cfg = make_config(tmp_path)
    placed = tmp_path / "All_3d" / "gcode" / "part.gcode"
    moves = plan_3d_folder([placed], cfg)
    assert moves == []


# --- build_plan: загрузки (deep) + All_3d всегда ---

def test_build_plan_shallow_ignores_downloads_subfolders(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "задача.pdf")
    touch(tmp_path / "3D" / "old.3mf")  # уже в подпапке загрузок
    moves = build_plan(cfg, deep=False)
    srcs = {m.src for m in moves}
    assert tmp_path / "задача.pdf" in srcs
    assert tmp_path / "3D" / "old.3mf" not in srcs


def test_build_plan_deep_includes_downloads_subfolders(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "3D" / "old.obj")
    moves = build_plan(cfg, deep=True)
    srcs = {m.src for m in moves}
    assert tmp_path / "3D" / "old.obj" in srcs


def test_build_plan_always_sorts_all_3d_folder(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "All_3d" / "part.gcode")
    moves = build_plan(cfg, deep=False)  # даже без изменений в загрузках
    dsts = {m.dst for m in moves}
    assert tmp_path / "All_3d" / "gcode" / "part.gcode" in dsts


def test_build_plan_no_collision_between_downloads_and_all_3d(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "part.gcode")             # 3D-файл из загрузок → All_3d/gcode
    touch(tmp_path / "All_3d" / "part.gcode")  # одноимённый уже в All_3d
    moves = build_plan(cfg, send_3d_external=True, deep=False)
    gcode_dsts = sorted(m.dst.name for m in moves
                        if m.dst.parent == tmp_path / "All_3d" / "gcode")
    assert gcode_dsts == ["part (1).gcode", "part.gcode"]
