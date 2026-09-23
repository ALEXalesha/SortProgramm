# Правила из окна: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Цель:** человек добавляет, переименовывает и удаляет категории, правит их слова
и задаёт правило для файла прямо из окна; правки живут в `my_rules.json` поверх
`rules.json` и переживают обновления.

**Архитектура:** новый модуль `sorter/user_rules.py` без интерфейса: чистое
наложение правок (`merge`), операции над правками (каждая либо возвращает новые
правки, либо бросает `ValueError`), чтение и атомарная запись файла.
`Config.load` накладывает правки. Окно получает диалог `sorter/ui_rules.py`,
кнопку «⚙ Правила» и контекстное меню таблицы плана.

**Стек:** Python 3.13, PyQt6, pytest, hypothesis 6.

Спецификация: `docs/superpowers/specs/2026-09-23-rules-editor-design.md`.

Запуск тестов везде: `python -m pytest -q` из корня проекта (566 тестов до начала).

---

## Файлы

| Файл | Что делает |
| --- | --- |
| `sorter/user_rules.py` (новый) | формат `my_rules.json`, наложение, операции, чтение/запись, предпросмотр слова |
| `sorter/config.py` | `Config.load(..., user=True)` накладывает правки; поля `base`, `user_rules`, `user_rules_unreadable` |
| `sorter/ui_rules.py` (новый) | диалог «Правила раскладки» |
| `sorter/ui_qt.py` | кнопка «⚙ Правила», контекстное меню таблицы, запись правок и перестройка плана |
| `tests/test_user_rules.py` (новый) | юнит-тесты модуля и связки с `Config.load` |
| `tests/test_user_rules_props.py` (новый) | свойства на hypothesis |
| `tests/test_ui_rules.py` (новый) | диалог и меню в окне (offscreen) |
| `tests/test_real_config.py`, `tests/test_real_layout_snapshot.py` | `Config.load(..., user=False)` |
| `.gitignore`, `README.md` | `my_rules.json` не в репозиторий; раздел о правилах из окна |

---

### Задача 1. Наложение правок, чтение и запись

**Файлы:** создать `sorter/user_rules.py`, `tests/test_user_rules.py`.

- [ ] **Шаг 1. Тесты наложения и файла** — `tests/test_user_rules.py`:

```python
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
    same = [k for k in m.overrides if os.path.normcase(k) == os.path.normcase("старый.zip")]
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
```

- [ ] **Шаг 2. Убедиться, что тесты падают**

Запуск: `python -m pytest tests/test_user_rules.py -q`
Ожидание: ошибка импорта `sorter.user_rules`.

- [ ] **Шаг 3. Модуль** — `sorter/user_rules.py` (операции добавит задача 2):

