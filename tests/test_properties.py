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
from sorter.util import rel_to

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


@pytest.mark.parametrize("seed", seeds(6))
def test_undo_stays_honest_when_moving_fails_midway(seed, tmp_path):
    """Перемещение падает ПОСРЕДИ отката — и бухгалтерия всё равно сходится.

    Порчу состояния гоняли вокруг применения; у отката проверяли, что он
    правильно считает записи, но не то, как он ведёт себя, когда падает само
    `shutil.move`. Ветки на этот случай (`mover.undo` — отвод в сторону не
    прошёл, возврат назад не прошёл) трассировщик показывал непокрытыми, а
    именно в такой непокрытой ветке однажды несколько сессий прожила починка,
    которая не выполнялась ни разу.

    Обещаний тут три, и все про честность: ничего не пропало, запись уходит из
    журнала только когда откат с ней и правда закончил, и ни один файл не лежит
    под именем, которого человек не давал и которое нигде не названо.
    """
    import shutil

    rng = random.Random(seed)
    config, root, all3d = sandbox(rng, tmp_path)
    result = apply(
        build_plan(config, send_3d_external=bool(all3d), deep=rng.random() < 0.5),
        config, dry_run=False)
    if not result.undo_log:
        pytest.skip("двигать было нечего — журнала нет")

    roots = [r for r in (root, all3d) if r is not None]
    existed = {str(p) for b in roots for p in b.rglob("*") if p.is_file()}
    before_bodies = bodies(root, all3d)
    op = history.list_operations(root)[0]
    left_before = journal(op.log_path)

    real = shutil.move

    def flaky(src, dst):
        if rng.random() < 0.35:
            raise OSError("диск отвалился посреди отката")
        return real(src, dst)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(shutil, "move", flaky)
    try:
        notes = history.undo_operation(op, config)
    finally:
        monkeypatch.undo()

    assert not set(before_bodies) - set(bodies(root, all3d)), "откат потерял содержимое"

    left_after = journal(op.log_path) if op.log_path.exists() else []
    assert len(left_after) <= len(left_before), "журнал после отката вырос"
    for e in left_after:
        assert e in left_before, f"в журнале появилась запись, которой не было: {e}"

    # Запись осталась в журнале — значит откат её не доиграл, и человек обязан
    # об этом услышать. Молчаливый «успешный» откат, после которого сортировка
    # висит в истории неоткатанной, — худший из возможных исходов: снаружи он
    # неотличим от полного, и следующая попытка возьмётся за файлы вслепую.
    assert not left_after or notes, (
        f"откат оставил в журнале {len(left_after)} записей и не сказал ни слова")

    named = {n for n, _ in notes}
    for e in (x for x in left_before if x not in left_after):
        assert (Path(e["src"]).exists() or e["src"] in named or e["dst"] in named), (
            f"запись стёрта, файла дома нет, оговорки нет: {e}")

    spoken = "\n".join([str(w) for w, _ in notes] + [w for _, w in notes])
    landed = {e["dst"] for e in left_after} | {e["src"] for e in left_before}
    for base in roots:
        for p in base.rglob("*"):
            if not p.is_file() or ".sorter" in p.parts:
                continue
            if str(p) in existed or str(p) in landed:
                continue
            assert p.name in spoken, (
                f"файл лежит под именем, которого никто не давал и которое "
                f"нигде не названо: {p}")


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
                           ("external_3d", dict),
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


# --- окно Tkinter целиком ------------------------------------------------------


def _tk_app(config_path, rng):
    """Настоящее окно Tkinter без `mainloop`. None — экрана нет.

    Виджеты поднимаются по-настоящему нарочно. `ui.py` — единственный модуль
    без своих тестов (13% по трассировщику), и проверять в нём заглушки значит
    проверять заглушки: половина кода окна живёт в том, что показано в таблице
    и в строке состояния, а не в том, что вернуло ядро.
    """
    import tkinter as tk
    from sorter import ui

    class Box:
        """Модальные окна в пробнике: помним, что сказали, и идём дальше.

        Третьим членом — каким окном сказали. Значок отличает уведомление от
        поломки не меньше, чем слова: спокойное «i» и тревожное «!» человек
        читает раньше заголовка.
        """
        said: list = []

        @classmethod
        def showinfo(cls, title, text): cls.said.append((title, text, "info"))

        @classmethod
        def showwarning(cls, title, text): cls.said.append((title, text, "warn"))

        @classmethod
        def showerror(cls, title, text): cls.said.append((title, text, "err"))

        @staticmethod
        def askyesno(title, text): return True

    Box.said = []
    root = _tk_root()
    if root is None:
        return None, None, None
    ui.messagebox = Box
    frame = tk.Toplevel(root)
    return ui.SorterApp(frame, config_path), Box, frame


