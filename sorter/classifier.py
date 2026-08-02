"""Классификация файла: (категория, тип, расширение)."""
from __future__ import annotations

import re

from .config import Config


# Служебный номер, который программа приписывает при конфликте имён:
# `отчёт.pdf` -> `отчёт (1).pdf`.
_DEDUP_SUFFIX = re.compile(r"\s*\(\d+\)$")


def base_name(filename: str) -> str:
    """Имя без служебного номера ` (1)`, приписанного разрешением конфликтов.

    Номер вставляется перед расширением, то есть ровно туда, где кончается
    смысловая часть имени. Слова вроде `-fon.` или `.exe` из-за этого перестают
    совпадать, и переразложение уносит уже разложенный файл в Others — просто
    потому, что программа сама его когда-то переименовала.

    Регулярки в rules.json обходят это вручную, дописывая `(\\s*\\(\\d+\\))?`
    к каждому шаблону. Здесь то же самое делается один раз и для слов тоже.
    """
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        return _DEDUP_SUFFIX.sub("", filename)
    return _DEDUP_SUFFIX.sub("", stem) + dot + extension


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

    Слова, начинающиеся с точки (`.exe`, `.json`, `.gguf`), — это расширения, и
    по содержимому они не ищутся. Иначе заметка со строкой «скачай installer.exe»
    объявлялась бы программой: в тексте такие подстроки встречаются сплошь и
    рядом, а значат совсем не то, что в имени файла.

    >>> Это сердце логики. Порядок категорий в config задаёт приоритет:
    первая подошедшая выигрывает. Хочешь иначе (по границам слова,
    вес имени против содержимого) — менять здесь.
    """
    name = filename.lower()
    body = content.lower()
    for source, extensions_count in ((name, True), (body, False)):
        for category, keywords in categories.items():
            for word in keywords:
                word = word.lower()
                if word.startswith(".") and not extensions_count:
                    continue
                if word in source:
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


def explain_category(filename: str, content: str, config: Config) -> tuple[str, str]:
    """Категория и чем она выбрана: (категория, причина).

    Приоритет: overrides → регулярки → ключевые слова → fallback. Регулярки
    идут раньше слов, потому что они конкретнее: `0001-0250.mp4` — точно
    рендер, а слово «mp4» в списке Медиа забрало бы его себе.

    Причина нужна для предпросмотра. Когда переразложение двигает сотню уже
    разложенных файлов, «почему» важнее «куда»: по нему видно, сработало
    новое правило или старая запись в overrides.

    Разбирается имя без служебного номера (`base_name`), чтобы файл, который
    программа сама переименовала в `отчёт (1).pdf`, оставался тем же файлом.
    Правило под точное имя всё-таки ищется первым: если руки написали его
    именно для `отчёт (1).pdf`, значит так и хотели.
    """
    name = base_name(filename)
    override = config.overrides.get(filename) or config.overrides.get(name)
    if override:
        return override, "правило"

    by_pattern = match_pattern(name, config.patterns)
    if by_pattern:
        return by_pattern, "шаблон"

    by_word = match_category(name, content, config.categories)
    if by_word:
        return by_word, "слово"

    return config.fallback_category, "не опознан"


def classify(filename: str, content: str, config: Config) -> tuple[str, str, str]:
    """Возвращает тройку (категория, тип, расширение)."""
    category, _ = explain_category(filename, content, config)
    extension = extension_of(filename)
    file_type = match_type(extension, config.type_map, config.fallback_type)
    return category, file_type, extension
