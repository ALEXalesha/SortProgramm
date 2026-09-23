"""Свойства правок из окна: то, что обязано держаться при любых действиях.

Юнит-тесты стерегут придуманные автором сценарии. Здесь hypothesis строит
случайные правила программы и случайные цепочки действий человека — удачных и
отказавших вперемешку — и после каждой проверяет договор целиком.
"""
import copy
import json

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
# краям, запасная категория в другом регистре, вложенная категория.
ANY_NAME = st.one_of(NAME, st.sampled_from(
    ["", " ", "C:/x", "a?b", "x ", " x", "..", "OTHERS", "others", "a/b", "a\x01"]),
    st.text(max_size=6))
WORD = st.text(LETTERS + " -", min_size=1, max_size=7)
FILE = st.text(LETTERS, min_size=1, max_size=5)


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
    patterns = {n: [f"^{n}_[0-9]+$"] for n in names if draw(st.booleans())}
    files = draw(st.lists(FILE, max_size=4))
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
        st.tuples(st.just("set_file"), FILE, name),
        st.tuples(st.just("clear_file"), FILE),
    ), max_size=25)


@st.composite
def scenario(draw):
    base = draw(bases())
    return base, draw(actions([*base.categories, "Others"]))


def consistent(base, edits):
    m = ur.merge(base, edits)
    keys = [folder_key(n) for n in m.categories]
    assert len(keys) == len(set(keys)), "две категории с одним именем"
    managed = set(map(folder_key, m.managed_folders))
    for name in m.categories:
        parts = [p for p in name.replace("\\", "/").split("/") if p]
        assert all(folder_key(p) in managed for p in parts), f"«{name}» не своя папка"
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


@settings(max_examples=150, deadline=None,
          suppress_health_check=[HealthCheck.too_slow,
                                 HealthCheck.function_scoped_fixture])
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
                assert target and ur._has_word(after.categories[target], word), (
                    f"слово «{word}» категории «{name}» потерялось при обновлении")
    gone = set(map(folder_key, [*edits["removed"], *edits["renamed"]]))
    for name in upstream.categories:
        if folder_key(name) not in gone:
            assert ur._find(after.categories, name), f"«{name}» из обновления не приехала"
    consistent(upstream, edits)


@RUN
@given(scenario(), st.data())
def test_file_rule_always_wins(case, data):
    base, steps = case
    edits = run(base, steps)
    m = ur.merge(base, edits)
    assume(m.categories)          # цепочка могла удалить все категории
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
    assume(m.categories)          # цепочка могла удалить все категории
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
    order = list(m.categories)
    earlier = order[:order.index(category)]
    stolen = any(w.strip() and w.lower() in filename.lower()
                 for c in earlier for w in m.categories[c])
    assert got == category or stolen


@settings(max_examples=300, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.binary(max_size=200))
def test_any_garbage_in_the_file_does_not_crash_loading(tmp_path_factory, raw):
    path = tmp_path_factory.mktemp("g") / ur.USER_RULES_FILENAME
    path.write_bytes(raw)
    edits, unreadable = ur.read(path, [])
    assert set(edits) == set(ur.empty())
    assert unreadable or isinstance(edits["files"], dict)


JSONISH = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=5),
    lambda inner: st.lists(inner, max_size=3)
    | st.dictionaries(st.text(max_size=5), inner, max_size=3),
    max_leaves=12)


@settings(max_examples=300, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.dictionaries(st.sampled_from(["version", "categories", "renamed", "removed",
                                        "keep_folders", "files", "лишнее"]),
                       JSONISH, max_size=6))
def test_json_of_the_wrong_shape_is_cleaned_not_crashed(tmp_path_factory, raw):
    path = tmp_path_factory.mktemp("j") / ur.USER_RULES_FILENAME
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    edits, unreadable = ur.read(path, [])
    if not unreadable:
        base = ur.Base({"Учёба": ["задач"]}, {}, {}, ["Учёба", "Others"], "Others")
        consistent(base, edits)