```python
"""Правки правил из окна: `my_rules.json` поверх `rules.json`.

`rules.json` установщик кладёт заново при каждом обновлении — так до уже
установленных копий доезжают новые категории. Писать туда правки из окна
значит потерять их при первой переустановке, а запретить установщику
перезаписывать файл — значит больше никогда не привезти людям новые правила.
Поэтому правки человека лежат отдельно и накладываются при каждой загрузке.

Файл хранит отличия, а не копию: полная копия категорий после первой же правки
молча отрезала бы все будущие обновления.

Формат и правила наложения разобраны в
`docs/superpowers/specs/2026-09-23-rules-editor-design.md`.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from .config import folder_key, folder_name_problem, name_key
from .util import read_text

USER_RULES_FILENAME = "my_rules.json"
VERSION = 1
# Слово короче двух знаков подстрокой входит почти в любое имя.
MIN_WORD = 2

_SEPARATORS = re.compile(r"[\\/]")
_UNREADABLE_TAIL = ("Правки из окна не применены; пока файл не починят или не "
                    "удалят, окно не будет в него писать — иначе они "
                    "заменились бы пустыми.")


@dataclass
class Base:
    """Правила программы до правок человека."""
    categories: dict[str, list[str]]
    patterns: dict[str, list[str]]
    overrides: dict[str, str]
    managed_folders: list[str]
    fallback_category: str = "Others"


@dataclass
class Merged:
    """Правила после наложения правок."""
    categories: dict[str, list[str]]
    patterns: dict[str, list[str]]
    overrides: dict[str, str]
    managed_folders: list[str]


def empty() -> dict:
    return {"version": VERSION, "categories": {}, "renamed": {}, "removed": [],
            "keep_folders": [], "files": {}}


def norm(edits: dict | None) -> dict:
    """Правки со всеми разделами на месте. Исходник не трогается."""
    full = empty()
    for key, value in (edits or {}).items():
        if key in full:
            full[key] = copy.deepcopy(value)
    full["version"] = VERSION
    return full


def path_for(config_path) -> Path:
    return Path(config_path).with_name(USER_RULES_FILENAME)


def _find(names, name: str) -> str | None:
    """Имя из `names`, совпадающее с `name` по правилам ФС (`folder_key`)."""
    key = folder_key(name)
    for existing in names:
        if folder_key(existing) == key:
            return existing
    return None


def _has_word(words, word: str) -> bool:
    low = word.lower()
    return any(w.lower() == low for w in words)


def _grow(managed: list[str], name: str) -> None:
    """Дописать в `managed_folders` все части имени, которых там нет.

    Вложенная категория (`Учёба/2026`) — это две папки, и обход спускается по
    ним по очереди (`config._check_managed`), поэтому нужны обе.
    """
    for part in (p for p in _SEPARATORS.split(name) if p):
        if _find(managed, part) is None:
            managed.append(part)


def _shown(names: list[str]) -> str:
    head = ", ".join(f"«{n}»" for n in names[:3])
    return head + (f" и ещё {len(names) - 3}" if len(names) > 3 else "")


def merge(base: Base, edits: dict, notices: list[str] | None = None) -> Merged:
    """Правила программы с наложенными правками. Без ввода-вывода.

    Порядок категорий — приоритет (`classifier.find_category`), поэтому
    категории программы остаются на своих местах, а созданные встают в конец.
    """
    notices = [] if notices is None else notices
    renamed = {folder_key(old): new for old, new in edits["renamed"].items()}
    removed = {folder_key(name) for name in edits["removed"]}
    categories: dict[str, list[str]] = {}

    def put(name: str, words) -> str:
        existing = _find(categories, name)
        if existing is None:
            categories[name] = []
            existing = name
        for word in words:
            if not _has_word(categories[existing], word):
                categories[existing].append(word)
        return existing

    # Переименования и удаления. `moved` — куда уехала категория программы:
    # по нему за ней идут шаблоны и ручные правила.
    moved: dict[str, str] = {}
    for name, words in base.categories.items():
        key = folder_key(name)
        if key not in removed:
            moved[key] = put(renamed.get(key, name), words)

    # Правки слов и созданные категории.
    owned: dict[str, str] = {}
    for name, edit in edits["categories"].items():
        target = _find(categories, name)
        if target is None:
            # Категории нет — её создал человек или её убрало обновление. Во
            # втором случае правка нужна, только если в ней есть слова.
            if not edit.get("created") and not edit.get("add"):
                continue
            target = put(name, [])
        drop = {w.lower() for w in edit.get("remove", [])}
        categories[target] = [w for w in categories[target] if w.lower() not in drop]
        for word in edit.get("add", []):
            if not _has_word(categories[target], word):
                categories[target].append(word)
            owned[word.lower()] = target

    # Слово, добавленное человеком, принадлежит его категории: такое же слово в
    # категории выше иначе молча перехватило бы файл.
    for name in categories:
        categories[name] = [w for w in categories[name]
                            if owned.get(w.lower(), name) == name]

    # Своими папки только прибавляются: новое имя — чтобы файлы в него
    # складывались и переразлагались, старое — чтобы из него было кому вынести.
    managed = list(base.managed_folders)
    base_keys = {folder_key(name) for name in base.categories}
    for name in categories:
        if folder_key(name) not in base_keys:
            _grow(managed, name)
    for name in edits["keep_folders"]:
        _grow(managed, name)

    def follow(category: str) -> str | None:
        key = folder_key(category)
        if key in removed:
            return None
        return moved.get(key, category)

    dropped: dict[str, list[int]] = {}
    patterns: dict[str, list[str]] = {}
    for name, regexes in base.patterns.items():
        target = follow(name)
        if target is None:
            dropped.setdefault(name, [0, 0])[0] += len(regexes)
            continue
        target = _find(patterns, target) or target
        bucket = patterns.setdefault(target, [])
        bucket.extend(r for r in regexes if r not in bucket)

    overrides: dict[str, str] = {}
    for filename, category in base.overrides.items():
        target = follow(category)
        if target is None:
            dropped.setdefault(_find(dropped, category) or category, [0, 0])[1] += 1
            continue
        overrides[filename] = target

    for name, (n_patterns, n_rules) in dropped.items():
        notices.append(
            f"{USER_RULES_FILENAME}: категория «{name}» удалена — вместе с ней "
            f"сняты шаблоны ({n_patterns}) и ручные правила из overrides.json "
            f"({n_rules}).")

    skipped = []
    fallback = folder_key(base.fallback_category)
    for filename, category in edits["files"].items():
        target = _find(categories, category)
        if target is None or folder_key(category) == fallback:
            skipped.append(filename)
            continue
        mine = name_key(filename)
        for other in [k for k in overrides if name_key(k) == mine]:
            del overrides[other]
        overrides[filename] = target
    if skipped:
        notices.append(
            f"{USER_RULES_FILENAME}: правила для файлов пропущены — их категории "
            f"больше нет ({len(skipped)} шт.: {_shown(skipped)}).")

    return Merged(categories, patterns, overrides, managed)


# --- файл ---

def _name_ok(value) -> bool:
    return isinstance(value, str) and value != "" and not folder_name_problem(value)


def _words(raw, where: str, bad: list[str]) -> list[str] | None:
    if not isinstance(raw, list):
        bad.append(where)
        return None
    good = [w for w in raw if isinstance(w, str) and len(w.strip()) >= MIN_WORD]
    if len(good) != len(raw):
        bad.append(where)
    return good


def _clean(raw: dict, bad: list[str]) -> dict:
    edits = empty()
    categories = raw.get("categories", {})
    if isinstance(categories, dict):
        for name, edit in categories.items():
            where = f"categories.{name}"
            if not _name_ok(name) or not isinstance(edit, dict):
                bad.append(where)
                continue
            add = _words(edit.get("add", []), where, bad)
            remove = _words(edit.get("remove", []), where, bad)
            if add is None or remove is None:
                continue
            clean = {"add": add, "remove": remove}
            if edit.get("created") is True:
                clean["created"] = True
            edits["categories"][name] = clean
    else:
        bad.append("categories")

    renamed = raw.get("renamed", {})
    if isinstance(renamed, dict):
        for old, new in renamed.items():
            if _name_ok(old) and _name_ok(new):
                edits["renamed"][old] = new
            else:
                bad.append(f"renamed.{old}")
    else:
        bad.append("renamed")

    for key in ("removed", "keep_folders"):
        items = raw.get(key, [])
        if not isinstance(items, list):
            bad.append(key)
            continue
        edits[key] = [n for n in items if _name_ok(n)]
        if len(edits[key]) != len(items):
            bad.append(key)

    files = raw.get("files", {})
    if isinstance(files, dict):
        for filename, category in files.items():
            if filename.strip() and _name_ok(category):
                edits["files"][filename] = category
            else:
                bad.append(f"files.{filename}")
    else:
        bad.append("files")
    return edits


def read(path, problems: list[str]) -> tuple[dict, bool]:
    """Правки и флаг «файл есть, но читать его нельзя — писать поверх нельзя»."""
    path = Path(path)
    if not path.exists():
        return empty(), False
    try:
        raw = json.loads(read_text(path))
    except (OSError, ValueError) as exc:
        problems.append(f"{path.name}: не читается ({exc}). {_UNREADABLE_TAIL}")
        return empty(), True
    if not isinstance(raw, dict):
        problems.append(f"{path.name}: ожидался объект JSON. {_UNREADABLE_TAIL}")
        return empty(), True
    version = raw.get("version", VERSION)
    if type(version) is not int or version > VERSION:
        problems.append(
            f"{path.name}: версия {version!r} незнакома — файл записан более "
            f"новой программой. {_UNREADABLE_TAIL}")
        return empty(), True
    bad: list[str] = []
    edits = _clean(raw, bad)
    if bad:
        problems.append(f"{path.name}: негодные записи пропущены ({_shown(bad)}).")
    return edits, False


def write(path, edits: dict) -> None:
    """Атомарно: временный файл рядом и `os.replace`, полфайла не бывает."""
    path = Path(path)
    data = json.dumps(norm(edits), ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

- [ ] **Шаг 4. Тесты проходят**

Запуск: `python -m pytest tests/test_user_rules.py -q` — все зелёные.

- [ ] **Шаг 5. Коммит:** `Правки правил из окна: наложение my_rules.json поверх rules.json`

---

### Задача 2. Операции

**Файлы:** дописать `sorter/user_rules.py`, `tests/test_user_rules.py`.

- [ ] **Шаг 1. Тесты операций** — дописать в `tests/test_user_rules.py`:

```python
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
```

- [ ] **Шаг 2.** `python -m pytest tests/test_user_rules.py -q` — падают на отсутствующих операциях.

- [ ] **Шаг 3. Операции** — дописать в конец `sorter/user_rules.py`:

```python
# --- операции ---
#
# Каждая принимает правки и правила программы и возвращает НОВЫЕ правки либо
# бросает ValueError с фразой, которую окно показывает как есть. Исходные
# правки не меняются никогда: отказ не должен оставлять полуправку.

