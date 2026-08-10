"""Точка входа сортировщика загрузок.

GUI:   python main.py
CLI:   python main.py --path <папка> [--apply]   (без --apply = только показать)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sorter.config import Config, NO_DOWNLOADS_PROBLEM, path_3d_reason
from sorter.planner import build_plan, external_3d_warning
from sorter.mover import apply
from sorter.util import rel_to, report, settings_message

# В собранном .exe (PyInstaller onefile) config.json/overrides.json лежат рядом с .exe,
# чтобы их можно было править без пересборки. В обычном запуске — рядом с main.py.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"


def run_cli(path: str | None, do_apply: bool, to_3d: bool | None, deep: bool) -> None:
    config = Config.load(CONFIG_PATH)
    # Пробелы по краям срезаем так же, как разбор настроек и поле окна: путь из
    # командной строки приходит в кавычках, и лишний пробел внутри них заметить
    # нечем, а на Windows он превращает работу в тихое «к перемещению: 0».
    if path and path.strip():
        config.downloads_path = path.strip()
        # Папку назвали флагом — значит жалоба на ненастроенную папку в
        # config.json больше ни о чём. Хуже того, она врёт дважды: настройку
        # только что перебили, а строка вдобавок называет `~/Downloads` папкой,
        # которую взяли, при том что следующей же строкой печатается «Папка:
        # <совсем другая>». Человек читает предупреждение о том, что программа
        # сейчас разложит не то, и идёт проверять — а разложит она ровно ту
        # папку, которую он назвал. Отчёт, спорящий с собственной соседней
        # строкой, эта программа считает ошибкой.
        #
        # Снимаем только эту жалобу. Всё остальное — нечитаемый config.json,
        # пропавшие правила, негодный путь к All_3d — от флага не меняется и
        # печатается как печаталось.
        config.problems = [p for p in config.problems
                           if not p.startswith(NO_DOWNLOADS_PROBLEM)]

    # Окно про испорченные настройки предупреждает окном, а CLI молчал — и
    # раскладка «всё в Others» из-за нечитаемого rules.json выглядела как
    # нормальный план. Сказать надо до `--apply`, а не после.
    #
    # Заголовок и текст общие на три интерфейса (`util.settings_message`):
    # подчищенное разбором печатается отдельно от поломок, иначе постоянное
    # «правило в Others пропущено» стоит в консоли под словами о неверной
    # раскладке — и настоящие поломки рядом перестают читать.
    if config.problems or config.notices:
        title, text, _ = settings_message(config.problems, config.notices)
        print(f"{title}:")
        print("\n".join(f"  {line}" if line else "" for line in text.splitlines()))
        print()

    # Опечатка в пути выглядела ровно как прибранная папка: «Найдено к
    # перемещению: 0». Окно в этом случае говорит «Папка не найдена» — CLI
    # должен говорить то же самое, иначе ноль в консоли ничего не значит.
    if not Path(config.downloads_path).is_dir():
        print(f"Папка не найдена: {config.downloads_path}")
        return

    # Без флага берём то, что стоит галочкой в окне: CLI и окно должны
    # показывать один и тот же план на одних и тех же настройках, иначе
    # проверка правил через консоль ничего не проверяет.
    if to_3d is None:
        to_3d = bool(config.external_3d.get("enabled", False))

    # Включённый вынос 3D без годного пути ничего не выносит: модели молча
    # едут в обычные категории. Окно про это говорит строкой под планом, а
    # консоль молчала — предупреждал `Config.load`, и только по галочке,
    # сохранённой в файле. `--to3d` включает вынос поверх выключенной галочки,
    # и тогда не предупреждал никто. План при этом от исправного неотличим.
    # Про сохранённую галочку с негодным путём уже сказано выше, разбором
    # настроек. Повторять ту же причину второй фразой подряд незачем — общий
    # кусок текста даёт `config.path_3d_reason`, по нему и сверяемся.
    warning = external_3d_warning(config, to_3d)
    if warning and not any(path_3d_reason(config.external_3d.get("path")) in p
                           for p in config.problems):
        print(f"! {warning}\n")

    # deep=False — только корень загрузок; deep=True — переразложить и то,
    # что программа уже разложила по своим папкам. Плюс разбор All_3d.
    #
    # Папки, которые не удалось прочитать, приходят отдельным списком. Сказать
    # о них надо до счёта, а не после: ноль в консоли значит «прибрано», и без
    # этих строк отличить его от «половину папок не открыли» нечем.
    unread: list[str] = []
    moves = build_plan(config, send_3d_external=to_3d, deep=deep, problems=unread)

    print(f"Папка: {config.downloads_path}")
    for problem in unread:
        print(f"  ! {problem}")
    print(f"Найдено к перемещению: {len(moves)}\n")
    root = Path(config.downloads_path)
    for mv in moves:
        reason = f"   [{mv.note}]" if mv.note else ""
        print(f"  {rel_to(mv.src, root)}  ->  {rel_to(mv.dst, root)}{reason}")

    if do_apply:
        result = apply(moves, config, dry_run=False)
        # Отчёт общий на три интерфейса (`util.report`) — это его обещание, и
        # до сих пор консоль его не выполняла: она печатала итог своими
        # словами. Пропадал при этом ровно тот заголовок, ради которого отчёт
        # и собрали в одном месте. Оговорки («лёг под другим именем») шли
        # сразу за списком ошибок, без единого слова между ними, и строка
        # «клип.mp4: в цели уже есть …» читалась как ещё одна неудача — при
        # том, что файл переехал и лежит под соседним именем. Сверять правила
        # через консоль README советует именно потому, что консоль показывает
        # то же, что окно.
        #
        # Хвост не сворачиваем (`limit=None`): десять строк — предел окна, а
        # не консоли, где список листают.
        print()
        print(report(result, limit=None))
        if result.undo_log:
            print(f"Лог отмены: {result.undo_log}")
    else:
        print("\n(режим показа — ничего не перемещено; добавь --apply чтобы применить)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Сортировщик загрузок")
    parser.add_argument("--path", help="папка для сортировки (по умолчанию из config.json)")
    parser.add_argument("--apply", action="store_true", help="реально перемещать файлы")
    parser.add_argument("--cli", action="store_true", help="режим командной строки без окна")
    parser.add_argument("--to3d", action=argparse.BooleanOptionalAction, default=None,
                        help="файлы 3D-моделей (3mf/obj/stl/gcode) -> внешняя папка "
                             "All_3d (по умолчанию — как настроено в окне)")
    parser.add_argument("--deep", action="store_true",
                        help="переразложить: проверить заново и то, что уже разложено "
                             "по папкам программы (чужие папки не трогаются)")
    parser.add_argument("--tk", action="store_true", help="старый интерфейс Tkinter")
    args = parser.parse_args()

    # `--apply`, `--deep`, `--to3d` осмысленны только в консоли: окно берёт то же
    # самое из галочек. Раньше в CLI уводили лишь `--cli` и `--path`, поэтому
    # документированный `python main.py --apply --to3d` открывал окно и молча
    # терял оба флага — человек ждал, что файлы разъедутся по папкам, а получал
    # нетронутые загрузки. Флаг, который меняет файлы на диске, терять нельзя.
    wants_cli = (args.cli or args.path or args.apply
                 or args.deep or args.to3d is not None)
    if wants_cli:
        run_cli(args.path, args.apply, args.to3d, args.deep)
    elif args.tk:
        from sorter.ui import launch
        launch(CONFIG_PATH)
    else:
        from sorter.ui_qt import launch
        launch(CONFIG_PATH)


if __name__ == "__main__":
    main()
