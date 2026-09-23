"""Кадры для README: собираются программой, а не снимком экрана.

    python tools\\make_screenshots.py

Окно поднимается на выдуманной папке загрузок во временном каталоге, с
настоящими правилами программы (`rules.json`) и без личных файлов автора: ни
`config.json`, ни `overrides.json`, ни `my_rules.json` сюда не попадают. Кадр
берётся с самого виджета (`QWidget.grab()`), поэтому в него не может попасть
чужое окно, как бывает со снимком экрана.

`QT_QPA_PLATFORM=offscreen` здесь нарочно не ставится: под ним Qt рисует без
темы Windows, и кадр вышел бы не тем, что видит человек. Окна создаются с
`WA_DontShowOnScreen` — рисуются по-настоящему, но на экран не выходят.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Выдуманная папка загрузок: по файлу на разные пути решения — слово, шаблон,
# правило из окна, неопознанный.
DEMO = [
    "задачи по физике 9 класс.pdf",
    "договор аренды.docx",
    "0001-0250.mp4",
    "Снимок экрана 2026-09-23 101500.png",
    "blender-4.5.0-windows-x64.msi",
    "arduino-ide_2.3.6_Windows_64bit.exe",
    "рецепт борща.pdf",
    "рецепт пирога.docx",
    "подставка для телефона.stl",
    "конспект лекции.pdf",
    "без названия.bin",
]


def shoot(widget, name: str, app) -> None:
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    widget.show()
    app.processEvents()
    path = OUT / name
    widget.grab().save(str(path))
    print(f"  {name}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="sorter-shots-"))
    try:
        downloads = tmp / "Загрузки"
        downloads.mkdir()
        for name in DEMO:
            (downloads / name).write_text("x", encoding="utf-8")
        shutil.copy(ROOT / "rules.json", tmp / "rules.json")
        (tmp / "config.json").write_text(
            json.dumps({"downloads_path": str(downloads)}, ensure_ascii=False),
            encoding="utf-8")

        app = QApplication.instance() or QApplication([])
        from sorter import ui_qt, ui_rules, user_rules as ur

        # Правило из окна для одного файла — чтобы в плане была и такая пометка.
        base = ui_qt.Config.load(tmp / "config.json").base
        edits = ur.add_category(ur.empty(), base, "Рецепты")
        edits = ur.add_word(edits, base, "Рецепты", "рецепт")
        edits = ur.set_file(edits, base, "без названия.bin", "Программы")
        ur.write(ur.path_for(tmp / "config.json"), edits)

        win = ui_qt.GlassWindow(tmp / "config.json")
        win.resize(980, 560)
        win.preview()
        shoot(win, "window.png", app)

        dialog = ui_rules.RulesDialog(win.config, [m.src.name for m in win.moves], win)
        dialog.select("Учёба")
        dialog.word_edit.setText("конспект")
        shoot(dialog, "rules.png", app)
        print(f"подсказка в кадре: {dialog.hint.text()}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"Готово: {OUT}")


if __name__ == "__main__":
    main()