def _merged(base: Base, edits: dict) -> Merged:
    return merge(base, edits, [])


def _category(merged: Merged, name: str) -> str:
    found = _find(merged.categories, name)
    if found is None:
        raise ValueError(f"Категории «{name}» нет.")
    return found


def _check_name(name, merged: Merged, base: Base, ignore: str | None = None) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Имя категории пустое.")
    problem = folder_name_problem(name, "имя категории")
    if problem:
        raise ValueError(f"«{name}»: {problem}.")
    if folder_key(name) == folder_key(base.fallback_category):
        raise ValueError(f"«{base.fallback_category}» — запасная категория: туда "
                         "и так попадает всё неопознанное.")
    existing = _find([n for n in merged.categories if n != ignore], name)
    if existing is not None:
        raise ValueError(f"Категория «{existing}» уже есть.")


def _origin(base: Base, edits: dict, current: str) -> str | None:
    """Имя в `rules.json` категории, которая сейчас зовётся `current`.

    None — категорию создал человек.
    """
    key = folder_key(current)
    for old, new in edits["renamed"].items():
        if folder_key(new) == key:
            return old
    gone = {folder_key(n) for n in [*edits["removed"], *edits["renamed"]]}
    found = _find(base.categories, current)
    if found is not None and folder_key(found) not in gone:
        return found
    return None


def _edit_key(edits: dict, current: str) -> str | None:
    return _find(edits["categories"], current)


def _keep(edits: dict, name: str) -> None:
    if _find(edits["keep_folders"], name) is None:
        edits["keep_folders"].append(name)


def _forget_rename(edits: dict, origin: str) -> None:
    for old in [o for o in edits["renamed"] if folder_key(o) == folder_key(origin)]:
        del edits["renamed"][old]


def _tidy(edits: dict) -> dict:
    """Пустая правка слов без `created` ничего не значит — убираем."""
    edits["categories"] = {
        name: edit for name, edit in edits["categories"].items()
        if edit.get("add") or edit.get("remove") or edit.get("created")}
    return edits


def add_category(edits: dict, base: Base, name: str) -> dict:
    edits = norm(edits)
    merged = _merged(base, edits)
    was_removed = _find(edits["removed"], name) if isinstance(name, str) else None
    if was_removed is not None and _find(merged.categories, name) is None:
        # Имя удалённой категории программы: возвращаем её саму, со словами и
        # шаблонами программы и на её прежнем месте.
        edits["removed"].remove(was_removed)
        return _tidy(edits)
    _check_name(name, merged, base)
    key = _edit_key(edits, name)
    if key is None:
        edits["categories"][name] = {"add": [], "remove": [], "created": True}
    else:
        edits["categories"][key]["created"] = True
    return _tidy(edits)


def rename_category(edits: dict, base: Base, old: str, new: str) -> dict:
    edits = norm(edits)
    merged = _merged(base, edits)
    current = _category(merged, old)
    if isinstance(new, str) and folder_key(new) == folder_key(current):
        raise ValueError("Новое имя совпадает со старым.")
    _check_name(new, merged, base, ignore=current)
    origin = _origin(base, edits, current)
    if origin is not None:
        _forget_rename(edits, origin)
        if folder_key(new) != folder_key(origin):
            edits["renamed"][origin] = new
    key = _edit_key(edits, current)
    if key is not None:
        edits["categories"] = {(new if k == key else k): v
                               for k, v in edits["categories"].items()}
    _keep(edits, current)
    for filename, category in edits["files"].items():
        if folder_key(category) == folder_key(current):
            edits["files"][filename] = new
    return _tidy(edits)


def remove_category(edits: dict, base: Base, name: str) -> dict:
    edits = norm(edits)
    current = _category(_merged(base, edits), name)
    origin = _origin(base, edits, current)
    if origin is not None:
        _forget_rename(edits, origin)
        edits["removed"].append(origin)
    key = _edit_key(edits, current)
    if key is not None:
        del edits["categories"][key]
    _keep(edits, current)
    edits["files"] = {f: c for f, c in edits["files"].items()
                      if folder_key(c) != folder_key(current)}
    return _tidy(edits)


def _word_edit(edits: dict, current: str) -> dict:
    key = _edit_key(edits, current)
    if key is None:
        key = current
        edits["categories"][key] = {"add": [], "remove": []}
    return edits["categories"][key]


def add_word(edits: dict, base: Base, category: str, word: str) -> dict:
    edits = norm(edits)
    merged = _merged(base, edits)
    current = _category(merged, category)
    if not isinstance(word, str) or len(word.strip()) < MIN_WORD:
        raise ValueError(f"Слово короче {MIN_WORD} знаков: подстрокой оно "
                         "зацепит почти любое имя.")
    if _has_word(merged.categories[current], word):
        raise ValueError(f"Слово «{word}» уже есть в «{current}».")
    for other, words in merged.categories.items():
        if other != current and _has_word(words, word):
            raise ValueError(f"Слово «{word}» уже есть в категории «{other}». "
                             "Сначала убери его оттуда.")
    edit = _word_edit(edits, current)
    low = word.lower()
    edit["remove"] = [w for w in edit["remove"] if w.lower() != low]
    if not _has_word(_merged(base, edits).categories[current], word):
        edit["add"].append(word)
    return _tidy(edits)


def remove_word(edits: dict, base: Base, category: str, word: str) -> dict:
    edits = norm(edits)
    merged = _merged(base, edits)
    current = _category(merged, category)
    if not isinstance(word, str) or not _has_word(merged.categories[current], word):
        raise ValueError(f"В «{current}» нет слова «{word}».")
    edit = _word_edit(edits, current)
    low = word.lower()
    edit["add"] = [w for w in edit["add"] if w.lower() != low]
    if _has_word(_merged(base, edits).categories[current], word):
        edit["remove"].append(word)
    return _tidy(edits)


def set_file(edits: dict, base: Base, filename: str, category: str) -> dict:
    edits = norm(edits)
    if isinstance(category, str) and folder_key(category) == folder_key(base.fallback_category):
        raise ValueError(
            f"«{base.fallback_category}» — запасная категория. Правило туда "
            f"ничего не решает: без него файл уедет в «{base.fallback_category}» "
            "сам, а в новую категорию уже не попадёт никогда.")
    current = _category(_merged(base, edits), category)
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Имя файла пустое.")
    mine = name_key(filename)
    edits["files"] = {f: c for f, c in edits["files"].items() if name_key(f) != mine}
    edits["files"][filename] = current
    return edits


def clear_file(edits: dict, base: Base, filename: str) -> dict:
    edits = norm(edits)
    if file_rule(edits, filename) is None:
        raise ValueError(f"Для «{filename}» нет правила из окна.")
    mine = name_key(filename)
    edits["files"] = {f: c for f, c in edits["files"].items() if name_key(f) != mine}
    return edits


