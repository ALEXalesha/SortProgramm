"""Окно Tkinter: настройки, которые оно меняет, должны переживать закрытие.

`tk.Tk()` требует экрана, поэтому виджеты не поднимаются: проверяется сам
метод сохранения на объекте с теми же полями, что у живого окна.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from sorter.config import Config
from sorter.ui import SorterApp


def make_window(tmp_path, to_3d):
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]},
        "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(tmp_path),
        "external_3d": {"enabled": False, "path": "X:/all3d", "extensions": ["stl"]},
    }, ensure_ascii=False), encoding="utf-8")

    app = SorterApp.__new__(SorterApp)
    app.config_path = tmp_path / "config.json"
    app.config = Config.load(app.config_path)
    app.to_3d = SimpleNamespace(get=lambda: to_3d)
    return app


def test_tk_window_remembers_the_3d_checkbox(tmp_path):
    """Галочка «3D-модели → All_3d» забывалась при закрытии окна.

    Окно PyQt сохраняет её в config.json, консоль без флагов оттуда же её и
    берёт — на том и держится обещание, что окно и консоль на одних настройках
    показывают один и тот же план. Окно Tkinter не сохраняло ничего: галочку
    поставили, файлы разложили, закрыли — и следующий запуск (хоть окна, хоть
    консоли) снова раскладывает модели по обычным категориям. Ни ошибки, ни
    слова о том, что настройку не запомнили.
    """
    app = make_window(tmp_path, to_3d=True)

    app._save_settings()

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["external_3d"]["enabled"] is True


def test_tk_window_keeps_the_3d_path_it_did_not_touch(tmp_path):
    """Поля пути к папке 3D у этого окна нет — стереть его оно не вправе."""
    app = make_window(tmp_path, to_3d=True)

    app._save_settings()

    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["external_3d"]["path"] == "X:/all3d"
