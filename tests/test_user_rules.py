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


# --- операции ---

def op(fn, edits, *args):
    return fn(ur.norm(edits), base(), *args)


def test_new_category_goes_last_and_becomes_own_folder():
    edits = op(ur.add_category, {}, "Рецепты")
    m = merged(edits)
    assert list(m.categories)[-1] == "Рецепты" and m.categories["Рецепты"] == []
    assert "Рецепты" in m.managed_folders


@pytest.mark.parametrize("name", ["", "   ", "C:/Windows", "a?b", "Учёба ",
                                  " Учёба2", "Others", "others", "учёба", "..", "a\x01"])
def test_bad_category_names_are_refused(name):
    with pytest.raises(ValueError):
        op(ur.add_category, {}, name)


def test_adding_a_removed_program_category_brings_it_back_in_place():
    edits = op(ur.remove_category, {}, "Медиа")
    edits = op(ur.add_category, edits, "медиа")
    m = merged(edits)
    assert list(m.categories) == ["Учёба", "Медиа", "Игры"]
    assert m.categories["Медиа"] == ["клип"] and m.patterns["Медиа"]


def test_rename_keeps_priority_and_drags_references_along():
    m = merged(op(ur.rename_category, {}, "Игры", "Games"))
    assert list(m.categories) == ["Учёба", "Медиа", "Games"]
    assert m.patterns["Games"] == [r"^mc_"] and "Игры" not in m.patterns
    assert m.overrides["старый.zip"] == "Games"
    assert {"Игры", "Games"} <= set(m.managed_folders)


def test_rename_chain_is_one_entry_and_rename_back_removes_it():
    edits = op(ur.rename_category, {}, "Игры", "Games")
    edits = op(ur.rename_category, edits, "Games", "Забавы")
    assert edits["renamed"] == {"Игры": "Забавы"}
    edits = op(ur.rename_category, edits, "Забавы", "Игры")
    assert edits["renamed"] == {}
    assert merged(edits).categories == base().categories


def test_rename_of_a_created_category_keeps_its_words():
    edits = op(ur.add_category, {}, "Рецепты")
    edits = op(ur.add_word, edits, "Рецепты", "рецепт")
    edits = op(ur.rename_category, edits, "Рецепты", "Кухня")
    assert merged(edits).categories["Кухня"] == ["рецепт"]
    assert "Рецепты" in merged(edits).managed_folders


@pytest.mark.parametrize("old,new", [("Кино", "Фильмы"), ("Игры", "игры"),
                                     ("Игры", "Медиа"), ("Игры", "a|b")])
def test_bad_renames_are_refused(old, new):
    with pytest.raises(ValueError):
        op(ur.rename_category, {}, old, new)


def test_rename_retargets_file_rules():
    edits = op(ur.set_file, {}, "a.pdf", "Игры")
    edits = op(ur.rename_category, edits, "Игры", "Games")
    assert edits["files"] == {"a.pdf": "Games"}


def test_remove_drops_references_and_says_so_but_keeps_the_folder():
    notices = []
    m = merged(op(ur.remove_category, {}, "Игры"), notices)
    assert "Игры" not in m.categories and "Игры" not in m.patterns
    assert "старый.zip" not in m.overrides
    assert "Игры" in m.managed_folders
    assert any("«Игры»" in n and "(1)" in n for n in notices)


def test_remove_of_a_created_category_keeps_its_folder_own():
    edits = op(ur.add_category, {}, "Рецепты")
    edits = op(ur.remove_category, edits, "Рецепты")
    assert "Рецепты" not in merged(edits).categories
    assert "Рецепты" in merged(edits).managed_folders


def test_the_last_category_cannot_be_removed():
    """Нашли свойства: без единой категории раскладывать не по чему, и следующий
    запуск встречает жалобой «все файлы уедут в Others». Из окна раскладку
    сломать нельзя - это договор."""
    edits = op(ur.remove_category, {}, "Учёба")
    edits = op(ur.remove_category, edits, "Медиа")
    with pytest.raises(ValueError, match="последн"):
        op(ur.remove_category, edits, "Игры")


def test_remove_drops_file_rules_into_it():
    edits = op(ur.set_file, {}, "a.pdf", "Игры")
    assert op(ur.remove_category, edits, "Игры")["files"] == {}


def test_word_added_and_removed_leaves_nothing_behind():
    edits = op(ur.add_word, {}, "Учёба", "курсовая")
    assert merged(edits).categories["Учёба"] == ["задач", "экзамен", "курсовая"]
    edits = op(ur.remove_word, edits, "Учёба", "КУРСОВАЯ")
    assert edits == ur.empty()


def test_program_word_removed_and_added_back_leaves_nothing_behind():
    edits = op(ur.remove_word, {}, "Учёба", "экзамен")
    assert merged(edits).categories["Учёба"] == ["задач"]
    assert op(ur.add_word, edits, "Учёба", "экзамен") == ur.empty()