def file_rule(edits: dict, filename: str) -> str | None:
    """Категория из правила окна для этого файла. None — правила нет."""
    mine = name_key(filename)
    for name, category in norm(edits)["files"].items():
        if name_key(name) == mine:
            return category
    return None


# --- для окна ---

def apply(config, edits: dict):
    """Тот же конфиг, но с этими правками вместо прежних. Файлы не трогает."""
    edits = norm(edits)
    merged = merge(config.base, edits, [])
    return replace(config, categories=merged.categories, patterns=merged.patterns,
                   overrides=merged.overrides,
                   managed_folders=merged.managed_folders, user_rules=edits)


def moved_by(names, before, after) -> Counter:
    """Сколько файлов из `names` сменят категорию: {откуда: сколько}.

    Считается по имени: содержимое текстовых файлов диалог не читает.
    """
    from .classifier import explain_category
    moved: Counter = Counter()
    for name in names:
        old = explain_category(name, "", before)[0]
        if explain_category(name, "", after)[0] != old:
            moved[old] += 1
    return moved
```

`Config(..., base=b, user_rules=...)` в последнем тесте требует полей из задачи 3 — этот тест пройдёт после неё.

- [ ] **Шаг 4.** `python -m pytest tests/test_user_rules.py -q` — зелёные все, кроме `test_moved_by_counts_files_the_word_would_take`.

- [ ] **Шаг 5. Коммит:** `Операции над правками: категории, слова, правило для файла`

---

### Задача 3. `Config.load` накладывает правки

**Файлы:** `sorter/config.py`, `tests/test_user_rules.py`.

- [ ] **Шаг 1. Тесты связки** — дописать:

```python
# --- Config.load ---

RULES = {
    "categories": {"Медиа": ["клип"], "Документы": ["отчёт"]},
    "patterns": {"Медиа": [r"^\d{4}\.mp4$"]},
    "type_map": {"Videos": ["mp4"], "Documents": ["pdf"]},
    "managed_folders": ["Медиа", "Документы", "Videos", "Documents", "Others", "Misc"],
    "fallback_category": "Others", "fallback_type": "Misc",
}


def setup(tmp_path, edits=None, overrides=None):
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps(RULES, ensure_ascii=False),
                                         encoding="utf-8")
    if overrides is not None:
        (tmp_path / "overrides.json").write_text(
            json.dumps(overrides, ensure_ascii=False), encoding="utf-8")
    if edits is not None:
        ur.write(ur.path_for(tmp_path / "config.json"), ur.norm(edits))
    return tmp_path / "config.json"


def test_load_applies_user_rules_without_new_problems(tmp_path):
    path = setup(tmp_path, {"categories": {"Рецепты": {"add": ["рецепт"], "remove": [],
                                                       "created": True}},
                            "renamed": {"Медиа": "Видео"}})
    cfg = Config.load(path)
    assert list(cfg.categories) == ["Видео", "Документы", "Рецепты"]
    assert cfg.patterns == {"Видео": [r"^\d{4}\.mp4$"]}
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
    """Правило из окна над шаблоном — решение человека, а не спор с программой."""
    path = setup(tmp_path, {"files": {"0001.mp4": "Документы"}})
    cfg = Config.load(path)
    assert cfg.notices == []
    assert explain_category("0001.mp4", "", cfg) == ("Документы", BY_RULE)
```

- [ ] **Шаг 2.** `python -m pytest tests/test_user_rules.py -q` — новые падают (`user`, `base` неизвестны).

- [ ] **Шаг 3. Правка `sorter/config.py`**

В классе `Config` после `settings_unreadable: bool = False` добавить:

```python
    # Правила программы до правок из окна (`user_rules.Base`) и сами правки.
    # Окно показывает, что пришло с программой, а что добавил человек, и
    # строит новые правки поверх этих.
    base: object | None = None
    user_rules: dict = field(default_factory=dict)
    # `my_rules.json` есть, но читать его нельзя: окно не пишет поверх.
    user_rules_unreadable: bool = False
```

Сигнатура: `def load(cls, path: str | Path, *, user: bool = True) -> "Config":`, в
docstring строка: «`user=False` — без `my_rules.json`: снимки настоящих правил
проверяют правила программы, а не правки человека».

Сразу после `overrides = _drop_frozen_rules(...)` вставить:

```python
        # Правки из окна поверх правил программы (`user_rules`). Импорт здесь,
        # а не наверху: модулю нужны имена из этого файла.
        from . import user_rules
        base = user_rules.Base(categories, patterns, overrides, managed_folders,
                               fallback_category)
        edits, user_unreadable = (
            user_rules.read(user_rules.path_for(path), problems) if user
            else (user_rules.empty(), False))
        merged = user_rules.merge(base, edits, notices)
        categories, patterns = merged.categories, merged.patterns
        overrides, managed_folders = merged.overrides, merged.managed_folders
```

Вызов `_check_overridden_patterns(overrides, ...)` заменить на

```python
        mine = set(edits["files"])
        _check_overridden_patterns(
            {k: v for k, v in overrides.items() if k not in mine},
            patterns, overrides_name, notices)
```

И в `return cls(...)` дописать `base=base, user_rules=edits,
user_rules_unreadable=user_unreadable,`.

- [ ] **Шаг 4.** `python -m pytest -q` — весь набор зелёный (566 старых + новые).

- [ ] **Шаг 5. Коммит:** `Config.load накладывает my_rules.json; снимки правил читают без него`
  (вместе с задачей 5 — см. ниже, если снимки падают на машине с `my_rules.json`).

---

### Задача 4. Свойства на hypothesis

**Файлы:** создать `tests/test_user_rules_props.py`.

- [ ] **Шаг 1. Тесты**

```python
"""Свойства правок из окна: то, что обязано держаться при любых действиях.

Юнит-тесты стерегут придуманные автором сценарии. Здесь hypothesis строит
случайные правила программы и случайные цепочки действий человека — удачных и
отказавших вперемешку — и после каждой проверяет договор целиком.
"""
import copy
import json
import os

from hypothesis import HealthCheck, assume, given, settings, strategies as st

from sorter import user_rules as ur
from sorter.classifier import BY_PATTERN, BY_RULE, explain_category
from sorter.config import Config, folder_key

RUN = settings(max_examples=300, deadline=None,
               suppress_health_check=[HealthCheck.too_slow,
                                      HealthCheck.function_scoped_fixture])

LETTERS = "абвгдеёжзиклмнопрстуфхцчшщыэюяABCDEFabcdef0123456789"
NAME = st.text(LETTERS, min_size=1, max_size=8)
# Имена, которые годиться не обязаны: пути, запрещённые символы, пробелы по
# краям, запасная категория в другом регистре.
ANY_NAME = st.one_of(NAME, st.sampled_from(
    ["", " ", "C:/x", "a?b", "x ", " x", "..", "OTHERS", "others", "a/b", "a\x01"]),
    st.text(max_size=6))
WORD = st.text(LETTERS + " -", min_size=1, max_size=7)


