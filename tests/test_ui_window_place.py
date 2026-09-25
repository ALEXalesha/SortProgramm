"""Окно PyQt открывается там и такого размера, где его закрыли (4.2): window.json рядом с
config.json, ключ qt; ключ старого окна Tkinter (tk) при записи не пропадает."""
import json

import pytest
from PyQt6.QtWidgets import QApplication

from sorter import ui_qt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _config(tmp_path):
    # Правила на месте: без них окно предупреждает модальным окном и тест ждал бы его вечно.
    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"Медиа": ["клип"]}, "type_map": {"Videos": ["mp4"]},
        "managed_folders": ["Медиа", "Videos", "Others", "Misc"],
        "fallback_category": "Others", "fallback_type": "Misc",
    }), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({"downloads_path": str(tmp_path)}), encoding="utf-8")
    return tmp_path / "config.json"


def test_the_window_comes_back_where_and_how_large_it_was(app, tmp_path):
    cfg = _config(tmp_path)
    (tmp_path / "window.json").write_text(json.dumps({"tk": {"x": 1, "y": 2, "width": 700, "height": 500}}),
                                          encoding="utf-8")
    win = ui_qt.GlassWindow(cfg)
    # Экран самого окна; не у самого верха - restoreGeometry() Qt оставляет над окном 32 px
    # под заголовок. 720x520 влезает и в экран раннера 1024x768.
    area = win.screen().availableGeometry()
    x, y = area.x() + 40, area.y() + 60
    win.setGeometry(x, y, 720, 520)
    win.close()
    data = json.loads((tmp_path / "window.json").read_text(encoding="utf-8"))
    assert isinstance(data["qt"], str) and data["tk"]["width"] == 700
    again = ui_qt.GlassWindow(cfg)
    try:
        assert (again.x(), again.y(), again.width(), again.height()) == (x, y, 720, 520)
    finally:
        win.deleteLater()
        again.deleteLater()


@pytest.mark.parametrize("text", ["", "{", "[]", "null", '{"qt": 42}', '{"qt": "@@@"}', '{"qt": "AAAA"}'])
def test_a_broken_file_gives_the_default_size(app, tmp_path, text):
    cfg = _config(tmp_path)
    (tmp_path / "window.json").write_text(text, encoding="utf-8")
    win = ui_qt.GlassWindow(cfg)
    try:
        assert (win.width(), win.height()) == (860, 600)
    finally:
        win.deleteLater()
