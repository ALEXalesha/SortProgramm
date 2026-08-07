"""Точка входа сортировщика загрузок.

GUI:   python main.py
CLI:   python main.py --path <папка> [--apply]   (без --apply = только показать)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sorter.config import Config, path_3d_reason
from sorter.planner import build_plan, external_3d_warning
from sorter.mover import apply
from sorter.util import rel_to

# В собранном .exe (PyInstaller onefile) config.json/overrides.json лежат рядом с .exe,
# чтобы их можно было править без пересборки. В обычном запуске — рядом с main.py.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"


def run_cli(path: str | None, do_apply: bool, to_3d: bool | None, deep: bool) -> None:
    config = Config.load(CONFIG_PATH)
    if path:
        config.downloads_path = path

    # Окно про испорченные настройки предупреждает окном, а CLI молчал — и
    # раскладка «всё в Others» из-за нечитаемого rules.json выглядела как
    # нормальный план. Сказать надо до `--apply`, а не после.
    if config.problems:
        print("Настройки прочитаны не полностью:")
        for problem in config.problems:
            print(f"  ! {problem}")
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
    moves = build_plan(config, send_3d_external=to_3d, deep=deep)

    print(f"Папка: {config.downloads_path}")
    print(f"Найдено к перемещению: {len(moves)}\n")
    root = Path(config.downloads_path)
    for mv in moves:
        reason = f"   [{mv.note}]" if mv.note else ""
        print(f"  {rel_to(mv.src, root)}  ->  {rel_to(mv.dst, root)}{reason}")

    if do_apply:
        result = apply(moves, config, dry_run=False)
        print(f"\nПеремещено: {result.moved}, ошибок: {len(result.errors)}")
        for src, err in result.errors:
            print(f"  ОШИБКА {src}: {err}")
        for src, note in result.notes:
            print(f"  {src}: {note}")
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