@st.composite
def bases(draw):
    names = draw(st.lists(NAME, min_size=1, max_size=5,
                          unique_by=lambda n: folder_key(n)))
    names = [n for n in names if folder_key(n) != folder_key("Others")]
    assume(names)
    pool = draw(st.lists(st.text(LETTERS, min_size=2, max_size=6), max_size=12,
                         unique_by=str.lower))
    categories = {n: [] for n in names}
    for word in pool:
        categories[draw(st.sampled_from(names))].append(word)
    patterns = {n: [f"^{n}_\\d+$"] for n in names if draw(st.booleans())}
    files = draw(st.lists(st.text(LETTERS, min_size=1, max_size=6), max_size=4))
    overrides = {f + ".bin": draw(st.sampled_from(names)) for f in files}
    managed = [*names, "Others", "Misc"]
    return ur.Base(categories, patterns, overrides, managed, "Others")


def actions(names_hint):
    name = st.one_of(st.sampled_from(names_hint), ANY_NAME)
    return st.lists(st.one_of(
        st.tuples(st.just("add_category"), name),
        st.tuples(st.just("rename_category"), name, name),
        st.tuples(st.just("remove_category"), name),
        st.tuples(st.just("add_word"), name, WORD),
        st.tuples(st.just("remove_word"), name, WORD),
        st.tuples(st.just("set_file"), st.text(LETTERS, min_size=1, max_size=5), name),
        st.tuples(st.just("clear_file"), st.text(LETTERS, min_size=1, max_size=5)),
    ), max_size=25)


@st.composite
def scenario(draw):
    base = draw(bases())
    hint = [*base.categories, "Others"]
    return base, draw(actions(hint))


def run(base, steps):
    """Прогнать действия, проверяя договор после каждого. Вернуть правки."""
    edits = ur.empty()
    for kind, *args in steps:
        before = copy.deepcopy(edits)
        managed_before = set(map(folder_key, ur.merge(base, edits).managed_folders))
        try:
            edits = getattr(ur, kind)(edits, base, *args)
        except ValueError:
            assert edits == before, f"{kind} отказал, но правки изменились"
            continue
        consistent(base, edits)
        managed_after = set(map(folder_key, ur.merge(base, edits).managed_folders))
        assert managed_before <= managed_after, f"{kind} сузил managed_folders"
    return edits


def consistent(base, edits):
    m = ur.merge(base, edits)
    keys = [folder_key(n) for n in m.categories]
    assert len(keys) == len(set(keys)), "две категории с одним именем"
    managed = set(map(folder_key, m.managed_folders))
    for name in m.categories:
        assert all(folder_key(p) in managed for p in name.replace("\\", "/").split("/")
                   if p), f"«{name}» не своя папка"
    for name in m.patterns:
        assert folder_key(name) in keys, f"шаблон на несуществующую «{name}»"
    for category in m.overrides.values():
        assert folder_key(category) in keys, f"правило на несуществующую «{category}»"
    seen = {}
    for name, words in m.categories.items():
        for w in words:
            assert w.lower() not in seen, f"«{w}» и в «{seen[w.lower()]}», и в «{name}»"
            seen[w.lower()] = name
    assert folder_key("Others") not in keys


@RUN
@given(scenario())
def test_any_sequence_of_actions_keeps_the_rules_consistent(case):
    base, steps = case
    run(base, steps)


@RUN
@given(scenario())
def test_written_file_reads_back_the_same(tmp_path_factory, case):
    base, steps = case
    edits = run(base, steps)
    path = tmp_path_factory.mktemp("r") / ur.USER_RULES_FILENAME
    ur.write(path, edits)
    assert ur.read(path, []) == (edits, False)


@RUN
@given(scenario())
def test_config_load_finds_no_new_problems(tmp_path_factory, case):
    base, steps = case
    edits = run(base, steps)
    folder = tmp_path_factory.mktemp("c")
    (folder / "config.json").write_text(json.dumps({"downloads_path": str(folder)}),
                                        encoding="utf-8")
    (folder / "rules.json").write_text(json.dumps({
        "categories": base.categories, "patterns": base.patterns,
        "type_map": {"Docs": ["pdf"]}, "managed_folders": [*base.managed_folders, "Docs"],
        "fallback_category": "Others", "fallback_type": "Misc"},
        ensure_ascii=False), encoding="utf-8")
    (folder / "overrides.json").write_text(json.dumps(base.overrides, ensure_ascii=False),
                                           encoding="utf-8")
    plain = Config.load(folder / "config.json", user=False).problems
    ur.write(ur.path_for(folder / "config.json"), edits)
    assert Config.load(folder / "config.json").problems == plain


@RUN
@given(bases(), st.data())
def test_rename_and_back_restores_the_categories(base, data):
    name = data.draw(st.sampled_from(list(base.categories)))
    other = data.draw(NAME)
    try:
        edits = ur.rename_category(ur.empty(), base, name, other)
    except ValueError:
        return
    edits = ur.rename_category(edits, base, other, name)
    assert ur.merge(base, edits).categories == base.categories


@RUN
@given(bases(), st.data())
def test_word_added_and_removed_restores_the_categories(base, data):
    name = data.draw(st.sampled_from(list(base.categories)))
    word = data.draw(WORD)
    try:
        edits = ur.add_word(ur.empty(), base, name, word)
    except ValueError:
        return
    edits = ur.remove_word(edits, base, name, word)
    assert ur.merge(base, edits).categories == base.categories


@RUN
@given(scenario(), bases())
def test_edits_survive_an_upstream_update(case, upstream):
    """Обновление меняет rules.json; правки человека остаются, новое приезжает."""
    base, steps = case
    edits = run(base, steps)
    before = ur.merge(base, edits)
    after = ur.merge(upstream, edits)
    for name, edit in edits["categories"].items():
        for word in edit["add"]:
            owner = ur._find(before.categories, name)
            if owner and ur._has_word(before.categories[owner], word):
                target = ur._find(after.categories, name)
                assert target and ur._has_word(after.categories[target], word)
    removed = set(map(folder_key, edits["removed"]))
    renamed = set(map(folder_key, edits["renamed"]))
    for name in upstream.categories:
        if folder_key(name) not in removed | renamed:
            assert ur._find(after.categories, name), f"«{name}» из обновления не приехала"
    consistent(upstream, edits)


@RUN
@given(scenario(), st.data())
def test_file_rule_always_wins(case, data):
    base, steps = case
    edits = run(base, steps)
    m = ur.merge(base, edits)
    category = data.draw(st.sampled_from(list(m.categories)))
    filename = data.draw(st.text(LETTERS, min_size=1, max_size=8)) + ".mp4"
    edits = ur.set_file(edits, base, filename, category)
    m = ur.merge(base, edits)
    cfg = Config(downloads_path="x", categories=m.categories, patterns=m.patterns,
                 overrides=m.overrides)
    assert explain_category(filename, "", cfg) == (category, BY_RULE)


