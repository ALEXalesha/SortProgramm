from sorter.ai import build_messages, parse_ai_response


CATS = ["Учёба", "Нейросети", "Код", "3D", "Игры", "Дизайн", "Программы", "Others"]


def test_build_messages_lists_all_categories():
    msgs = build_messages(["a.pdf", "b.exe"], CATS)
    system = " ".join(m["content"] for m in msgs)
    for c in CATS:
        assert c in system
    # имена файлов попали в запрос
    assert "a.pdf" in system and "b.exe" in system


def test_parse_valid_json():
    content = '{"a.pdf": "Учёба", "model.gguf": "Нейросети"}'
    assert parse_ai_response(content, CATS) == {"a.pdf": "Учёба", "model.gguf": "Нейросети"}


def test_parse_unknown_category_becomes_others():
    content = '{"x.bin": "Музыка"}'
    assert parse_ai_response(content, CATS) == {"x.bin": "Others"}


def test_parse_json_wrapped_in_code_fence():
    content = '```json\n{"a.pdf": "Код"}\n```'
    assert parse_ai_response(content, CATS) == {"a.pdf": "Код"}


def test_parse_garbage_returns_empty():
    assert parse_ai_response("не json вообще", CATS) == {}


def test_parse_non_dict_returns_empty():
    assert parse_ai_response('["a", "b"]', CATS) == {}


def test_a_note_next_to_the_key_does_not_turn_into_a_codec_error(tmp_path):
    """Ключ кладут руками, и рядом с ним пишут заметки.

    Файл читался целиком: `sk-...` первой строкой и «ключ от 2 июня» второй
    уезжали в заголовок Authorization одной строкой. `urllib` кодирует
    заголовки в latin-1, кириллица туда не влезает, и человек получал
    «'latin-1' codec can't encode characters in position 27-30» — сообщение,
    в котором нет ни слова ни про ключ, ни про файл.

    Тот же фильтр уже стоял, но только на одной форме мусора: метку BOM и
    UTF-16 из Блокнота сняли, а заметку рядом — нет. Ключ — это одна строка,
    которую примет заголовок; всё остальное ключом не является.
    """
    from sorter.ai import load_api_key

    def key_from(body: str) -> str | None:
        case = tmp_path / str(abs(hash(body)))
        case.mkdir()
        (case / "deepseek_key.txt").write_text(body, encoding="utf-8")
        return load_api_key(case)

    assert key_from("sk-0123456789abcdef\nключ от 2 июня\n") == "sk-0123456789abcdef"
    assert key_from("ключ от 2 июня\nsk-0123456789abcdef") == "sk-0123456789abcdef"
    assert key_from("sk-0123456789abcdef\xa0") == "sk-0123456789abcdef"
    # Ключа в файле нет вовсе — окно скажет, куда его положить.
    assert key_from("сюда надо положить ключ") is None
    assert key_from("\n\n  \n") is None

    for body in ("sk-0123456789abcdef\nзаметка", "ключ: sk-abc", "заметка"):
        key = key_from(body)
        if key is not None:
            key.encode("latin-1")  # заголовок такое примет
