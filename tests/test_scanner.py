from pathlib import Path

from sorter.scanner import scan
from sorter.config import Config


def make_config(root):
    """Конфиг, в котором `Documents` и `Учёба` — именно КАТЕГОРИИ.

    Сказать это приходится вслух: в корень загрузок обход заходит только по
    категориям, а не по всему, что названо в `managed_folders`. Список там
    плоский, категории и типы вперемешку, и по нему обход раньше растаскивал
    чужие папки, которым имя типа досталось само собой — `Models` у моделей
    Stable Diffusion, `Code` у распакованного репозитория.
    """
    return Config(
        downloads_path=str(root),
        categories={"Documents": [], "Учёба": []},
        type_map={},
        managed_folders=["Documents", "Учёба", "Others"],
        ignore=["*.crdownload", "desktop.ini"],
    )


def touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")


def test_includes_loose_file_in_root(tmp_path):
    touch(tmp_path / "report.pdf")
    found = scan(tmp_path, make_config(tmp_path))
    assert tmp_path / "report.pdf" in found


def test_includes_files_inside_managed_folder(tmp_path):
    touch(tmp_path / "Documents" / "a.docx")
    found = scan(tmp_path, make_config(tmp_path))
    assert tmp_path / "Documents" / "a.docx" in found


def test_deep_false_returns_only_root_files(tmp_path):
    touch(tmp_path / "report.pdf")
    touch(tmp_path / "Documents" / "a.docx")
    found = scan(tmp_path, make_config(tmp_path), deep=False)
    assert found == [tmp_path / "report.pdf"]


def test_deep_false_still_skips_ignored_root_files(tmp_path):
    touch(tmp_path / "keep.pdf")
    touch(tmp_path / "desktop.ini")
    found = scan(tmp_path, make_config(tmp_path), deep=False)
    assert found == [tmp_path / "keep.pdf"]


def test_excludes_files_inside_foreign_folder(tmp_path):
    touch(tmp_path / "games_pygame" / "main.py")
    found = scan(tmp_path, make_config(tmp_path))
    assert tmp_path / "games_pygame" / "main.py" not in found


def test_excludes_ignored_files(tmp_path):
    touch(tmp_path / "Не подтвержден 1.crdownload")
    touch(tmp_path / "desktop.ini")
    found = scan(tmp_path, make_config(tmp_path))
    assert found == []


def test_does_not_return_directories(tmp_path):
    (tmp_path / "Documents").mkdir()
    found = scan(tmp_path, make_config(tmp_path))
    assert found == []


def test_missing_folder_returns_empty(tmp_path):
    missing = tmp_path / "no_such_folder"
    assert scan(missing, make_config(tmp_path)) == []


def test_deep_scan_walks_own_subfolders(tmp_path):
    """Переразложение заходит внутрь папок программы — но только своих.

    Своя папка — та, чьё имя есть в managed_folders (категория или тип).
    Вложенная `2026` создана руками, поэтому её содержимое не трогаем.
    """
    cfg = make_config(tmp_path)
    cfg.managed_folders = cfg.managed_folders + ["Учёба"]
    touch(tmp_path / "Учёба" / "Documents" / "отчёт.pdf")
    touch(tmp_path / "Учёба" / "Documents" / "2026" / "личное.pdf")
    found = scan(tmp_path, cfg, deep=True)
    assert found == [tmp_path / "Учёба" / "Documents" / "отчёт.pdf"]


def test_deep_scan_never_enters_foreign_folder(tmp_path):
    """Чужая папка не разбирается даже при переразложении.

    Мир Minecraft или репозиторий — единица, а не набор файлов: разложив их
    содержимое по типам, программа уничтожила бы папку.
    """
    cfg = make_config(tmp_path)
    touch(tmp_path / "fluga" / "level.dat")
    touch(tmp_path / "claude-usage" / ".git" / "HEAD")
    touch(tmp_path / "Documents" / "note.txt")
    found = scan(tmp_path, cfg, deep=True)
    assert found == [tmp_path / "Documents" / "note.txt"]


def test_foreign_folders_untouched_without_deep(tmp_path):
    cfg = make_config(tmp_path)
    touch(tmp_path / "MalumMenu" / "winhttp.dll")
    assert scan(tmp_path, cfg, deep=False) == []