@RUN
@given(scenario(), st.data())
def test_added_word_takes_the_file_unless_something_stronger_does(case, data):
    base, steps = case
    edits = run(base, steps)
    m = ur.merge(base, edits)
    category = data.draw(st.sampled_from(list(m.categories)))
    word = data.draw(st.text(LETTERS, min_size=2, max_size=6))
    try:
        edits = ur.add_word(edits, base, category, word)
    except ValueError:
        return
    m = ur.merge(base, edits)
    filename = f"x{word}y.dat"
    cfg = Config(downloads_path="x", categories=m.categories, patterns=m.patterns,
                 overrides=m.overrides)
    got, why = explain_category(filename, "", cfg)
    if why in (BY_RULE, BY_PATTERN):
        return
    earlier = list(m.categories)[:list(m.categories).index(category)]
    stolen = any(w.strip() and w.lower() in filename.lower()
                 for c in earlier for w in m.categories[c])
    assert got == category or stolen


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=200))
def test_any_garbage_in_the_file_does_not_crash_loading(tmp_path_factory, raw):
    path = tmp_path_factory.mktemp("g") / ur.USER_RULES_FILENAME
    path.write_bytes(raw)
    problems = []
    edits, unreadable = ur.read(path, problems)
    assert set(edits) == set(ur.empty())
    assert unreadable or isinstance(edits["files"], dict)


JSONISH = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=5),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3),
    max_leaves=12)


@settings(max_examples=300, deadline=None)
@given(st.dictionaries(st.sampled_from(["version", "categories", "renamed", "removed",
                                        "keep_folders", "files", "лишнее"]),
                       JSONISH, max_size=6))
def test_json_of_the_wrong_shape_is_cleaned_not_crashed(tmp_path_factory, raw):
    path = tmp_path_factory.mktemp("j") / ur.USER_RULES_FILENAME
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    edits, unreadable = ur.read(path, [])
    if not unreadable:
        base = ur.Base({"Учёба": ["задач"]}, {}, {}, ["Учёба", "Others"], "Others")
        ur.merge(base, edits)   # не падает на том, что прошло чистку
```

- [ ] **Шаг 2.** `python -m pytest tests/test_user_rules_props.py -q`. Каждое падение
  разобрать: либо баг в модуле (чинить модуль и добавить юнит-тест на найденное),
  либо свойство сформулировано сильнее договора (править свойство и записать почему).

- [ ] **Шаг 3. Коммит:** `Свойства правок из окна на hypothesis`

---

### Задача 5. Снимки настоящих правил без `my_rules.json`

**Файлы:** `tests/test_real_config.py:20,318`, `tests/test_real_layout_snapshot.py:64,115`.

- [ ] **Шаг 1.** Заменить в этих строках `Config.load(CONFIG_PATH)` на
  `Config.load(CONFIG_PATH, user=False)` и дописать над первым вызовом в каждом
  файле комментарий: «Без my_rules.json: снимок проверяет правила программы, а
  правки человека в папке разработчика ломали бы его на его машине».
- [ ] **Шаг 2.** `python -m pytest -q` — зелёный.
- [ ] **Шаг 3.** Коммит вместе с задачей 3.

---

### Задача 6. Диалог «Правила раскладки»

**Файлы:** создать `sorter/ui_rules.py`, `tests/test_ui_rules.py`.

- [ ] **Шаг 1. Тесты диалога**

```python
"""Диалог «Правила раскладки» и правила из контекстного меню окна."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from sorter import ui_qt, ui_rules, user_rules as ur
from sorter.config import Config


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def folder(tmp_path):
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    for name in ("клип.mp4", "отчёт.pdf", "видео-урок.mp4", "рецепт борща.pdf"):
        (downloads / name).write_text("x", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"downloads_path": str(downloads)}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"], "Документы": ["отчёт"]},
        "type_map": {"Videos": ["mp4"], "Documents": ["pdf"]},
        "managed_folders": ["Медиа", "Документы", "Videos", "Documents", "Others", "Misc"],
        "fallback_category": "Others", "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def dialog(app, folder):
    cfg = Config.load(folder / "config.json")
    dlg = ui_rules.RulesDialog(cfg, ["клип.mp4", "видео-урок.mp4", "рецепт борща.pdf"])
    yield dlg
    dlg.deleteLater()


def typed(monkeypatch, text):
    monkeypatch.setattr(ui_rules.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (text, True)))


def test_dialog_lists_program_categories(dialog):
    assert dialog.category_names() == ["Медиа", "Документы"]


def test_add_category_from_the_dialog(dialog, monkeypatch):
    typed(monkeypatch, "Рецепты")
    dialog.add_category()
    assert dialog.category_names()[-1] == "Рецепты"
    assert dialog.selected() == "Рецепты"


def test_refused_name_is_shown_and_changes_nothing(dialog, monkeypatch):
    typed(monkeypatch, "медиа")
    dialog.add_category()
    assert "уже есть" in dialog.hint.text()
    assert dialog.edits == ur.empty()


def test_word_preview_counts_files_it_would_take(dialog):
    dialog.select("Медиа")
    dialog.word_edit.setText("видео")
    assert dialog.hint.text() == "Заберёт из плана: 1 (Others: 1)."
    dialog.word_edit.setText("zzz")
    assert dialog.hint.text() == "В текущем плане таких файлов нет."
    dialog.word_edit.setText("отчёт")
    assert "уже есть в категории «Документы»" in dialog.hint.text()


def test_add_and_remove_word(dialog):
    dialog.select("Медиа")
    dialog.word_edit.setText("видео")
    dialog.add_word()
    assert "видео" in dialog.word_names() and dialog.word_edit.text() == ""
    assert dialog.is_mine("видео")
    dialog.select_word("видео")
    dialog.remove_word()
    assert dialog.edits == ur.empty()


def test_rename_and_remove_category(dialog, monkeypatch):
    dialog.select("Медиа")
    typed(monkeypatch, "Видео")
    dialog.rename_category()
    assert dialog.category_names() == ["Видео", "Документы"]
    dialog.remove_category()
    assert dialog.category_names() == ["Документы"]


def test_cancel_writes_nothing(dialog, folder, monkeypatch):
    typed(monkeypatch, "Рецепты")
    dialog.add_category()
    dialog.reject()
    assert not ur.path_for(folder / "config.json").exists()


# --- окно ---

@pytest.fixture
def window(app, folder, monkeypatch):
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(ui_qt.QMessageBox, kind, staticmethod(lambda *a, **k: None))
    win = ui_qt.GlassWindow(folder / "config.json")
    win.preview()
    yield win
    win.deleteLater()


def row_of(win, name):
    return next(i for i, mv in enumerate(win.moves) if mv.src.name == name)


def test_context_menu_puts_a_file_into_a_category(window, folder):
    menu = window.rule_menu(row_of(window, "рецепт борща.pdf"))
    into = next(a.menu() for a in menu.actions() if a.text() == "Всегда класть в")
    next(a for a in into.actions() if a.text() == "Медиа").trigger()
    saved = ur.read(ur.path_for(folder / "config.json"), [])[0]
    assert saved["files"] == {"рецепт борща.pdf": "Медиа"}
    mv = window.moves[row_of(window, "рецепт борща.pdf")]
    assert mv.dst.parent.parent.name == "Медиа"


def test_context_menu_removes_my_rule(window, folder):
    window.set_file_rule("рецепт борща.pdf", "Медиа")
    menu = window.rule_menu(row_of(window, "рецепт борща.pdf"))
    next(a for a in menu.actions() if a.text() == "Убрать моё правило").trigger()
    assert ur.read(ur.path_for(folder / "config.json"), [])[0]["files"] == {}


def test_rules_button_saves_and_rebuilds_the_plan(window, folder, monkeypatch):
    def accept(self):
        self.edits = ur.add_category(self.edits, self.config.base, "Рецепты")
        self.edits = ur.add_word(self.edits, self.config.base, "Рецепты", "рецепт")
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui_rules.RulesDialog, "exec", accept)
    window.show_rules()
    assert "Рецепты" in window.config.categories
    mv = window.moves[row_of(window, "рецепт борща.pdf")]
    assert mv.dst.parent.parent.name == "Рецепты"


def test_unreadable_rules_file_is_not_overwritten(window, folder):
    path = ur.path_for(folder / "config.json")
    path.write_text("{", encoding="utf-8")
    window.config = Config.load(folder / "config.json")
    window.set_file_rule("отчёт.pdf", "Медиа")
    assert path.read_text(encoding="utf-8") == "{"
```

- [ ] **Шаг 2.** `python -m pytest tests/test_ui_rules.py -q` — падает на импорте `ui_rules`.

- [ ] **Шаг 3. Диалог** — `sorter/ui_rules.py`:

```python
"""Диалог «Правила раскладки»: категории и их слова из окна.

