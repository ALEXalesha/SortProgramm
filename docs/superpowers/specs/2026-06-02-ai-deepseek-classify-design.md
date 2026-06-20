# Кнопка ИИ-классификации (DeepSeek) — дизайн

Дата: 2026-06-02

## Цель

Кнопка в окне, которая классифицирует ВСЕ файлы через DeepSeek (deepseek-chat),
результат пишет в overrides.json (кэш), дальше работает обычный конвейер.

## Поведение

- Кнопка «✨ ИИ» рядом с «Очистить».
- Берём имена найденных файлов (только имена, не содержимое), шлём в DeepSeek
  вместе со списком категорий. Модель возвращает JSON имя->категория.
- Пишем в overrides.json, обновляем превью. Повторный показ мгновенный.
- Запрос в фоне (QThread), статус «Спрашиваю DeepSeek…». Ошибки/нет ключа — сообщение, не падаем.

## Сеть/модель

- deepseek-chat, эндпоинт https://api.deepseek.com/chat/completions, JSON-режим.
- Без новых зависимостей: POST через стандартный urllib.

## Ключ (безопасность)

- Файл deepseek_key.txt рядом с config (или env DEEPSEEK_API_KEY).
- НЕ включаем в установщик и портабл-zip, не коммитим (.gitignore).
- Локально кладём текущий ключ пользователя. Ключ засветился в чате — перевыпустить.

## Архитектура

- sorter/ai.py:
  - build_messages(filenames, categories) -> list[dict]
  - parse_ai_response(content, valid_categories) -> dict[str,str]
  - classify_with_ai(filenames, categories, api_key, model, base_url) -> dict  (HTTP)
  - load_api_key(base_dir) -> str | None
- ui_qt.py: кнопка + QThread-воркер + запись overrides + refresh.
- Ядро (classifier/scanner/planner/mover) не меняется.

## Тесты (TDD)

- build_messages содержит все категории.
- parse_ai_response: валидный JSON -> мапа; неизвестная категория -> Others; кривой JSON -> {}.

## После реализации

Пересобрать exe, портабл-zip (без deepseek_key.txt) и установщик.
