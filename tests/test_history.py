import json
from pathlib import Path

from sorter.config import Config
from sorter.mover import apply
from sorter.planner import Move
from sorter import history


def make_config(root):
    return Config(
        downloads_path=str(root),
        categories={},
        type_map={},
        managed_folders=["Documents", "Others"],
        ignore=[],
    )


def touch(path: Path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_log(root: Path, name: str, entries):
    log_dir = root / ".sorter"
    log_dir.mkdir(exist_ok=True)
    (log_dir / name).write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def test_no_sorter_dir_returns_empty(tmp_path):
    assert history.list_operations(tmp_path) == []


def test_lists_operation_with_count_and_time(tmp_path):
    _write_log(tmp_path, "undo_20260102_030405.json",
               [{"src": "a", "dst": "b"}, {"src": "c", "dst": "d"}])
    ops = history.list_operations(tmp_path)
    assert len(ops) == 1
    assert ops[0].count == 2
    assert ops[0].when.year == 2026 and ops[0].when.month == 1
    assert ops[0].when.hour == 3 and ops[0].when.second == 5


def test_newest_first(tmp_path):
    _write_log(tmp_path, "undo_20260101_000000.json", [])
    _write_log(tmp_path, "undo_20260601_120000.json", [])
    ops = history.list_operations(tmp_path)
    assert [op.when.month for op in ops] == [6, 1]


def test_ignores_broken_and_foreign_files(tmp_path):
    log_dir = tmp_path / ".sorter"
    log_dir.mkdir()
    (log_dir / "undo_20260101_000000.json").write_text("{ broken", encoding="utf-8")
    (log_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (log_dir / "undo_bad_name.json").write_text("[]", encoding="utf-8")
    assert history.list_operations(tmp_path) == []


def test_log_with_strange_entries_does_not_break_history(tmp_path):
    """Журнал пишет программа, но лежит он в папке пользователя.

    Правка руками, обрыв записи, чужой файл под таким же именем — и в списке
    оказывается что угодно вместо пар «откуда/куда». Окно истории падало на
    показе такой записи, а откат — на попытке её прочитать.
    """
    _write_log(tmp_path, "undo_20260101_000000.json",
               ["строка", 5, {"src": "a"}, {"src": "a", "dst": "b"}])

    ops = history.list_operations(tmp_path)

    assert len(ops) == 1
    assert ops[0].entries == [{"src": "a", "dst": "b"}], "мусор надо отсеять"
    assert history.undo_operation(ops[0]) == []


def test_undo_keeps_log_when_file_did_not_return(tmp_path):
    """Не вернулось — значит откатить ещё предстоит, и журнал нужен.

    Файл держит другая программа, исходной папки больше нет, диск снят —
    откат сообщает об этом оговоркой. Журнал при этом удалялся всё равно, и
    вместе с ним пропадала сама возможность повторить попытку: запись из
    истории исчезала, а файл оставался лежать не там, где был.
    """
    cfg = make_config(tmp_path)
    src = tmp_path / "занято" / "a.pdf"
    touch(src, "data")
    apply([Move(src, tmp_path / "Others" / "a.pdf")], cfg, dry_run=False)
    (tmp_path / "занято").rmdir()
    touch(tmp_path / "занято")  # под файлом папку не создать — откат не пройдёт

    op = history.list_operations(tmp_path)[0]
    notes = history.undo_operation(op)

    assert notes, "неудачу надо показать"
    assert (tmp_path / "Others" / "a.pdf").exists(), "файл никуда не вернулся"
    assert len(history.list_operations(tmp_path)) == 1, "журнал удалён — откат не повторить"


def test_undo_operation_restores_and_removes_log(tmp_path):
    cfg = make_config(tmp_path)
    src = tmp_path / "a.pdf"
    touch(src, "data")
    dst = tmp_path / "Others" / "Documents" / "pdf" / "a.pdf"
    apply([Move(src, dst)], cfg, dry_run=False)

    ops = history.list_operations(tmp_path)
    assert len(ops) == 1

    history.undo_operation(ops[0])
    assert src.exists()
    assert not dst.exists()
    assert history.list_operations(tmp_path) == []