Диалог правит копию правок и файлов не пишет: «Сохранить» отдаёт правки окну,
«Отмена» просто закрывается. Запись и перестройка плана — дело окна
(`GlassWindow._write_rules`), одно место на диалог и контекстное меню.
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from . import user_rules as ur

MINE = "  ✎"


class RulesDialog(QDialog):
    def __init__(self, config, names: list[str], parent=None):
        super().__init__(parent)
        self.config = config
        self.names = list(names)
        self.edits = ur.norm(copy.deepcopy(config.user_rules))
        self.setWindowTitle("Правила раскладки")
        self.resize(680, 460)

        self.cats = QListWidget()
        self.cats.currentRowChanged.connect(lambda *_: self._fill_words())
        self.words = QListWidget()
        self.word_edit = QLineEdit()
        self.word_edit.setPlaceholderText("Новое слово для выбранной категории")
        self.word_edit.textChanged.connect(self._preview_word)
        self.word_edit.returnPressed.connect(self.add_word)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(QLabel("Категории (выше — главнее)"))
        left.addWidget(self.cats, stretch=1)
        row = QHBoxLayout()
        for text, slot in (("Добавить", self.add_category),
                           ("Переименовать", self.rename_category),
                           ("Удалить", self.remove_category)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        left.addLayout(row)

        right = QVBoxLayout()
        right.addWidget(QLabel("Слова: достаточно, чтобы слово было частью имени файла"))
        right.addWidget(self.words, stretch=1)
        add_row = QHBoxLayout()
        add_row.addWidget(self.word_edit, stretch=1)
        add_btn = QPushButton("Добавить слово")
        add_btn.clicked.connect(self.add_word)
        add_row.addWidget(add_btn)
        right.addLayout(add_row)
        remove_btn = QPushButton("Убрать выбранное слово")
        remove_btn.clicked.connect(self.remove_word)
        right.addWidget(remove_btn)

        body = QHBoxLayout()
        body.addLayout(left, stretch=1)
        body.addLayout(right, stretch=1)

        footer = QHBoxLayout()
        footer.addWidget(QLabel(f"{MINE.strip()} — добавлено тобой"))
        footer.addStretch(1)
        save = QPushButton("Сохранить")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        footer.addWidget(save)
        footer.addWidget(cancel)

        root = QVBoxLayout(self)
        root.addLayout(body, stretch=1)
        root.addWidget(self.hint)
        root.addLayout(footer)
        self._fill_categories()

    # --- состояние ---

    def current(self):
        return ur.apply(self.config, self.edits)

    def category_names(self) -> list[str]:
        return list(self.current().categories)

    def selected(self) -> str | None:
        item = self.cats.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, name: str) -> None:
        for row in range(self.cats.count()):
            if self.cats.item(row).data(Qt.ItemDataRole.UserRole) == name:
                self.cats.setCurrentRow(row)
                return

    def word_names(self) -> list[str]:
        return [self.words.item(r).data(Qt.ItemDataRole.UserRole)
                for r in range(self.words.count())]

    def select_word(self, word: str) -> None:
        self.words.setCurrentRow(self.word_names().index(word))

    def is_mine(self, word: str) -> bool:
        name = self.selected()
        key = ur._find(self.edits["categories"], name) if name else None
        added = self.edits["categories"][key]["add"] if key else []
        return ur._has_word(added, word)

    def _category_is_mine(self, name: str) -> bool:
        return ur._origin(self.config.base, self.edits, name) is None or any(
            ur.folder_key(new) == ur.folder_key(name) for new in self.edits["renamed"].values())

    # --- списки ---

    def _item(self, raw: str, mine: bool) -> QListWidgetItem:
        item = QListWidgetItem(raw + (MINE if mine else ""))
        item.setData(Qt.ItemDataRole.UserRole, raw)
        if mine:
            font = QFont()
            font.setItalic(True)
            item.setFont(font)
            item.setToolTip("Добавлено тобой, а не пришло с программой")
        return item

    def _fill_categories(self, select: str | None = None) -> None:
        keep = select or self.selected()
        self.cats.blockSignals(True)
        self.cats.clear()
        for name in self.category_names():
            self.cats.addItem(self._item(name, self._category_is_mine(name)))
        self.cats.blockSignals(False)
        found = ur._find(self.category_names(), keep) if keep else None
        if found:
            self.select(found)
        elif self.cats.count():
            self.cats.setCurrentRow(0)
        self._fill_words()

    def _fill_words(self) -> None:
        self.words.clear()
        name = self.selected()
        if name is None:
            return
        for word in self.current().categories.get(name, []):
            self.words.addItem(self._item(word, self.is_mine(word)))
        self._preview_word(self.word_edit.text())

    # --- действия ---

    def _do(self, fn, *args) -> bool:
        try:
            self.edits = fn(self.edits, self.config.base, *args)
        except ValueError as exc:
            self.hint.setText(str(exc))
            return False
        self.hint.setText("")
        return True

    def add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Новая категория", "Имя категории:")
        if ok and self._do(ur.add_category, name):
            self._fill_categories(select=name)

    def rename_category(self) -> None:
        old = self.selected()
        if old is None:
            return
        new, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", text=old)
        if ok and self._do(ur.rename_category, old, new):
            self._fill_categories(select=new)

    def remove_category(self) -> None:
        name = self.selected()
        if name is not None and self._do(ur.remove_category, name):
            self._fill_categories()

    def add_word(self) -> None:
        name = self.selected()
        word = self.word_edit.text()
        if name is not None and self._do(ur.add_word, name, word):
            self.word_edit.clear()
            self._fill_words()

    def remove_word(self) -> None:
        name = self.selected()
        item = self.words.currentItem()
        if name is None or item is None:
            return
        if self._do(ur.remove_word, name, item.data(Qt.ItemDataRole.UserRole)):
            self._fill_words()

    def _preview_word(self, text: str) -> None:
        name = self.selected()
        if not text.strip() or name is None:
            self.hint.setText("")
            return
        try:
            trial = ur.add_word(self.edits, self.config.base, name, text)
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        moved = ur.moved_by(self.names, self.current(), ur.apply(self.config, trial))
        if not moved:
            self.hint.setText("В текущем плане таких файлов нет.")
            return
        parts = ", ".join(f"{k}: {v}" for k, v in moved.most_common())
        self.hint.setText(f"Заберёт из плана: {sum(moved.values())} ({parts}).")
