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
