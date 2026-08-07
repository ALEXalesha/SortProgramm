"""Строка «3D-модели → отдельная папка» в окне PyQt.

Окно поднимается в offscreen-режиме на временной папке: проверяется, что поле
пути остаётся управляемым, потому что оно управляет и тем, что происходит
помимо галочки.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtWidgets import QApplication

from sorter import ui_qt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    downloads = tmp_path / "загрузки"
    downloads.mkdir()
    all_3d = tmp_path / "All_3d"
    all_3d.mkdir()
    (downloads / "деталь.stl").write_text("x", encoding="utf-8")
    (all_3d / "модель.stl").write_text("x", encoding="utf-8")

    (tmp_path / "rules.json").write_text(json.dumps({
        "categories": {"3D": [".stl"]},
        "type_map": {"3D": ["stl"]},
        "managed_folders": ["3D", "Others", "Misc"],
        "fallback_category": "Others",
        "fallback_type": "Misc",
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps({
        "downloads_path": str(downloads),
        "external_3d": {"enabled": False, "path": str(all_3d), "extensions": ["stl"]},
    }, ensure_ascii=False), encoding="utf-8")

    win = ui_qt.GlassWindow(tmp_path / "config.json")
    yield win, downloads, all_3d
    win.deleteLater()


def test_path_field_stays_editable_with_the_box_unticked(window):
    """Поле пути гасили по галочке, а работать оно не переставало.

    Галочка решает одно: уезжают ли модели из загрузок. Разбор самого корня
    All_3d по подпапкам расширений идёт всегда, пока путь задан, — так это и
    описано в README. Отключённое поле обещало обратное: настройка выглядит
    выключенной, файлы в All_3d при каждой уборке продолжают переезжать, а
    убрать или поправить путь через окно нельзя — только правкой config.json
    руками. Про негодный путь окно в этом же состоянии ещё и предупреждает
    («Папка 3D не разбирается»), то есть показывает пальцем на поле, которое
    само же и запретило трогать.
    """
    win, _, _ = window

    assert not win.to_3d.isChecked()
    assert win.path_3d_edit.isEnabled(), (
        "путь к All_3d двигает файлы и со снятой галочкой — поле должно быть живым")
    assert win.browse_3d_btn.isEnabled()


def test_all_3d_is_still_sorted_with_the_box_unticked(window):
    """Тот самый разбор, ради которого поле и должно оставаться доступным."""
    win, downloads, all_3d = window

    win.preview()
    targets = [mv.dst for mv in win.moves]

    assert all_3d / "stl" / "модель.stl" in targets, targets
    assert downloads / "3D" / "3D" / "деталь.stl" in targets, targets


def test_clearing_the_path_stops_sorting_all_3d(window):
    """Пустое поле должно и правда выключать разбор — иначе чинить нечем."""
    win, _, all_3d = window

    win.path_3d_edit.setText("")
    win.preview()

    assert not any("All_3d" in str(mv.dst) for mv in win.moves)


def test_ticking_the_box_still_toggles_the_plan(window):
    """Сама галочка при этом работать не перестала."""
    win, _, all_3d = window

    win.to_3d.setChecked(True)
    win.preview()

    assert all_3d / "stl" / "деталь.stl" in [mv.dst for mv in win.moves]
