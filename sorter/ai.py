"""ИИ-классификация имён файлов через DeepSeek (OpenAI-совместимый API).

Изолировано от ядра. Возвращает мапу имя_файла -> категория, которую затем
кладут в overrides.json. Сетевой вызов отделён от разбора, чтобы разбор тестировался.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/chat/completions"
KEY_FILENAME = "deepseek_key.txt"


def build_messages(filenames: list[str], categories: list[str]) -> list[dict]:
    """Системное + пользовательское сообщения для модели."""
    cats = ", ".join(categories)
    system = (
        "Ты сортируешь файлы из папки загрузок по категориям. "
        "Доступные категории: " + cats + ". "
        "Определи категорию по имени файла (язык, расширение, смысл). "
        "Если не подходит ни одна — используй Others. "
        "Ответь СТРОГО одним JSON-объектом вида {\"имя файла\": \"Категория\"} "
        "для всех присланных файлов, без пояснений."
    )
    user = "Файлы:\n" + "\n".join(filenames)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_json(content: str) -> str:
    """Достаёт JSON-объект из ответа (на случай ```json ... ``` или текста вокруг)."""
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return ""
    return content[start:end + 1]


def parse_ai_response(content: str, valid_categories: list[str]) -> dict[str, str]:
    """Разбор ответа модели в мапу имя->категория. Неизвестная категория -> Others."""
    raw = _extract_json(content)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    valid = set(valid_categories)
    result: dict[str, str] = {}
    for name, cat in data.items():
        if not isinstance(name, str) or not isinstance(cat, str):
            continue
        result[name] = cat if cat in valid else "Others"
    return result


def load_api_key(base_dir: Path) -> str | None:
    """Ключ из переменной окружения или файла deepseek_key.txt рядом с программой."""
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    key_file = Path(base_dir) / KEY_FILENAME
    if key_file.exists():
        text = key_file.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def classify_with_ai(
    filenames: list[str],
    categories: list[str],
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = 60,
) -> dict[str, str]:
    """Запрос к DeepSeek. Возвращает мапу имя->категория (через parse_ai_response)."""
    payload = {
        "model": model,
        "messages": build_messages(filenames, categories),
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return parse_ai_response(content, categories)
