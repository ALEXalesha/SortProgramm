"""Пачки запросов к ИИ, подсказки категорий и разбор имён папок."""
from sorter.ai import (
    batched,
    build_messages,
    classify_many,
    parse_ai_response,
    useful_rules,
)

CATS = ["Учёба", "Код", "3D", "Игры", "Медиа", "Others"]
HINTS = {"Код": "программирование: SDK, IDE, репозитории"}


# --- нарезка ---


def test_batched_splits_evenly():
    assert batched(["a", "b", "c", "d"], 2) == [["a", "b"], ["c", "d"]]


def test_batched_keeps_remainder():
    assert batched(["a", "b", "c"], 2) == [["a", "b"], ["c"]]


def test_batched_empty_list_has_no_batches():
    assert batched([], 40) == []


# --- промт ---


def test_hints_reach_the_prompt():
    system = build_messages(["a.py"], CATS, HINTS)[0]["content"]
    assert "программирование: SDK, IDE, репозитории" in system


def test_prompt_without_hints_still_lists_categories():
    system = build_messages(["a.py"], CATS)[0]["content"]
    for category in CATS:
        assert category in system


def test_prompt_explains_folder_slash():
    """Модель должна понимать, что `имя/` — папка, а не файл без расширения."""
    system = build_messages(["fluga/"], CATS)[0]["content"]
    assert "папка" in system.lower()


def test_prompt_carries_example():
    system = build_messages(["a.py"], CATS)[0]["content"]
    assert "0001-0250.mp4" in system


# --- разбор ---


def test_folder_slash_stripped_from_keys():
    """Ключ override сравнивается с именем папки — косой черты там нет."""
    assert parse_ai_response('{"fluga/": "Игры"}', CATS) == {"fluga": "Игры"}


def test_bare_slash_key_ignored():
    assert parse_ai_response('{"/": "Игры"}', CATS) == {}


# --- какие ответы стоит записывать правилом ---


def test_others_is_not_saved_as_a_rule():
    """«Others» от модели — это «не знаю». Правило заморозило бы незнание."""
    assert useful_rules({"a.py": "Код", "x.bin": "Others"}) == {"a.py": "Код"}


def test_useful_rules_respects_custom_fallback():
    assert useful_rules({"a": "Разное", "b": "Код"}, fallback="Разное") == {"b": "Код"}


def test_useful_rules_keeps_everything_when_model_was_sure():
    mapping = {"a.py": "Код", "b.mp3": "Медиа"}
    assert useful_rules(mapping) == mapping


# --- сборка пачек ---


def test_classify_many_merges_batches():
    calls = []

    def fake(names, categories, api_key, **kwargs):
        calls.append(list(names))
        return {name: "Код" for name in names}

    result = classify_many(["a", "b", "c"], CATS, "key", batch_size=2, classifier=fake)
    assert result == {"a": "Код", "b": "Код", "c": "Код"}
    assert calls == [["a", "b"], ["c"]]


def test_failed_batch_does_not_sink_the_rest():
    """Один таймаут не должен обнулять всю разложенную папку."""

    def flaky(names, categories, api_key, **kwargs):
        if "b" in names:
            raise TimeoutError("сеть")
        return {name: "Код" for name in names}

    result = classify_many(["a", "b", "c"], CATS, "key", batch_size=1, classifier=flaky)
    assert result == {"a": "Код", "c": "Код"}


def test_progress_reports_every_batch():
    seen = []
    result = classify_many(
        ["a", "b", "c", "d"],
        CATS,
        "key",
        batch_size=2,
        on_progress=lambda done, total: seen.append((done, total)),
        classifier=lambda names, c, k, **kw: {},
    )
    assert seen == [(1, 2), (2, 2)]
    assert result == {}
