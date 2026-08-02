"""Классификация файла: (категория, тип, расширение)."""
from __future__ import annotations

import re

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


def match_pattern(filename: str, patterns: dict[str, list[str]]) -> str | None:
    """Категория по регулярному выражению на имени файла. None, если не подошло.

    Точнее ключевых слов: ловит то, что подстрокой не выразить. Например
    рендеры Blender называются диапазоном кадров (`0001-0250.mp4`), а скриншоты —
    датой (`Снимок экрана 2026-05-28 185311.png`). Ни то, ни другое не описать
    словом-подстрокой, не задев кучу лишнего.

    Битое выражение тихо пропускаем: опечатка в config.json не должна ронять
    сортировку целиком.
    """
    for category, expressions in patterns.items():
        for expression in expressions:
            try:
                if re.search(expression, filename, re.IGNORECASE):
                    return category
            except re.error:
                continue
    return None


def classify(filename: str, content: str, config: Config) -> tuple[str, str, str]:
    """Возвращает тройку (категория, тип, расширение).

    Категория решается по приоритету:
    overrides → регулярки → ключевые слова → fallback.

    Регулярки идут раньше слов, потому что они конкретнее: `0001-0250.mp4` —
    точно рендер, а слово «mp4» в списке Медиа забрало бы его себе.
    """
    category = (
        config.overrides.get(filename)
        or match_pattern(filename, config.patterns)
        or match_category(filename, content, config.categories)
        or config.fallback_category
    )
    extension = extension_of(filename)
    file_type = match_type(extension, config.type_map, config.fallback_type)
    return category, file_type, extension
