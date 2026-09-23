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


# --- операции ---
#
# Каждая принимает правки и правила программы и возвращает НОВЫЕ правки либо
# бросает ValueError с фразой, которую окно показывает как есть. Исходные
# правки не меняются никогда: отказ не должен оставлять полуправку.

def _merged(base: Base, edits: dict) -> Merged:
    return merge(base, edits, [])


def _category(merged: Merged, name) -> str:
    found = _find(merged.categories, name) if isinstance(name, str) else None
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