```

`ur.folder_key` доступен, потому что `user_rules` импортирует его из `config`.

- [ ] **Шаг 4.** `python -m pytest tests/test_ui_rules.py -q -k "not window and not context and not button and not unreadable"` — тесты диалога зелёные.

- [ ] **Шаг 5. Коммит:** `Диалог «Правила раскладки»`

---

### Задача 7. Кнопка и контекстное меню в окне

**Файлы:** `sorter/ui_qt.py`.

- [ ] **Шаг 1. Импорты:** в `from PyQt6.QtWidgets import (...)` добавить `QMenu`; ниже
  `from . import history` добавить `from . import user_rules` и
  `from .ui_rules import RulesDialog`.

- [ ] **Шаг 2. Кнопка** — в `_header` после `history_btn`:

```python
        rules_btn = QPushButton("⚙ Правила")
        rules_btn.clicked.connect(self.show_rules)
```

и `row.addWidget(rules_btn)` после `row.addWidget(history_btn)`.

- [ ] **Шаг 3. Меню таблицы** — в `_table` перед `return self.table`:

```python
        # Правый клик по файлу: «всегда класть в…». Пишет my_rules.json сразу —
        # подтверждать нечего, результат виден в той же строке плана.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
```

- [ ] **Шаг 4. Методы окна** — после `show_history`:

```python
    def show_rules(self):
        dlg = RulesDialog(self.config, [mv.src.name for mv in self.moves], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._write_rules(dlg.edits)

    def rule_menu(self, row: int):
        """Меню правил для строки плана. None — строки нет."""
        if not 0 <= row < len(self.moves):
            return None
        name = self.moves[row].src.name
        menu = QMenu(self)
        into = menu.addMenu("Всегда класть в")
        for category in self.config.categories:
            action = into.addAction(category)
            action.triggered.connect(
                lambda _=False, c=category: self.set_file_rule(name, c))
        if user_rules.file_rule(self.config.user_rules, name):
            action = menu.addAction("Убрать моё правило")
            action.triggered.connect(lambda _=False: self.clear_file_rule(name))
        return menu

    def _table_menu(self, pos):
        menu = self.rule_menu(self.table.rowAt(pos.y()))
        if menu is not None:
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def set_file_rule(self, name: str, category: str):
        self._change_rules(user_rules.set_file, name, category)

    def clear_file_rule(self, name: str):
        self._change_rules(user_rules.clear_file, name)

    def _change_rules(self, fn, *args):
        try:
            edits = fn(self.config.user_rules, self.config.base, *args)
        except ValueError as exc:
            QMessageBox.information(self, "Правило не задано", str(exc))
            return
        self._write_rules(edits)

    def _write_rules(self, edits) -> bool:
        """Пишет my_rules.json, перечитывает правила и перестраивает план.

        На место нечитаемого файла не пишем: там правки человека, взять их
        больше неоткуда (`user_rules.read`).
        """
        if self.config.user_rules_unreadable:
            QMessageBox.warning(
                self, "Правила не сохранены",
                f"{user_rules.USER_RULES_FILENAME} не читается, и окно не пишет "
                "поверх, чтобы не стереть правки. Поправь его в редакторе или "
                "удали, и попробуй снова.")
            return False
        try:
            user_rules.write(user_rules.path_for(self.config_path), edits)
        except OSError as exc:
            QMessageBox.critical(self, "Правила не сохранены", str(exc))
            return False
        self.config = Config.load(self.config_path)
        self.preview()
        return True
```

- [ ] **Шаг 5.** `python -m pytest -q` — весь набор зелёный.

- [ ] **Шаг 6. Коммит:** `Окно: кнопка «⚙ Правила» и «Всегда класть в…» по правому клику`

---

### Задача 8. Документация и `.gitignore`

- [ ] **Шаг 1.** `.gitignore`: после блока «Локальные логи» добавить

```
# Правки правил из окна: у каждого человека свои (см. sorter/user_rules.py)
my_rules.json
```

- [ ] **Шаг 2.** README: в «Возможности» после пункта про приоритет категорий
  раздел **«Правила из окна»**: что можно (категории, слова, правило для файла по
  правому клику), где хранится (`my_rules.json` рядом с `config.json`, поверх
  `rules.json`, переживает обновления), почему не в `rules.json`, счётчик
  «заберёт из плана», Tk-окно без редактора. В приоритете категорий первой
  строкой — правило для файла из окна. В абзаце 3.7 про `overrides.json` —
  что он по-прежнему только читается, а правила из окна пишутся в свой файл.
- [ ] **Шаг 3. Коммит:** `README: правила из окна`

---

### Задача 9. Сборка и живая проверка

- [ ] **Шаг 1.** `python -m pytest -q` — зелёный; записать число тестов в README.
- [ ] **Шаг 2.** Пересобрать exe тем же способом, что прежние релизы (`Сортировщик.spec`,
  затем `installer.iss`), номер версии поднять на минорную.
- [ ] **Шаг 3.** Запустить собранный exe на копии папки загрузок: добавить категорию и
  слово, убедиться, что план сменился; правый клик → «Всегда класть в»; закрыть,
  открыть — правки на месте; `my_rules.json` появился рядом с `config.json`.
  Кадры для README — только скриптом через `QWidget.grab()`, не снимком экрана.
- [ ] **Шаг 4.** Коммит сборочных правок, пуш в Gitea, затем публикация на GitHub
  по общей схеме.
