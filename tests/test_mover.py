from pathlib import Path

from sorter.mover import apply, undo, Result
from sorter.planner import Move
from sorter.config import Config


def make_config(root):
    return Config(
        downloads_path=str(root),
        categories={},
        type_map={},
        managed_folders=["Documents", "Учёба", "Others"],
        ignore=[],
    )


def touch(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_dry_run_moves_nothing(tmp_path):
    cfg = make_config(tmp_path)
    src = tmp_path / "a.pdf"
    touch(src)
    dst = tmp_path / "Others" / "Documents" / "pdf" / "a.pdf"
    result = apply([Move(src, dst)], cfg, dry_run=True)
    assert src.exists()
    assert not dst.exists()
    assert result.moved == 0
    assert result.planned == 1


def test_apply_moves_file_and_creates_dirs(tmp_path):
    cfg = make_config(tmp_path)
    src = tmp_path / "a.pdf"
    touch(src, "hello")
    dst = tmp_path / "Others" / "Documents" / "pdf" / "a.pdf"
    result = apply([Move(src, dst)], cfg, dry_run=False)
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "hello"
    assert result.moved == 1


def test_missing_source_recorded_as_error(tmp_path):
    cfg = make_config(tmp_path)
    src = tmp_path / "ghost.pdf"
    dst = tmp_path / "Others" / "Documents" / "pdf" / "ghost.pdf"
    result = apply([Move(src, dst)], cfg, dry_run=False)
    assert result.moved == 0
    assert len(result.errors) == 1


def test_empty_managed_folder_removed_after_apply(tmp_path):
    cfg = make_config(tmp_path)
    src = tmp_path / "Documents" / "a.pdf"
    touch(src)
    dst = tmp_path / "Учёба" / "Documents" / "pdf" / "a.pdf"
    apply([Move(src, dst)], cfg, dry_run=False)
    assert not (tmp_path / "Documents").exists()


def test_undo_restores_files(tmp_path):
    cfg = make_config(tmp_path)
    src = tmp_path / "a.pdf"
    touch(src, "data")
    dst = tmp_path / "Others" / "Documents" / "pdf" / "a.pdf"
    result = apply([Move(src, dst)], cfg, dry_run=False)
    undo(result.undo_log)
    assert src.exists()
    assert not dst.exists()
