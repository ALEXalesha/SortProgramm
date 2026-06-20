"""Классификация файла: (категория, тип, расширение)."""
from __future__ import annotations

from .config import Config


def extension_of(filename: str) -> str:
    """Расширение в нижнем регистре без точки. Пустая строка, если его нет."""
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in name.strip("."):
        return ""
    return name.rsplit(".", 1)[-1].lower()


def match_type(extension: str, type_map: dict[str, list[str]], fallback: str = "Misc") -> str:
    """Тип файла по карте расширение→тип. Иначе fallback."""
    ext = extension.lower()
    for type_name, extensions in type_map.items():
        if ext in extensions:
            return type_name
    return fallback


def match_category(filename: str, content: str, categories: dict[str, list[str]]) -> str | None:
    """Категория по ключевым словам.

    Сначала ищем слово в имени файла, затем в содержимом. Сравнение —
    регистронезависимое вхождение подстроки. None, если ничего не подошло.

    >>> Это сердце логики. Порядок категорий в config задаёт приоритет:
    первая подошедшая выигрывает. Хочешь иначе (по границам слова,
    вес имени против содержимого) — менять здесь.
    """
    name = filename.lower()
    body = content.lower()
    for source in (name, body):
        for category, keywords in categories.items():
            if any(word.lower() in source for word in keywords):
                return category
    return None


def classify(filename: str, content: str, config: Config) -> tuple[str, str, str]:
    """Возвращает тройку (категория, тип, расширение).

    Категория решается по приоритету: overrides → ключевые слова → fallback.
    """
    category = (
        config.overrides.get(filename)
        or match_category(filename, content, config.categories)
        or config.fallback_category
    )
    extension = extension_of(filename)
    file_type = match_type(extension, config.type_map, config.fallback_type)
    return category, file_type, extension
