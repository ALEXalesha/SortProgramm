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

# Сколько имён кладём в один запрос. На длинных списках модель начинает
# «экономить»: отвечает не про все файлы или срезает ответ на середине. Сорок
# имён — размер, на котором ответ стабильно полный, а запросов всё ещё немного.
BATCH_SIZE = 40

# Примеры делают больше, чем любые объяснения: показывают формат ответа и разбор
# ровно тех случаев, где модель ошибается сама. Первые два — из реальной папки
# загрузок: рендер Blender по диапазону кадров и мир Minecraft без опознавательного
# имени. Третий закрывает частую ошибку — шрифт Lora не имеет отношения к LoRA.
EXAMPLE_INPUT = "0001-0250.mp4\nfluga/\nLora-Regular.ttf\ncuda_13.2.1_windows.exe"
EXAMPLE_OUTPUT = (
    '{"0001-0250.mp4": "3D", "fluga/": "Игры", '
    '"Lora-Regular.ttf": "Дизайн", "cuda_13.2.1_windows.exe": "Код"}'
)


def build_messages(
    filenames: list[str],
    categories: list[str],
    hints: dict[str, str] | None = None,
) -> list[dict]:
    """Системное + пользовательское сообщения для модели.

    hints — короткое описание каждой категории из config. Без них модель судит
    по одному лишь названию папки и стабильно путает соседей: «Программы» против
    «Код», «Дизайн» против «3D». Описание снимает этот спор.

    Имена папок приходят с косой чертой на конце — папку надо оценивать целиком,
    а не по расширению, которого у неё нет.
    """
    hints = hints or {}
    lines = [
        f"- {name}: {hints[name]}" if name in hints else f"- {name}"
        for name in categories
    ]
    system = (
        "Ты раскладываешь содержимое папки «Загрузки» по категориям.\n\n"
        "Категории:\n" + "\n".join(lines) + "\n\n"
        "Правила:\n"
        "1. Решай по смыслу имени: язык, расширение, версия, узнаваемый продукт.\n"
        "2. Имя, оканчивающееся на «/», — это папка. Оценивай её как единое целое.\n"
        "3. Ключ в ответе повторяй ровно как прислали, символ в символ, включая «/».\n"
        "4. Отвечай про все присланные имена и только про них. Ничего не выдумывай.\n"
        "5. Не уверен — ставь Others. Это лучше, чем угадать мимо.\n\n"
        "Формат ответа — один JSON-объект {\"имя\": \"Категория\"}, без пояснений.\n\n"
        "Пример\n"
        "Ввод:\n" + EXAMPLE_INPUT + "\n"
        "Ответ:\n" + EXAMPLE_OUTPUT
    )
    user = "Имена:\n" + "\n".join(filenames)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def batched(items: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    """Режет список на куски по size. Пустой список — ноль кусков."""
    if size < 1:
        raise ValueError("размер пачки должен быть положительным")
    return [items[i:i + size] for i in range(0, len(items), size)]


def _extract_json(content: str) -> str:
    """Достаёт JSON-объект из ответа (на случай ```json ... ``` или текста вокруг)."""
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return ""
    return content[start:end + 1]


def parse_ai_response(
    content: str,
    valid_categories: list[str],
    fallback: str = "Others",
    requested: list[str] | None = None,
) -> dict[str, str]:
    """Разбор ответа модели в мапу имя->категория. Незнакомая категория -> fallback.

    Косую черту с конца имени убираем: в запрос она уходит как пометка «это
    папка», а в overrides.json ключом должно быть чистое имя — иначе правило
    никогда не совпадёт с реальной папкой.

    Имя запасной категории берётся из правил, а не пишется здесь буквально.
    Раньше выдумка модели превращалась в «Others» жёстко, а `useful_rules`
    отсеивает незнание по имени из конфига. Стоило переименовать
    `fallback_category` — имена расходились, и в overrides.json уезжало
    правило `файл → Others`: категории с таким именем в правилах нет, в
    `managed_folders` тоже, значит папка `Загрузки/Others` больше никогда не
    разбирается и не убирается. Плюс правило имеет наивысший приоритет, то
    есть закрывает файлу дорогу в любую новую категорию навсегда.

    Название категории сверяется так же мягко и по той же причине. Модель
    отвечает `3d` вместо `3D` или `« Медиа »` с пробелами — ответ верный, а
    сверка строка в строку объявляла его незнакомой категорией и заменяла
    запасной, после чего фильтр «не сохранять незнание» (`useful_rules`) её
    выбрасывал. Снаружи это «без решения»: вопрос задан и оплачен, файл остался
    неразобранным, и понять, что модель ответила верно, было неоткуда.
    Возвращаем название в том написании, в каком оно стоит в правилах, — из
    него получится имя папки. Выдумку это не пропускает: незнакомое название
    по-прежнему становится запасной категорией.

    `requested` — имена, про которые спрашивали. Промт просит повторять ключ
    символ в символ, и это правило модель нарушает регулярно: приводит имя к
    нижнему регистру, теряет служебный номер, дописывает файлы, которых ей не
    присылали. Оба исхода плохи по-своему. Ключ не тем регистром не совпадёт
    ни с одним файлом — правило мёртвое, а окно всё равно отчитается «ИИ
    разложил N шт.»: счёт врёт, файл остался неразобранным, и понять это
    неоткуда. Выдуманное имя оседает в overrides.json навсегда, и стоит
    такому файлу однажды появиться в загрузках, он поедет по решению,
    принятому вслепую про другую папку. Поэтому ответ сверяется со списком:
    чужое отбрасываем, своё возвращаем в том написании, в каком спрашивали.
    Списка нет (разбор из тестов, ручной вызов) — сверять не с чем, берём как
    есть.

    Точное совпадение имени ищется первым, и это не мелочь. Раньше список
    спрошенных складывался в словарь по нижнему регистру, а `readme.md` и
    `README.md` из разных папок программы — два разных файла, и второе имя
    затирало первое. Ответ про один пропадал молча, второму доставалась чужая
    категория. Когда точного совпадения нет, а кандидатов по регистру
    несколько, ответ отбрасывается: на какой из файлов модель смотрела,
    неизвестно, и угаданное правило встало бы не на тот.
    """
    raw = _extract_json(content)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    known = {c.strip().lower(): c for c in valid_categories}
    asked_exactly: set[str] = set()
    asked_loosely: dict[str, list[str]] = {}
    if requested is not None:
        for value in requested:
            name = value.rstrip("/")
            asked_exactly.add(name)
            asked_loosely.setdefault(name.lower(), []).append(name)
    result: dict[str, str] = {}
    for name, cat in data.items():
        if not isinstance(name, str) or not isinstance(cat, str):
            continue
        key = name.rstrip("/")
        if not key:
            continue
        if requested is not None and key not in asked_exactly:
            same = asked_loosely.get(key.lower(), [])
            if len(same) != 1:
                continue
            key = same[0]
        result[key] = known.get(cat.strip().lower(), fallback)
    return result


# Чем пробуем читать `deepseek_key.txt`. Файл создаёт человек — как правило
# Блокнотом, а он предлагает и «UTF-8 с BOM», и «UTF-16 LE». Ключ внутри всё
# равно из латиницы и дефисов, так что дело только в том, чем его раскодировать.
# `utf-8-sig` первым: он читает и обычный UTF-8, и вариант с меткой в начале.
_KEY_ENCODINGS = ("utf-8-sig", "utf-16")


def load_api_key(base_dir: Path) -> str | None:
    """Ключ из переменной окружения или файла deepseek_key.txt рядом с программой.

    Читается осторожно, потому что ключ кладут руками. UTF-16 из Блокнота
    ронял `read_text(encoding="utf-8")` через UnicodeDecodeError — а это
    ValueError, не OSError, поэтому его не ловил никто по дороге. В окне такое
    исключение прилетает внутрь слота PyQt, где необработанное исключение гасит
    процесс целиком: нажатие «✨ИИ» закрывало программу молча.

    Метку BOM у «UTF-8 с BOM» тоже надо снять. Программа с ней не падала, но
    невидимый символ уезжал в заголовок Authorization, DeepSeek отвечал
    «неверный ключ», и найти причину было нельзя: в файле на вид ровно то,
    что выдал сайт.

    Файл, который не разобрать ничем (или который не открыть), — это «ключа
    нет». Окно на такой ответ говорит, куда его положить: подсказка на месте,
    программа жива.
    """
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    key_file = Path(base_dir) / KEY_FILENAME
    for encoding in _KEY_ENCODINGS:
        try:
            text = key_file.read_text(encoding=encoding).strip()
        except (OSError, ValueError):
            continue
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
    hints: dict[str, str] | None = None,
    fallback: str = "Others",
) -> dict[str, str]:
    """Один запрос к DeepSeek. Мапа имя->категория (через parse_ai_response).

    Для длинных списков используй classify_many — она режет их на пачки.
    """
    payload = {
        "model": model,
        "messages": build_messages(filenames, categories, hints),
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
    return parse_ai_response(content, categories, fallback, requested=filenames)


def useful_rules(mapping: dict[str, str], fallback: str = "Others") -> dict[str, str]:
    """Отбрасывает правила «в запасную категорию».

    Когда модель отвечает Others, она говорит «не знаю». Записать это правилом —
    значит заморозить незнание: правила стоят выше ключевых слов, поэтому такой
    файл больше никогда не попадёт в новую категорию, сколько её ни улучшай.
    Без правила файл и так уедет в Others — но уже по текущим правилам, а не по
    прошлогоднему «не знаю».

    Правила, поставленные руками, это не трогает: фильтр применяется только к
    ответу ИИ перед сохранением.
    """
    return {name: cat for name, cat in mapping.items() if cat != fallback}


def classify_many(
    filenames: list[str],
    categories: list[str],
    api_key: str,
    batch_size: int = BATCH_SIZE,
    on_progress=None,
    should_stop=None,
    classifier=classify_with_ai,
    **kwargs,
) -> dict[str, str]:
    """Классификация длинного списка пачками. Возвращает объединённую мапу.

    Упавшая пачка не топит остальные: её имена просто останутся без правила и
    поедут по обычным ключевым словам. Половина разложенных загрузок лучше, чем
    ошибка на весь список из-за одного таймаута.

    on_progress(готово, всего) — для полоски прогресса в интерфейсе.
    should_stop() — «хватит»: окно закрывают, и ждать оставшиеся пачки незачем.
    Спрашиваем между пачками, потому что запрос в полёте не прервать; дольше
    одного таймаута ожидание всё равно не затянется.
    classifier подменяется в тестах, чтобы не ходить в сеть.
    """
    batches = batched(filenames, batch_size)
    result: dict[str, str] = {}
    failures: list[Exception] = []
    for index, batch in enumerate(batches, start=1):
        if should_stop and should_stop():
            break
        try:
            result.update(classifier(batch, categories, api_key, **kwargs))
        except Exception as exc:
            failures.append(exc)
        if on_progress:
            on_progress(index, len(batches))

    # Упало всё и ничего не разобрано — это не «пустой ответ», а поломка:
    # неверный ключ, нет сети, сменился адрес. Молча вернуть {} значит показать
    # «ИИ разложил 0» и спрятать причину. Частичный успех важнее — его отдаём.
    if failures and not result:
        raise failures[0]
    return result