_TK_ROOT: list = []


def _tk_root():
    """Один корень Tk на весь набор. None — экрана нет.

    Своё `tk.Tk()` на каждый прогон Tcl держит плохо: на два десятка подряд
    один-другой падает с `TclError`, и пробник молча превращается в пропуск —
    то есть в зелёный тест, который ничего не проверил.
    """
    import tkinter as tk
    if not _TK_ROOT:
        try:
            root = tk.Tk()
        except tk.TclError:
            _TK_ROOT.append(None)
        else:
            root.withdraw()
            _TK_ROOT.append(root)
    return _TK_ROOT[0]


def _write_rules(case: Path, root: Path, all3d: Path | None) -> Path:
    (case / "rules.json").write_text(json.dumps({
        "categories": {k: list(v) for k, v in CATEGORIES.items()},
        "type_map": {k: list(v) for k, v in TYPE_MAP.items()},
        "managed_folders": list(MANAGED),
        "ignore": ["*.tmp"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (case / "config.json").write_text(json.dumps({
        "downloads_path": str(root),
        "external_3d": {"enabled": bool(all3d), "extensions": ["stl", "gcode"],
                        "path": str(all3d) if all3d else ""},
    }, ensure_ascii=False), encoding="utf-8")
    return case / "config.json"


@pytest.mark.parametrize("seed", seeds(5))
def test_the_tk_window_shows_and_does_what_the_core_would(seed, tmp_path):
    """Окно Tkinter на случайной папке: показано одно — сделано то же.

    До сих пор этот интерфейс проверялся одним методом сохранения настроек, и
    расхождение с ядром искать было нечем. А расходиться ему есть где: таблицу,
    строку состояния и порядок «применить → пересобрать план → отчитаться» окно
    складывает само.

    Папку нарочно портим между «Очистить» и «Применить» — без этого половина
    `mover` (столкновения, неудачи, отчёт о них) не выполняется вовсе, и
    пробник смотрит на прогон, где всё и так хорошо.
    """
    rng = random.Random(seed)
    root = tmp_path / "загрузки"
    all3d = tmp_path / "All_3d" if rng.random() < 0.5 else None
    build_layout(rng, root, all3d)
    assert str(tmp_path) in str(root)          # песочница, а не чужие загрузки
    config_path = _write_rules(tmp_path, root, all3d)

    app, box, frame = _tk_app(config_path, rng)
    if app is None:
        pytest.skip("экрана нет — окно не поднять")
    try:
        app.resort.set(rng.random() < 0.5)
        app.to_3d.set(bool(all3d) and rng.random() < 0.8)
        app.preview()

        rows = [app.tree.item(i, "values") for i in app.tree.get_children()]
        assert len(rows) == len(app.moves), "в таблице не столько строк, сколько в плане"
        for (shown, _dst), mv in zip(rows, app.moves):
            assert shown == rel_to(mv.src, root), (
                f"таблица называет источник «{shown}», а план ведёт {mv.src}")
        assert f"План готов: {len(app.moves)}" in app.status.get()

        spoiled = rng.random() < 0.75
        if spoiled:
            spoil_before_apply(rng, app.moves)
        was = bodies(root, all3d)

        app.move_enabled.set(True)
        app.do_apply()

        assert not set(was) - set(bodies(root, all3d)), "применение потеряло содержимое"
        status = app.status.get()
        assert status.startswith("Перемещено:"), (
            f"после применения окно пишет не про итог: {status!r}")
        moved = int(status.split("Перемещено:")[1].split(",")[0])
        errors = int(status.split("ошибок:")[1])
        assert box.said, "файлы тронули, а отчёта не показали"
        if moved:
            assert history.list_operations(root), (
                f"переместили {moved}, а откатить нечем: истории нет")

        # Прибранная папка второй уборки не требует. Спрашиваем это только с
        # непорченого прогона: порча САМА рождает файлы («чужак» в цели,
        # «пробка» на месте папки), и разложить их вторым проходом —
        # правильная работа. Упавшее перемещение честно оставляет файл на месте.
        app.preview()
        if not spoiled and not errors:
            assert not app.moves, (
                "повторный план после успешной уборки не пуст: "
                f"{[str(m.src) for m in app.moves[:3]]}")
    finally:
        frame.destroy()


# --- испорченные настройки не роняют программу ---------------------------------


def test_a_broken_3d_path_never_takes_the_program_down(tmp_path):
    """Путь к All_3d правят руками — значит там бывает что угодно.

    Ждём от каждой строки одного из двух: работы или внятной жалобы. Падения не
    ждём ни от одной, и проверяется здесь весь путь целиком — разбор, план,
    применение, откат, — потому что упало оно в самом конце: `mkdir` бросал
    `ValueError`, а `apply` ловит `OSError`.

    Не-OSError тут дороже обычной ошибки: в окне PyQt необработанное исключение
    в слоте гасит процесс целиком, без окна и без строчки в отчёте, а файлы к
    тому времени частью уже переехали.
    """
    nasty = [
        str(tmp_path / "All\x003d"),     # склеенная программой строка
        str(tmp_path / "All\x01_3d"),    # управляющий символ
        str(tmp_path / "All_3d\n2"),
        str(tmp_path / "All_3d?"),       # запрещённый символ
        str(tmp_path / "All_3d*"),
        str(tmp_path / "All_3d "),       # хвостовой пробел
        str(tmp_path / ("д" * 250)),     # длиннее предела Windows
        "All_3d", "C:", "", " ",         # неполные пути
    ]
    for i, raw in enumerate(nasty):
        case = tmp_path / f"c{i}"
        root = case / "загрузки"
        root.mkdir(parents=True)
        (root / "деталь.stl").write_text("тело", encoding="utf-8")
        (root / "клип.mp4").write_text("тело2", encoding="utf-8")
        config = make_config(root, None)
        config.external_3d = {"enabled": True, "path": raw,
                              "extensions": ["stl", "gcode"]}

        moves = build_plan(config, send_3d_external=True, deep=False)
        result = apply(moves, config, dry_run=False)
        for op in history.list_operations(root):
            history.undo_operation(op, config)

        assert result.moved + len(result.errors) == len(moves), (
            f"отчёт не сходится с планом на пути {raw!r}")
        assert not any("\x00" in str(mv.dst) for mv in moves), (
            f"план ведёт файл по пути, которого файловая система не примет: {raw!r}")


# --- отчёт о настройках не спорит сам с собой ----------------------------------


def _frozen_rules_case(tmp_path: Path) -> Path:
    """Настройки, где всё в порядке, а ручных правил «в Others» три штуки.

    Файлы читаются целиком, раскладка выходит верная — точнее, чем была бы с
    этими правилами. Больше поводов сказать хоть слово тут нет ни одного.
    """
    root = tmp_path / "загрузки"
    root.mkdir(parents=True, exist_ok=True)
    (root / "0001-0250.mp4").write_text("рендер", encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "patterns": {"3D": [r"^\d{4}-\d{4}\.(mp4|png)$"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "3D", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(root),
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "overrides.json").write_text(json.dumps({
        "0001-0250.mp4": "Others",
        "0251-0500.mp4": "Others",
        "непонятно.qqq": "others",
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "config.json"


def test_dropped_frozen_rules_are_not_announced_as_a_breakage(tmp_path):
    """Пропущенное правило «в Others» — не поломка, и говорить о нём надо иначе.

    Разбор выбрасывает такие правила нарочно: без них файл едет в ту же
    запасную папку, но по текущим правилам, а с ними — мимо всего, что
    появилось потом. То есть раскладка от пропуска становится ВЕРНЕЕ.

    Жалоба при этом складывалась в общий список `problems`, а все три
    интерфейса печатают его под заголовком «Настройки прочитаны не полностью»
    с подписью «раскладка может быть неверной». Обе фразы про эту строку
    ложны: файл прочитан целиком, раскладка верна. Висит сообщение при каждом
    запуске навсегда — строки нарочно остаются в файле, — и человек либо идёт
    искать поломку, которой нет, либо перестаёт читать этот список вовсе,
    вместе с настоящими поломками рядом.
    """
    from sorter.util import SETTINGS_ALARM, settings_message

    config = Config.load(_frozen_rules_case(tmp_path))

    assert config.overrides == {}, "правила «в Others» должны быть выброшены"
    assert config.notices, "о выброшенных правилах надо сказать хоть что-то"
    assert not config.problems, (
        "поломок тут нет ни одной, а разбор их насчитал: " + "; ".join(config.problems))

    title, text, broken = settings_message(config.problems, config.notices)
    assert not broken, "уведомление объявлено поломкой"
    assert title != SETTINGS_ALARM, f"заголовок обещает поломку: {title!r}"
    assert "неверной" not in text, f"текст пугает неверной раскладкой: {text!r}"
    assert "0001-0250.mp4" in text, "сказали о пропуске, но не назвали строку"


def test_the_startup_report_separates_breakages_from_notices(tmp_path):
    """Настоящая поломка рядом с уведомлением: заголовок остаётся тревожным.

    Смешивать их в одном списке нельзя, но и терять уведомление, когда рядом
    есть поломка, тоже: строки в файле остались, и убрать их всё ещё стоит.
    """
    from sorter.util import SETTINGS_ALARM, settings_message

    config_path = _frozen_rules_case(tmp_path)
    (tmp_path / "overrides.json").write_text(
        '{"0001-0250.mp4": "Others", "клип.mp4": "Медиа "}',
        encoding="utf-8")

    config = Config.load(config_path)

    assert config.problems, "правило с непригодной категорией — поломка"
    assert config.notices, "правило «в Others» осталось без единого слова"

    title, text, broken = settings_message(config.problems, config.notices)
    assert broken and title == SETTINGS_ALARM
    assert "Медиа " in text and "0001-0250.mp4" in text, (
        f"половина сказанного потерялась: {text!r}")


def test_all_three_interfaces_say_the_same_about_the_settings(tmp_path):
    """Консоль, PyQt и Tkinter об одних настройках говорят одними словами.

    Текст этот собирает `util.settings_message` — по той же причине, по какой
    общими сделаны `util.report` и `config.path_3d_reason`: расходясь, три
    интерфейса перестают проверять друг друга.
    """
    import ast

    root = Path(__file__).resolve().parent.parent
    hardcoded = []
    for path in (root / "sorter" / "ui.py", root / "sorter" / "ui_qt.py",
                 root / "main.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "прочитаны не полностью" in node.value:
                    hardcoded.append(f"{path.name}:{node.lineno}")

    assert not hardcoded, (
        "заголовок набран во второй раз — однажды он разойдётся с остальными:\n  "
        + "\n  ".join(hardcoded))


# --- «Применить», когда двигать нечего ------------------------------------------


def _empty_case(tmp_path: Path, downloads: str) -> Path:
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps(
        {"downloads_path": downloads}, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "config.json"


def test_the_window_tells_the_three_empty_plans_apart(tmp_path):
    """«Нет плана» значило три разных вещи, а совет давало один.

    Пустой `moves` получается тремя путями: план ещё не строили, папки на диске
    нет, план построен и в нём ноль строк. Ответ на «Применить» был на все три
    один — «Сначала нажми «Очистить»», — и в двух случаях из трёх он неверен.

    На ненайденной папке он вдобавок спорит с соседней строкой: окно в ту же
    секунду пишет «Папка не найдена», а кнопка советует нажать «Очистить»,
    после чего окно скажет ровно то же самое. Круг замкнут, и выйти из него
    подсказкой нельзя. На прибранной папке совет ведёт по тому же кругу, только
    тише: человек жмёт «Очистить» ещё раз и получает всё те же ноль строк.
    """
    from sorter.util import (PLAN_EMPTY, PLAN_NOT_BUILT, PLAN_NO_FOLDER,
                             nothing_to_apply)

    titles = {state: nothing_to_apply(state)[0]
              for state in (PLAN_NOT_BUILT, PLAN_NO_FOLDER, PLAN_EMPTY)}
    assert len(set(titles.values())) == 3, (
        f"три разных состояния отвечают одними словами: {titles}")

    advice = nothing_to_apply(PLAN_NO_FOLDER)[1] + nothing_to_apply(PLAN_EMPTY)[1]
    assert "Очистить" not in advice, (
        "совет нажать «Очистить» ведёт по кругу: окно ответит тем же самым")


def test_the_tk_window_names_the_reason_it_moves_nothing(tmp_path):
    """Окно Tkinter: три состояния — три разных ответа, и файлы не тронуты."""
    from sorter.util import PLAN_EMPTY, PLAN_NOT_BUILT, PLAN_NO_FOLDER

    root = tmp_path / "загрузки"
    root.mkdir()
    config_path = _empty_case(tmp_path, str(root))

    app, box, frame = _tk_app(config_path, random.Random(1))
    if app is None:
        pytest.skip("экрана нет — окно не поднять")
    try:
        app.move_enabled.set(True)

        assert app.plan_state == PLAN_NOT_BUILT
        app.do_apply()
        first = box.said[-1][0]

        app.preview()                       # папка есть, но пуста
        assert app.plan_state == PLAN_EMPTY, app.status.get()
        app.do_apply()
        empty = box.said[-1][0]

        app.config.downloads_path = str(root / "нетути")
        app.preview()
        assert app.plan_state == PLAN_NO_FOLDER, app.status.get()
        app.do_apply()
        gone = box.said[-1][0]

        assert len({first, empty, gone}) == 3, (
            f"окно отвечает одинаково на разное: {first!r}, {empty!r}, {gone!r}")
        assert "Очистить" not in box.said[-1][1], (
            f"на ненайденной папке совет ведёт по кругу: {box.said[-1][1]!r}")
    finally:
        frame.destroy()


def test_both_windows_title_the_report_the_same_way(tmp_path):
    """Отчёт с оговорками не называется «Готово» в одном окне и иначе в другом.

    Текст отчёта давно общий (`util.report`), а заголовок каждое окно ставило
    своё: Tkinter отличал «Готово с оговорками» от «Готово», PyQt показывал
    тревожный значок под словом «Готово». Значок в списке уведомлений Windows
    не остаётся, а заголовок остаётся.
    """
    import ast

    from sorter.mover import Result
    from sorter.util import report_title

    assert report_title(Result(planned=1, moved=1)) == "Готово"
    with_notes = Result(planned=1, moved=1)
    with_notes.notes.append(("клип.mp4", "лёг под другим именем"))
    assert report_title(with_notes) != "Готово", (
        "оговорки в отчёте, а заголовок обещает гладкий прогон")

    root = Path(__file__).resolve().parent.parent
    hardcoded = []
    for path in (root / "sorter" / "ui.py", root / "sorter" / "ui_qt.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "Готово":
                hardcoded.append(f"{path.name}:{node.lineno}")
    assert not hardcoded, (
        "заголовок отчёта набран на месте — однажды окна разойдутся:\n  "
        + "\n  ".join(hardcoded))


# --- непрочитанная папка не должна теряться по дороге ---------------------------
#
# Пробник на ту же беду в окне PyQt живёт в `test_ui_ai.py`: Tk и Qt в одном
# процессе роняют его без traceback, а корень Tk тут поднят на весь набор.


def test_the_tk_window_greets_a_notice_calmly_and_a_breakage_loudly(tmp_path):
    """Окно на старте: подчищенное разбором — спокойным окном, поломка — тревожным.

    Проверяем в самом окне, а не только в тексте: значок человек читает раньше
    заголовка, а выбирает его каждое окно у себя. Пока списки жалоб были одним,
    «правило в Others пропущено» встречало пользователя восклицательным знаком
    при каждом запуске — навсегда, потому что строки нарочно остаются в файле.
    """
    from sorter.util import SETTINGS_ALARM

    quiet = tmp_path / "тихо"
    quiet.mkdir()
    app, box, frame = _tk_app(_frozen_rules_case(quiet), random.Random(1))
    if app is None:
        pytest.skip("экрана нет — окно не поднять")
    try:
        assert box.said, "про выброшенные правила окно не сказало ничего"
        title, text, kind = box.said[0]
        assert kind == "info", f"уведомление показано как поломка: {title!r}"
        assert title != SETTINGS_ALARM and "неверной" not in text, text
    finally:
        frame.destroy()

    loud = tmp_path / "громко"
    loud.mkdir()
    config_path = _frozen_rules_case(loud)
    (loud / "overrides.json").write_text(
        '{"0001-0250.mp4": "Others", "клип.mp4": "Медиа "}', encoding="utf-8")
    app, box, frame = _tk_app(config_path, random.Random(1))
    try:
        title, text, kind = box.said[0]
        assert kind == "warn", f"поломка показана как уведомление: {title!r}"
        assert title == SETTINGS_ALARM
        assert "Медиа " in text and "0001-0250.mp4" in text, (
            f"половина сказанного потерялась: {text!r}")
    finally:
        frame.destroy()


def test_the_line_left_after_applying_still_names_the_unread_folder(tmp_path):
    """«Перемещено: 2, ошибок: 0» — и ни слова о папке, которую не открыли.

    Строку про непрочитанную папку ставит `preview`, а `do_apply` зовёт его и
    тут же затирает итогом — нарочно, чтобы последнее слово осталось за тем,
    что случилось с файлами. Вместе со строкой уезжала и жалоба, то есть ровно
    в тот момент, когда человек читает отчёт, окно докладывает о безупречном
    прогоне: ноль ошибок, всё разложено. А файлы непрочитанной папки в плане не
    участвовали и лежат неразобранными — ошибкой это не считается, и в списке
    неудач их нет.
    """
    from sorter import scanner

    root = tmp_path / "загрузки"
    (root / "Медиа").mkdir(parents=True)
    (root / "клип.mp4").write_text("тело", encoding="utf-8")
    (root / "Медиа" / "старое.mp4").write_text("тело", encoding="utf-8")
    config_path = _empty_case(tmp_path, str(root))

    def unreadable(folder, config, visited, problems=None):
        scanner._unreadable(folder, OSError(5, "Отказано в доступе"), problems)
        return []

    app, box, frame = _tk_app(config_path, random.Random(1))
    if app is None:
        pytest.skip("экрана нет — окно не поднять")
    walked = scanner._walk_managed
    scanner._walk_managed = unreadable
    try:
        app.resort.set(True)
        app.preview()
        assert "Не прочитано папок: 1" in app.status.get()

        app.move_enabled.set(True)
        app.do_apply()

        assert app.status.get().startswith("Перемещено:"), app.status.get()
        assert "Не прочитано папок: 1" in app.status.get(), (
            "после «Применить» окно молчит про папку, которую не открыло: "
            f"{app.status.get()!r}")
    finally:
        scanner._walk_managed = walked
        frame.destroy()


def test_the_tk_window_names_a_missing_folder_instead_of_a_winerror(tmp_path):
    """«📂 Открыть» на ненайденной папке: окна должны говорить одно и то же.

    PyQt проверяет папку сам и отвечает «Папка не найдена. Укажи существующую
    папку». Tkinter звал `os.startfile` вслепую и показывал `[WinError 2] Не
    удается найти указанный файл` — сообщение системы про путь, который окно
    само же и написало строкой выше.
    """
    root = tmp_path / "загрузки"
    root.mkdir()
    config_path = _empty_case(tmp_path, str(root / "нетути"))

    app, box, frame = _tk_app(config_path, random.Random(1))
    if app is None:
        pytest.skip("экрана нет — окно не поднять")
    try:
        box.said.clear()
        app.open_downloads()
        assert box.said, "кнопка промолчала о ненайденной папке"
        title, text = box.said[-1][0], box.said[-1][1]
        assert "WinError" not in text and "Errno" not in text, (
            f"вместо слов показан код системы: {title!r} / {text!r}")
        assert "не найдена" in title.lower(), f"заголовок не про то: {title!r}"
    finally:
        frame.destroy()
