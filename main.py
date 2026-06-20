"""Точка входа сортировщика загрузок.

GUI:   python main.py
CLI:   python main.py --path <папка> [--apply]   (без --apply = только показать)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sorter.config import Config
from sorter.scanner import scan
from sorter.planner import plan
from sorter.mover import apply
from sorter.util import rel_to

# В собранном .exe (PyInstaller onefile) config.json/overrides.json лежат рядом с .exe,
# чтобы их можно было править без пересборки. В обычном запуске — рядом с main.py.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"


def build_plan(config: Config, send_3d_external: bool = False):
    files = scan(config.downloads_path, config)
    return plan(files, config, send_3d_external=send_3d_external)


def run_cli(path: str | None, do_apply: bool, to_3d: bool) -> None:
    config = Config.load(CONFIG_PATH)
    if path:
        config.downloads_path = path
    moves = build_plan(config, send_3d_external=to_3d)

    print(f"Папка: {config.downloads_path}")
    print(f"Найдено к перемещению: {len(moves)}\n")
    root = Path(config.downloads_path)
    for mv in moves:
        print(f"  {rel_to(mv.src, root)}  ->  {rel_to(mv.dst, root)}")

    if do_apply:
        result = apply(moves, config, dry_run=False)
        print(f"\nПеремещено: {result.moved}, ошибок: {len(result.errors)}")
        for src, err in result.errors:
            print(f"  ОШИБКА {src}: {err}")
        if result.undo_log:
            print(f"Лог отмены: {result.undo_log}")
    else:
        print("\n(режим показа — ничего не перемещено; добавь --apply чтобы применить)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Сортировщик загрузок")
    parser.add_argument("--path", help="папка для сортировки (по умолчанию из config.json)")
    parser.add_argument("--apply", action="store_true", help="реально перемещать файлы")
    parser.add_argument("--cli", action="store_true", help="режим командной строки без окна")
    parser.add_argument("--to3d", action="store_true",
                        help="файлы 3D-моделей (3mf/obj/stl/gcode) -> внешняя папка All_3d")
    parser.add_argument("--tk", action="store_true", help="старый интерфейс Tkinter")
    args = parser.parse_args()

    if args.cli or args.path:
        run_cli(args.path, args.apply, args.to3d)
    elif args.tk:
        from sorter.ui import launch
        launch(CONFIG_PATH)
    else:
        from sorter.ui_qt import launch
        launch(CONFIG_PATH)


if __name__ == "__main__":
    main()
