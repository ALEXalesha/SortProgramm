from pathlib import Path

from sorter.planner import plan, Move
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
