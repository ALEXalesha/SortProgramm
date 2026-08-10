"""Свойства раскладки на случайных папках: то, что обязано быть верным всегда.

Эти тесты появились после сессии, в которой выяснилось неприятное: починка,
дописанная несколькими сессиями раньше, не выполнялась НИ РАЗУ. `apply` отводил
файл в сторону и возвращал его назад под условием `not mv.src.exists()` — а
строкой выше `mkdir` сам создавал этот путь папкой. Условие ложно всегда, ветка
стояла мёртвой ровно в том случае, ради которого её писали. Комментарий на
месте, выглядит осторожно, в обзоре не видно.

Причина, по которой это прожило так долго, измерима. Набор из четырёх с лишним
сотен тестов и случайные раскладки давали `errors: 0, notes: 0` — вся половина
`mover`, отвечающая за столкновения, неудачи и восстановление, на случайных
данных не запускалась вообще. Тесты на найденные баги (`test_bugfixes.py`)
закрепляют конкретную поломку, но не класс: они ходят по тем же путям, по
которым однажды прошли.

Поэтому здесь проверяются не случаи, а свойства, и папку нарочно портят между
шагами — как это делает жизнь между «Очистить» и «Применить»: файл дописался,
скачался второй с тем же именем, папку унесли, диск отвалился.

Сколько раскладок гонять и с какого места — задаётся снаружи:

    SORTER_PROP_SEEDS=200 pytest tests/test_properties.py        # глубже
    SORTER_PROP_OFFSET=1000 pytest tests/test_properties.py      # целина

По умолчанию сиды одни и те же, чтобы падение воспроизводилось дословно. Смена
`SORTER_PROP_OFFSET` — способ проверить новое поле, а не перепахивать старое:
если на свежих сидах ничего не находится, это уже результат, а не отговорка.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import pytest

from sorter import history
from sorter.config import Config
from sorter.mover import apply
from sorter.planner import build_plan

SEEDS = int(os.environ.get("SORTER_PROP_SEEDS", "20"))
OFFSET = int(os.environ.get("SORTER_PROP_OFFSET", "0"))


def seeds(salt: int) -> list[int]:
    """Свой ряд сидов на каждое свойство, чтобы они не смотрели одно и то же."""
    return [OFFSET + salt * 100_000 + i for i in range(SEEDS)]


# --- из чего строится случайная папка ---------------------------------------

CATEGORIES = {
    "Учёба": ["задач", "класс"],
    "Медиа": ["клип", "музык"],
    "3D": [".stl", ".gcode", "blender"],
}
TYPE_MAP = {
    "Documents": ["txt", "md", "csv"],
    "Videos": ["mp4"],
    "3D": ["stl", "gcode"],
}
# «3D» стоит и в категориях, и в типах — как в поставляемых правилах.
MANAGED = ["Учёба", "Медиа", "3D", "Others", "Documents", "Videos", "Misc"]

# Имён нарочно мало, а расширений — наоборот: тёзки в разных папках это и есть
# вся возня с номерами ` (1)`. Имена `Медиа`, `3D`, `Others` без расширения —
# те самые файлы, которые загораживают дорогу собственной категории.
NAMES = ["задача", "клип", "фон", "Медиа", "3D", "Others", "заметка"]
EXTS = ["txt", "csv", "md", "mp4", "stl", "gcode", ""]


def make_config(root: Path, all3d: Path | None) -> Config:
    return Config(
        downloads_path=str(root),
        categories={k: list(v) for k, v in CATEGORIES.items()},
        patterns={},
        type_map={k: list(v) for k, v in TYPE_MAP.items()},
        managed_folders=list(MANAGED),
        ignore=["*.tmp"],
        overrides={},
        external_3d={"enabled": bool(all3d), "extensions": ["stl", "gcode"],
                     "path": str(all3d) if all3d else ""},
        fallback_category="Others",
        fallback_type="Misc",
    )


def build_layout(rng: random.Random, root: Path, all3d: Path | None) -> None:
    """Папка загрузок со всем, что в ней бывает.

    Порядок важен: сначала подпапки, потом файлы корня. Файл с именем категории
    и папка с тем же именем на диске не уживаются, а нужны оба случая — просто
    в разных прогонах.
    """
    root.mkdir(parents=True)
    if all3d:
        all3d.mkdir(parents=True)
    serial = [0]

    def spawn(folder: Path, forced: str | None = None) -> None:
        serial[0] += 1
        name = forced if forced is not None else rng.choice(NAMES)
        ext = "" if forced is not None else rng.choice(EXTS)
        target = folder / (f"{name}.{ext}" if ext else name)
        n = 1
        while target.exists():
            target = folder / (f"{name} ({n}).{ext}" if ext else f"{name} ({n})")
            n += 1
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target.write_text(f"тело-{serial[0]}", encoding="utf-8")
        except OSError:
            serial[0] -= 1  # имя занято папкой — этот файл просто не родился

    for _ in range(rng.randint(0, 4)):          # уже разложенное
        spawn(root / rng.choice(["Учёба", "Медиа", "3D", "Others"])
              / rng.choice(["Documents", "Videos", "Misc", "3D"]))
    if rng.random() < 0.5:                      # чужая папка — не трогать
        spawn(root / "чужая-папка")
    if rng.random() < 0.4:                      # ручная раскладка внутри своей
        spawn(root / "Учёба" / "9 класс")
    if all3d:
        for _ in range(rng.randint(0, 2)):
            spawn(all3d / rng.choice(["stl", "gcode"]))
        for _ in range(rng.randint(0, 3)):
            spawn(all3d)
    for _ in range(rng.randint(1, 9)):
        spawn(root)
    # Файл, занявший путь СОБСТВЕННОЙ цели (`Others` едет в `Others/Misc/Others`),
    # разбирается отдельной веткой с отводом в сторону — и родится сам собой
    # примерно раз в полсотни раскладок. На такой редкости ветка проверяется
    # раз в сотню прогонов, то есть практически никогда: ровно поэтому в ней и
    # прожила несколько сессий починка, которая не выполнялась ни разу.
    # Подкидываем его нарочно.
    if rng.random() < 0.4:
        spawn(root, forced=rng.choice(["Others", "Медиа", "3D"]))


def sandbox(rng: random.Random, tmp_path: Path) -> tuple[Config, Path, Path | None]:
    root = tmp_path / "загрузки"
    all3d = tmp_path / "All_3d" if rng.random() < 0.5 else None
    build_layout(rng, root, all3d)
    config = make_config(root, all3d)
    # Пробник обязан убедиться, что работает в песочнице: `Config` собирается
    # руками, но одна опечатка — и мы раскладываем настоящие загрузки.
    assert str(tmp_path) in config.downloads_path
    return config, root, all3d


def layout(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".sorter" not in p.parts
    }


def bodies(*roots: Path | None) -> list[str]:
    """Содержимое всех файлов, без имён: что переименовано — видно отдельно."""
    out = []
    for root in roots:
        if root is None or not root.exists():
            continue
        out += [p.read_text(encoding="utf-8") for p in root.rglob("*")
                if p.is_file() and ".sorter" not in p.parts]
    return sorted(out)


def journal(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# --- порча состояния между шагами -------------------------------------------


def spoil_before_apply(rng: random.Random, moves) -> None:
    """Между «Очистить» и «Применить» проходит сколько угодно времени."""
    for mv in moves:
        roll = rng.random()
        try:
            if roll < 0.18:                     # цель занял другой файл
                mv.dst.parent.mkdir(parents=True, exist_ok=True)
                mv.dst.write_text("чужак", encoding="utf-8")
            elif roll < 0.30:                   # источник унесли руками
                mv.src.unlink()
            elif roll < 0.38:                   # на месте папки назначения файл
                blocker = mv.dst.parent
                blocker.parent.mkdir(parents=True, exist_ok=True)
                if not blocker.exists():
                    blocker.write_text("пробка", encoding="utf-8")
            elif roll < 0.44:                   # цель заняли папкой
                mv.dst.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def spoil_before_undo(rng: random.Random, entries: list[dict]) -> None:
    """Между сортировкой и «↩ Отменить» — тем более."""
    for e in entries:
        src, dst = Path(e["src"]), Path(e["dst"])
        roll = rng.random()
        try:
            if roll < 0.15 and dst.exists():
                dst.unlink()
            elif roll < 0.30 and not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                src.write_text("тёзка", encoding="utf-8")
            elif roll < 0.42 and not src.exists():
                src.mkdir(parents=True)
                (src / "чужое.txt").write_text("чужое", encoding="utf-8")
            elif roll < 0.50 and dst.exists():
                dst.rename(dst.with_name(dst.name + ".переименован"))
        except OSError:
            pass


# --- свойства ---------------------------------------------------------------


@pytest.mark.parametrize("seed", seeds(1))
def test_apply_moves_files_without_losing_or_renaming_them(seed, tmp_path):
    """Уборка на случайной папке: ничего не пропало, ничего лишнего не названо.

    Собраны в одно свойство пять обещаний сразу, потому что нарушить их можно
    только все вместе — раскладка либо цела, либо нет.
    """
    rng = random.Random(seed)
    config, root, all3d = sandbox(rng, tmp_path)
    deep = rng.random() < 0.5
    to3d = bool(all3d)

    before, before_bodies = layout(root), bodies(root, all3d)
    born_with = {Path(k).name for k in before}
    foreign = layout(root / "чужая-папка") if (root / "чужая-папка").is_dir() else None
    handmade = (layout(root / "Учёба" / "9 класс")
                if (root / "Учёба" / "9 класс").is_dir() else None)

    moves = build_plan(config, send_3d_external=to3d, deep=deep)
    result = apply(moves, config, dry_run=False)

    assert result.moved + len(result.errors) == result.planned, (
        "каждое перемещение либо прошло, либо названо в ошибках")
    assert bodies(root, all3d) == before_bodies, "содержимое файлов изменилось"
    if foreign is not None:
        assert layout(root / "чужая-папка") == foreign, "тронута чужая папка"
    if handmade is not None:
        assert layout(root / "Учёба" / "9 класс") == handmade, (
            "тронута ручная подпапка внутри своей")

    # Служебный номер ` (1)` — это оговорка про столкновение. Свободное имя
    # рядом с ним значит, что столкновения не было, а номер файл получил
    # навсегда: разбор его снимает, и следующая уборка ничего не поправит.
    for base in (root, all3d):
        if base is None:
            continue
        for p in base.rglob("*"):
            if not p.is_file() or ".sorter" in p.parts or p.name in born_with:
                continue
            m = re.match(r"^(.*) \(\d+\)$", p.stem)
            assert not (m and not (p.parent / (m.group(1) + p.suffix)).exists()), (
                f"номер за столкновение, которого нет: {p}")

    assert build_plan(config, send_3d_external=to3d, deep=deep) == [], (
        "план не идемпотентен: повторная уборка снова что-то двигает")
    assert len(journal(result.undo_log) if result.undo_log else []) == result.moved


@pytest.mark.parametrize("seed", seeds(2))
def test_report_tells_the_truth_when_the_folder_changed_under_it(seed, tmp_path):
    """План строился по одному состоянию папки, применяется по другому.

    Проверяется не раскладка, а ОТЧЁТ: правда ли то, что программа о себе
    рассказала. Именно это свойство поднимает ветки ошибок и оговорок с нуля до
    сотен — без порчи состояния они на случайных данных не выполняются вовсе.
    """
    rng = random.Random(seed)
    config, root, all3d = sandbox(rng, tmp_path)
    moves = build_plan(config, send_3d_external=bool(all3d), deep=rng.random() < 0.5)
    planned_dst = {str(mv.src): mv.dst for mv in moves}
    spoil_before_apply(rng, moves)
    before_bodies = bodies(root, all3d)
    # Имена вида `задача (1).csv` бывают и у обычных файлов — их пишет и сам
    # генератор, и человек. Придуманным программой считается только то, чего до
    # уборки на диске не было.
    existed = {str(p) for base in (root, all3d) if base is not None
               for p in base.rglob("*") if p.is_file()}

    result = apply(moves, config, dry_run=False)

    assert result.moved + len(result.errors) == result.planned
    assert not set(before_bodies) - set(bodies(root, all3d)), "содержимое пропало"

    entries = journal(result.undo_log) if result.undo_log else []
    assert len(entries) == result.moved
    moved_srcs = {Path(e["src"]) for e in entries}
    for e in entries:
        assert Path(e["dst"]).exists(), f"журнал обещает файл, которого нет: {e}"
        # Папка на месте уехавшего файла — норма: `Медиа` без расширения
        # уезжает, а следом там создаётся категория `Медиа`.
        assert not (Path(e["src"]).is_file() and Path(e["src"]) != Path(e["dst"])), (
            f"исходный путь всё ещё занят файлом: {e['src']}")

    failed = {src for src, _ in result.errors}
    for src in failed:
        assert Path(src) not in moved_srcs, "файл и в ошибках, и в журнале"
        # Файл, отведённый в сторону, не должен остаться лежать под номером,
        # о котором в отчёте не сказано ни слова.
        p = Path(src)
        aside = p.with_name(f"{p.stem} (1){p.suffix}")
        assert not (aside.exists() and str(aside) not in existed and not p.exists()), (
            f"файл брошен в стороне: {aside}")

    for src, why in result.notes:
        assert src not in failed, "файл и в оговорках, и в ошибках"
        if "положили как «" in why:
            name = why.split("положили как «", 1)[1].rstrip("»")
            assert planned_dst[src].with_name(name).exists(), (
                f"оговорка называет имя, которого на диске нет: {name}")


@pytest.mark.parametrize("seed", seeds(5))
def test_nothing_is_left_under_a_name_the_program_invented_silently(seed, tmp_path):
    """Перемещения падают вперемешку — и ни один файл не остаётся безымянным.

    Свойство ровно про тот класс, из-за которого этот файл и появился. Имена
    вроде `Others (1)` придумывает сама программа: `_free_name` — при
    столкновении, `apply` — когда отводит в сторону файл, занявший путь
    собственной цели. Придуманное имя обязано быть где-то названо: в журнале
    отмены, в оговорке или в списке ошибок. Файл, лежащий под именем, которого
    человек не давал и нигде не видел, — потерянный файл, даже если он цел.

    Именно так и жила мёртвая ветка возврата: `apply` не возвращал отведённый в
    сторону файл (проверка «путь свободен» смотрела на папку, созданную своим же
    `mkdir`), а в отчёте называл исходное имя. Файл лежал рядом как `Others (1)`,
    и в отчёте этого имени не было ни разу. Обычные тесты сюда не заходят: нужна
    неудача перемещения ПОСЛЕ удачного отвода в сторону.
    """
    import shutil

    rng = random.Random(seed)
    config, root, all3d = sandbox(rng, tmp_path)
    moves = build_plan(config, send_3d_external=bool(all3d), deep=rng.random() < 0.5)
    before = {**layout(root), **(layout(all3d) if all3d else {})}
    before_bodies = bodies(root, all3d)
    roots = [r for r in (root, all3d) if r is not None]
    was_here = {str(r / k) for r in roots for k in before}

    real = shutil.move

    def flaky(src, dst):
        if rng.random() < 0.3:
            raise OSError("диск отвалился на полпути")
        return real(src, dst)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(shutil, "move", flaky)
    try:
        result = apply(moves, config, dry_run=False)
    finally:
        monkeypatch.undo()

    assert result.moved + len(result.errors) == result.planned
    assert bodies(root, all3d) == before_bodies, "содержимое пропало или размножилось"

    landed = {e["dst"] for e in (journal(result.undo_log) if result.undo_log else [])}
    spoken = "\n".join(
        [str(s) for s, _ in result.errors] + [str(s) for s, _ in result.notes]
        + [why for _, why in result.errors + result.notes])
    for base in roots:
        for p in base.rglob("*"):
            if not p.is_file() or ".sorter" in p.parts:
                continue
            if str(p) in was_here or str(p) in landed:
                continue
            assert p.name in spoken, (
                f"файл лежит под именем, которого никто не давал и которое "
                f"нигде не названо: {p}")


@pytest.mark.parametrize("seed", seeds(3))
def test_two_sorts_undo_back_to_the_original_layout_in_any_order(seed, tmp_path):
    """Две сортировки подряд, откат в случайном порядке — раскладка та же.

    Порядок работы самый обычный: прибрались, поправили правила, нажали
    «Переразложить старое». В «🕘 Истории» две строки, и человек жмёт любую.
    Записи, до которых очередь ещё не дошла, остаются в журнале нарочно —
    повторная попытка после отката верхней строки обязана их доиграть.
    """
    rng = random.Random(seed)
    config, root, all3d = sandbox(rng, tmp_path)
    to3d = bool(all3d)
    origin = layout(root), (layout(all3d) if all3d else {})

    first = apply(build_plan(config, send_3d_external=to3d), config, dry_run=False)
    second = apply(build_plan(config, send_3d_external=to3d, deep=True),
                   config, dry_run=False)

    ops = history.list_operations(root)
    if len(ops) == 2:
        assert (ops[0].when, ops[0].serial) >= (ops[1].when, ops[1].serial), (
            "история не отсортирована: старая сортировка сверху")

    order = list(range(len(ops)))
    rng.shuffle(order)
    for i in order:
        op = ops[i]
        if not op.log_path.exists():
            continue
        was = bodies(root, all3d)
        left_before = journal(op.log_path)
        notes = history.undo_operation(op, config)
        assert not set(was) - set(bodies(root, all3d)), "откат потерял содержимое"
        left_after = journal(op.log_path) if op.log_path.exists() else []
        assert len(left_after) <= len(left_before), "журнал после отката вырос"
        # Запись ушла из журнала — значит откат с ней и правда закончил.
        named = {n for n, _ in notes}
        for e in (x for x in left_before if x not in left_after):
            assert (Path(e["src"]).exists() or e["src"] in named
                    or e["dst"] in named), (
                f"запись стёрта, файла дома нет, оговорки нет: {e}")

    # Недоигранные записи доигрываются повтором — так и жмут кнопку второй раз.
    for _ in range(6):
        rest = history.list_operations(root)
        if not rest:
            break
        moved = False
        for op in rest:
            was = bodies(root, all3d)
            history.undo_operation(op, config)
            moved = moved or bodies(root, all3d) != was or not op.log_path.exists()
        if not moved:
            break

    if not first.errors and not second.errors:
        assert (layout(root), (layout(all3d) if all3d else {})) == origin, (
            "после всех откатов раскладка не совпала с исходной")
        assert history.list_operations(root) == [], "в истории что-то осталось"


# --- канал жалоб наружу ------------------------------------------------------


COMPLAINING = {
    "scan": "sorter.scanner",
    "scan_3d": "sorter.scanner",
    "is_extension_folder": "sorter.scanner",
    "build_plan": "sorter.planner",
    "list_operations": "sorter.history",
}


def test_every_caller_asks_for_the_complaints():
    """Кто зовёт обход, тот обязан забрать у него список жалоб.

    Папку, которую не удалось прочитать, обход пропускает — падать посреди
    списка нельзя, — но говорит о ней вслух через `problems`. Если читатель
    список не забрал, её файлы исчезают молча, и ноль в ответе становится
    неотличим от прибранной папки. README называет это ошибкой трижды.

    Чинили это по одному читателю за раз: сперва обход научили складывать
    жалобы, потом `build_plan` — доносить их до трёх интерфейсов. А кнопка
    «✨ ИИ» зовёт обход НАПРЯМУЮ, четвёртым читателем, и список не забирала:
    «Нечего разбирать» на папке, полную файлов которой окно не смогло открыть.
    Соседняя кнопка в ту же секунду говорила «Не прочитано папок: 1».

    Проверка статическая нарочно: она ловит не поведение, а появление ПЯТОГО
    читателя. Тест на поведение пришлось бы писать заново на каждого нового, то
    есть ровно тогда, когда о нём уже вспомнили.
    """
    import ast
    import importlib
    import inspect

    where = {name: inspect.signature(
        getattr(importlib.import_module(mod), name)) for name, mod in COMPLAINING.items()}
    index = {name: list(sig.parameters).index("problems")
             for name, sig in where.items()}

    root = Path(__file__).resolve().parent.parent
    missing = []
    for path in [*(root / "sorter").glob("*.py"), root / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(
                func, "id", None)
            if name not in COMPLAINING:
                continue
            got_keyword = any(kw.arg == "problems" for kw in node.keywords)
            got_positional = len(node.args) > index[name]
            if not (got_keyword or got_positional):
                missing.append(f"{path.name}:{node.lineno} — {name}(...)")

    assert not missing, (
        "эти читатели зовут обход, но список жалоб не забирают — папка, которую "
        "не удалось прочитать, исчезнет у них молча:\n  " + "\n  ".join(missing))


# --- разбор чужого ввода -----------------------------------------------------


def test_ai_answer_parser_never_invents_a_name_or_a_category():
    """Ответ модели — чужой ввод, и он уезжает ключом в `overrides.json`.

    Выдуманное имя оседает там навсегда и однажды решит судьбу файла, про
    который никто не спрашивал. Выдуманная категория станет именем папки.
    """
    from sorter.ai import parse_ai_response, useful_rules

    cats = ["Медиа", "Учёба", "3D", "Others"]
    names = ["клип.mp4", "клип (1).mp4", "КЛИП.MP4", "задача.pdf", "папка/", "", "  "]
    junk = [
        "", "не json", "{}", "[]", "null", "42", '{"a":', "```json\n{}\n```",
        '{"клип.mp4": "Медиа"}', '{"клип.mp4": 5}', '{"клип.mp4": null}',
        '{"": "Медиа"}', '{"/": "Медиа"}', '{"клип.mp4": "выдумка"}',
        '{"чужое.txt": "Медиа"}', '{"КЛИП.MP4": "медиа"}', '{"клип.mp4": " 3d "}',
        'мусор {"клип.mp4": "Учёба"} хвост', '{"a":1}{"b":2}',
        '{"клип.mp4": "Others"}', '{"папка/": "3D"}',
    ]
    rng = random.Random(20260810)
    for _ in range(600):
        content = rng.choice(junk)
        asked = rng.sample(names, rng.randint(1, 4))
        fallback = rng.choice(["Others", "Прочее"])
        known = cats + ([fallback] if fallback not in cats else [])
        out = parse_ai_response(content, known, fallback, requested=asked)
        allowed = {n.rstrip("/") for n in asked}
        for key, value in out.items():
            assert key in allowed, f"ответ про имя, о котором не спрашивали: {key!r}"
            assert value in known, f"категория, которой нет в правилах: {value!r}"
        assert fallback not in useful_rules(out, fallback).values(), (
            "«не знаю» записалось правилом и закрыло файлу дорогу навсегда")


def test_settings_parser_survives_any_junk_and_keeps_foreign_keys(tmp_path):
    """`rules.json` и `config.json` правят руками, значит там бывает что угодно.

    До `Path()` и до `.items()` обязано доезжать либо то, что там ожидают, либо
    ничего — и сохранение не вправе стирать чужие ключи, дописанные в файл.
    """
    from sorter.config import RULE_KEYS, USER_KEYS

    values = [None, 0, 1, True, False, "", " ", "текст", [], {}, [1, 2], {"a": 1},
              ["", "  "], {"Медиа": "клип"}, {"Медиа": ["клип"]}, {"Медиа": []},
              {"C:/Windows": ["x"]}, {"Медиа ": ["клип"]}, {"..": ["x"]},
              "C:/Windows", ["Медиа", 5, ""], {"Медиа": [""]}]
    rng = random.Random(20260811)
    for i in range(250):
        case = tmp_path / f"c{i}"
        case.mkdir()
        settings = {k: rng.choice(values) for k in USER_KEYS if rng.random() < 0.75}
        settings["моё_поле"] = "не стирать"
        (case / "rules.json").write_text(json.dumps(
            {k: rng.choice(values) for k in RULE_KEYS if rng.random() < 0.75},
            ensure_ascii=False, default=str), encoding="utf-8")
        (case / "config.json").write_text(
            json.dumps(settings, ensure_ascii=False, default=str), encoding="utf-8")

        config = Config.load(case / "config.json")

        for name, want in (("categories", dict), ("patterns", dict),
                           ("type_map", dict), ("overrides", dict),
                           ("category_hints", dict), ("external_3d", dict),
                           ("managed_folders", list), ("ignore", list),
                           ("downloads_path", str), ("fallback_category", str),
                           ("fallback_type", str)):
            assert isinstance(getattr(config, name), want), f"{name} вышел не тем"
        for words in config.categories.values():
            assert all(isinstance(w, str) and w.strip() for w in words), (
                "пустое слово подходит к любому имени и заберёт всю папку")
        config.save(case / "config.json")
        after = json.loads((case / "config.json").read_text(encoding="utf-8"))
        assert after.get("моё_поле") == "не стирать", "сохранение стёрло чужой ключ"


@pytest.mark.parametrize("seed", seeds(4))
def test_undo_bookkeeping_survives_a_folder_stirred_by_hand(seed, tmp_path):
    """Папку поворошили между сортировкой и откатом: что сказано, то и сделано.

    Запись уходит из журнала, только когда откат с ней закончил, оговорки
    называют то, что есть на диске, и ничего не пропадает молча.
    """
    rng = random.Random(seed)
    config, root, all3d = sandbox(rng, tmp_path)
    result = apply(
        build_plan(config, send_3d_external=bool(all3d), deep=rng.random() < 0.5),
        config, dry_run=False)
    if not result.undo_log:
        pytest.skip("двигать было нечего — журнала нет")

    spoil_before_undo(rng, journal(result.undo_log))
    was = bodies(root, all3d)
    op = history.list_operations(root)[0]
    left_before = journal(op.log_path)

    notes = history.undo_operation(op, config)

    assert not set(was) - set(bodies(root, all3d)), "откат потерял содержимое"
    left_after = journal(op.log_path) if op.log_path.exists() else []
    assert len(left_after) <= len(left_before), "журнал после отката вырос"
    for e in left_after:
        assert e in left_before, f"в журнале появилась запись, которой не было: {e}"

    named = {n for n, _ in notes}
    for e in (x for x in left_before if x not in left_after):
        assert (Path(e["src"]).exists() or e["src"] in named or e["dst"] in named), (
            f"запись стёрта, файла дома нет, оговорки нет: {e}")
    for what, why in notes:
        if "вернули как «" in why:
            name = why.split("вернули как «", 1)[1].rstrip("»")
            assert (Path(what).parent / name).exists(), (
                f"оговорка называет имя, которого на диске нет: {name}")
