"""Правки правил из окна: наложение, операции, файл, связка с Config."""
import json
import os

import pytest

from sorter import user_rules as ur
from sorter.classifier import BY_RULE, explain_category
from sorter.config import Config


def base():
    return ur.Base(
        categories={"Учёба": ["задач", "экзамен"], "Медиа": ["клип"],
                    "Игры": ["minecraft"]},
        patterns={"Медиа": [r"^\d{4}\.mp4$"], "Игры": [r"^mc_"]},
        overrides={"старый.zip": "Игры", "лекция.pdf": "Учёба"},
        managed_folders=["Учёба", "Медиа", "Игры", "Others", "Misc"],
        fallback_category="Others")


def merged(edits, notices=None):
    return ur.merge(base(), ur.norm(edits), notices)


def test_no_edits_is_the_base_itself():
    m = merged(ur.empty())
    b = base()
    assert (m.categories, m.patterns, m.overrides, m.managed_folders) == (
        b.categories, b.patterns, b.overrides, b.managed_folders)


def test_upstream_word_moves_to_the_category_the_person_chose():
    """Правило 4: слово, добавленное человеком, принадлежит его категории."""
    b = base()
    b.categories["Учёба"].append("клип-урок")
    edits = ur.norm({"categories": {"Медиа": {"add": ["клип-урок"], "remove": []}}})
    m = ur.merge(b, edits)
    assert "клип-урок" in m.categories["Медиа"]
    assert "клип-урок" not in m.categories["Учёба"]


def test_edit_for_a_vanished_category_with_words_recreates_it():
    """Правило 5: обновление убрало категорию, а слова человека остались."""
    edits = ur.norm({"categories": {"Кино": {"add": ["фильм"], "remove": []}}})
    assert merged(edits).categories["Кино"] == ["фильм"]


def test_edit_for_a_vanished_category_without_words_is_dropped():
    edits = ur.norm({"categories": {"Кино": {"add": [], "remove": ["x"]}}})
    assert "Кино" not in merged(edits).categories


def test_created_category_that_collides_with_a_new_upstream_one_merges():
    """Правило 6: регистр не важен, как и у Windows."""
    edits = ur.norm({"categories": {"медиа": {"add": ["ролик"], "remove": [],
                                              "created": True}}})
    m = merged(edits)
    assert [n for n in m.categories if n.lower() == "медиа"] == ["Медиа"]
    assert m.categories["Медиа"] == ["клип", "ролик"]


def test_file_rule_to_a_vanished_category_is_skipped_with_a_notice():
    notices = []
    m = merged({"files": {"a.pdf": "Кино"}}, notices)
    assert "a.pdf" not in m.overrides
    assert any("a.pdf" in n for n in notices)


def test_file_rule_replaces_the_overrides_json_entry_for_the_same_name():
    m = merged({"files": {"СТАРЫЙ.zip": "Медиа"}})
    same = [k for k in m.overrides
            if os.path.normcase(k) == os.path.normcase("СТАРЫЙ.zip")]
    assert same == ["СТАРЫЙ.zip"] and m.overrides["СТАРЫЙ.zip"] == "Медиа"


# --- файл ---

def test_missing_file_means_no_edits(tmp_path):
    problems = []
    assert ur.read(tmp_path / ur.USER_RULES_FILENAME, problems) == (ur.empty(), False)
    assert problems == []


@pytest.mark.parametrize("raw", [b"{", b"[1, 2]", b"\xff\xfe\x00", b""])
def test_unreadable_file_is_reported_and_locked(tmp_path, raw):
    path = tmp_path / ur.USER_RULES_FILENAME
    path.write_bytes(raw)
    problems = []
    edits, unreadable = ur.read(path, problems)
    assert edits == ur.empty() and unreadable
    assert problems and ur.USER_RULES_FILENAME in problems[0]


def test_file_from_a_newer_program_is_not_parsed(tmp_path):
    path = tmp_path / ur.USER_RULES_FILENAME
    path.write_text(json.dumps({"version": 2, "files": {"a": "Учёба"}}), encoding="utf-8")
    problems = []
    assert ur.read(path, problems) == (ur.empty(), True)
    assert "2" in problems[0]


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_file_saved_by_notepad_reads(tmp_path, encoding):
    path = tmp_path / ur.USER_RULES_FILENAME
    path.write_text(json.dumps({"files": {"a.pdf": "Учёба"}}, ensure_ascii=False),
                    encoding=encoding)
    assert ur.read(path, [])[0]["files"] == {"a.pdf": "Учёба"}


def test_bad_entries_are_skipped_one_by_one(tmp_path):
    path = tmp_path / ur.USER_RULES_FILENAME
    path.write_text(json.dumps({
        "categories": {"Учёба": {"add": ["курсовая", 5, "x"], "remove": []},
                       "C:/Windows": {"add": ["y"]}},
        "renamed": {"Медиа": "Вид?ео"},
        "removed": "Игры",
        "files": {"a.pdf": "Учёба", "b.pdf": 3},
    }, ensure_ascii=False), encoding="utf-8")
    problems = []
    edits, unreadable = ur.read(path, problems)
    assert not unreadable
    assert edits["categories"] == {"Учёба": {"add": ["курсовая"], "remove": []}}
    assert edits["renamed"] == {} and edits["removed"] == []
    assert edits["files"] == {"a.pdf": "Учёба"}
    assert len(problems) == 1 and "пропущены" in problems[0]


def test_write_then_read_is_identity(tmp_path):
    edits = ur.norm({"categories": {"Рецепты": {"add": ["рецепт"], "remove": [],
                                                "created": True}},
                     "renamed": {"Медиа": "Видео"}, "removed": ["Игры"],
                     "keep_folders": ["Медиа", "Игры"], "files": {"a.pdf": "Учёба"}})
    path = tmp_path / ur.USER_RULES_FILENAME
    ur.write(path, edits)
    assert ur.read(path, []) == (edits, False)
    assert path.read_bytes().endswith(b"\n")


def test_failed_write_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch):
    path = tmp_path / ur.USER_RULES_FILENAME
    ur.write(path, ur.norm({"files": {"a.pdf": "Учёба"}}))
    before = path.read_bytes()

    def broken(*a, **k):
        raise OSError("диск отвалился")
    monkeypatch.setattr(ur.os, "replace", broken)
    with pytest.raises(OSError):
        ur.write(path, ur.norm({"files": {"b.pdf": "Медиа"}}))
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [ur.USER_RULES_FILENAME]