@pytest.mark.parametrize("category,word,said", [
    ("Учёба", "x", "короче"), ("Учёба", "  a ", "короче"),
    ("Учёба", "ЗАДАЧ", "уже есть в «Учёба»"), ("Медиа", "экзамен", "«Учёба»"),
    ("Кино", "фильм", "нет"),
])
def test_bad_words_are_refused_with_a_reason(category, word, said):
    with pytest.raises(ValueError, match=said):
        op(ur.add_word, {}, category, word)


def test_word_with_spaces_inside_is_kept_as_typed():
    edits = op(ur.add_word, {}, "Медиа", " фон ")
    assert merged(edits).categories["Медиа"][-1] == " фон "


def test_removing_a_missing_word_is_refused():
    with pytest.raises(ValueError):
        op(ur.remove_word, {}, "Учёба", "курсовая")


def test_file_rule_beats_patterns_and_words():
    edits = op(ur.set_file, {}, "0001.mp4", "Игры")
    m = merged(edits)
    cfg = Config(downloads_path="x", categories=m.categories, patterns=m.patterns,
                 overrides=m.overrides)
    assert explain_category("0001.mp4", "", cfg) == ("Игры", BY_RULE)


@pytest.mark.parametrize("category", ["Others", "Кино"])
def test_bad_file_rules_are_refused(category):
    with pytest.raises(ValueError):
        op(ur.set_file, {}, "a.pdf", category)


def test_clear_file_removes_the_rule_and_refuses_twice():
    edits = op(ur.set_file, {}, "a.pdf", "Игры")
    assert ur.file_rule(edits, "A.PDF" if os.name == "nt" else "a.pdf") == "Игры"
    edits = op(ur.clear_file, edits, "a.pdf")
    assert edits["files"] == {}
    with pytest.raises(ValueError):
        op(ur.clear_file, edits, "a.pdf")


def test_refused_operation_changes_nothing():
    edits = op(ur.add_word, {}, "Учёба", "курсовая")
    before = json.dumps(edits, sort_keys=True)
    with pytest.raises(ValueError):
        ur.add_word(edits, base(), "Учёба", "курсовая")
    assert json.dumps(edits, sort_keys=True) == before


def test_moved_by_counts_files_the_word_would_take():
    b = base()
    cfg = Config(downloads_path="x", categories=b.categories, patterns=b.patterns,
                 overrides=b.overrides, base=b, user_rules=ur.empty())
    trial = op(ur.add_word, {}, "Медиа", "видео")
    after = ur.apply(cfg, trial)
    names = ["видео-урок.mp4", "видео.avi", "задачи.pdf", "0001.mp4"]
    assert ur.moved_by(names, cfg, after) == {"Others": 2}


# --- Config.load ---

RULES = {
    "categories": {"Медиа": ["клип"], "Документы": ["отчёт"]},
    "patterns": {"Медиа": ["^[0-9]{4}[.]mp4$"]},
    "type_map": {"Videos": ["mp4"], "Documents": ["pdf"]},
    "managed_folders": ["Медиа", "Документы", "Videos", "Documents", "Others", "Misc"],
    "fallback_category": "Others", "fallback_type": "Misc",
}


def setup(tmp_path, edits=None):
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps(RULES, ensure_ascii=False),
                                         encoding="utf-8")
    if edits is not None:
        ur.write(ur.path_for(tmp_path / "config.json"), ur.norm(edits))
    return tmp_path / "config.json"


def test_load_applies_user_rules_without_new_problems(tmp_path):
    path = setup(tmp_path, {"categories": {"Рецепты": {"add": ["рецепт"], "remove": [],
                                                       "created": True}},
                            "renamed": {"Медиа": "Видео"}})
    cfg = Config.load(path)
    assert list(cfg.categories) == ["Видео", "Документы", "Рецепты"]
    assert cfg.patterns == {"Видео": RULES["patterns"]["Медиа"]}
    assert cfg.problems == []
    assert cfg.base.categories == RULES["categories"]
    assert cfg.user_rules["renamed"] == {"Медиа": "Видео"}


def test_load_without_user_rules_ignores_the_file(tmp_path):
    path = setup(tmp_path, {"renamed": {"Медиа": "Видео"}})
    assert list(Config.load(path, user=False).categories) == ["Медиа", "Документы"]


def test_unreadable_user_rules_lock_writing(tmp_path):
    path = setup(tmp_path)
    ur.path_for(path).write_text("{", encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.user_rules_unreadable
    assert any(ur.USER_RULES_FILENAME in p for p in cfg.problems)


def test_file_rule_from_the_window_is_not_called_an_argument_with_a_pattern(tmp_path):
    """Правило из окна над шаблоном - решение человека, а не спор с программой."""
    path = setup(tmp_path, {"files": {"0001.mp4": "Документы"}})
    cfg = Config.load(path)
    assert cfg.notices == []
    assert explain_category("0001.mp4", "", cfg) == ("Документы", BY_RULE)
