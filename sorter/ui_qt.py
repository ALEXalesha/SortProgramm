"""Интерфейс в стиле Liquid Glass на PyQt6 + системный Acrylic (DWM)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import json

from PyQt6.QtCore import Qt, QPoint, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QCheckBox, QVBoxLayout,
    QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QLineEdit, QFileDialog,
)

from .config import Config
from .scanner import scan
from .planner import plan, Move
from .mover import apply
from .util import rel_to
from . import ai


class _AiWorker(QThread):
    """Фоновый запрос к DeepSeek, чтобы окно не зависало."""
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, filenames, categories, api_key):
        super().__init__()
        self._filenames = filenames
        self._categories = categories
        self._api_key = api_key

    def run(self):
        try:
            result = ai.classify_with_ai(self._filenames, self._categories, self._api_key)
            self.done.emit(result)
        except Exception as exc:  # сеть, ключ, разбор — наружу как текст
            self.failed.emit(str(exc))

STYLE = """
#glass {
    background: rgba(22, 24, 32, 0.55);
    border-radius: 16px;
    border: 1px solid rgba(255, 255, 255, 0.18);
}
QLabel { color: rgba(255,255,255,0.92); background: transparent; }
#title { font-size: 15px; font-weight: 600; }
#path { color: rgba(255,255,255,0.55); font-size: 12px; }
#status { color: rgba(255,255,255,0.6); font-size: 12px; }
QPushButton {
    color: white;
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 10px;
    padding: 8px 16px;
}
QPushButton:hover { background: rgba(255,255,255,0.20); }
QPushButton:pressed { background: rgba(255,255,255,0.28); }
#accent {
    background: rgba(80, 140, 255, 0.55);
    border: 1px solid rgba(150, 190, 255, 0.6);
}
#accent:hover { background: rgba(90, 150, 255, 0.75); }
#winbtn {
    background: transparent; border: none; border-radius: 8px;
    padding: 4px 10px; font-size: 14px;
}
#winbtn:hover { background: rgba(255,255,255,0.18); }
#close:hover { background: rgba(232, 80, 80, 0.85); }
QLineEdit {
    color: white; background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.18); border-radius: 8px; padding: 6px 10px;
    selection-background-color: rgba(80,140,255,0.5);
}
QLineEdit:disabled { color: rgba(255,255,255,0.35); background: rgba(255,255,255,0.04); }
QCheckBox { color: rgba(255,255,255,0.9); background: transparent; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid rgba(255,255,255,0.4);
    background: rgba(255,255,255,0.08);
}
QCheckBox::indicator:checked { background: rgba(80, 140, 255, 0.9); }
QTableWidget {
    background: transparent; color: rgba(255,255,255,0.9);
    border: 1px solid rgba(255,255,255,0.12); border-radius: 12px;
    gridline-color: rgba(255,255,255,0.08);
    selection-background-color: rgba(80,140,255,0.4);
}
QHeaderView::section {
    background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.85);
    border: none; padding: 8px; font-weight: 600;
}
QTableWidget::item { padding: 4px 8px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.25); border-radius: 5px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class GlassWindow(QWidget):
    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.config = Config.load(config_path)
        self.moves: list[Move] = []
        self._drag_pos: QPoint | None = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(860, 600)
        self.setWindowTitle("Сортировщик загрузок")

        self._build()

    # --- разметка ---

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        glass = QWidget(objectName="glass")
        outer.addWidget(glass)
        root = QVBoxLayout(glass)
        root.setContentsMargins(18, 12, 18, 16)
        root.setSpacing(12)

        root.addLayout(self._title_bar())
        root.addLayout(self._header())
        root.addWidget(self._table(), stretch=1)
        root.addLayout(self._row_3d())
        root.addLayout(self._footer())

        self.setStyleSheet(STYLE)

    def _title_bar(self):
        bar = QHBoxLayout()
        title = QLabel("✦  Сортировщик загрузок", objectName="title")
        bar.addWidget(title)
        bar.addStretch(1)
        mini = QPushButton("—", objectName="winbtn")
        mini.clicked.connect(self.showMinimized)
        close = QPushButton("✕", objectName="winbtn")
        close.setObjectName("close")
        close.clicked.connect(self.close)
        bar.addWidget(mini)
        bar.addWidget(close)
        return bar

    def _header(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("Папка:"))
        self.path_edit = QLineEdit(self.config.downloads_path)
        self.path_edit.setPlaceholderText("Путь к папке загрузок для сортировки")
        row.addWidget(self.path_edit, stretch=1)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self.browse_folder)
        open_btn = QPushButton("📂")
        open_btn.clicked.connect(self.open_downloads)
        clean_btn = QPushButton("🧹 Очистить")
        clean_btn.clicked.connect(self.preview)
        self.ai_btn = QPushButton("✨ ИИ")
        self.ai_btn.clicked.connect(self.run_ai)
        row.addWidget(browse)
        row.addWidget(open_btn)
        row.addWidget(clean_btn)
        row.addWidget(self.ai_btn)
        return row

    def _row_3d(self):
        row = QHBoxLayout()
        self.to_3d = QCheckBox("3D-модели → отдельная папка")
        self.to_3d.setChecked(bool(self.config.external_3d.get("enabled", False)))
        self.to_3d.stateChanged.connect(self._on_3d_toggle)
        row.addWidget(self.to_3d)
        self.path_3d_edit = QLineEdit(self.config.external_3d.get("path", ""))
        self.path_3d_edit.setPlaceholderText("Путь к папке для 3D-моделей (3mf/obj/stl/gcode)")
        self.path_3d_edit.setEnabled(self.to_3d.isChecked())
        row.addWidget(self.path_3d_edit, stretch=1)
        self.browse_3d_btn = QPushButton("Обзор…")
        self.browse_3d_btn.clicked.connect(self.browse_3d_folder)
        self.browse_3d_btn.setEnabled(self.to_3d.isChecked())
        row.addWidget(self.browse_3d_btn)
        return row

    def _table(self):
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Файл", "Куда поедет"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        return self.table

    def _footer(self):
        row = QHBoxLayout()
        self.move_enabled = QCheckBox("Перемещать файлы")
        row.addWidget(self.move_enabled)
        row.addStretch(1)
        self.status = QLabel("Нажми «Очистить», чтобы построить план.", objectName="status")
        row.addWidget(self.status)
        apply_btn = QPushButton("Применить", objectName="accent")
        apply_btn.clicked.connect(self.do_apply)
        row.addWidget(apply_btn)
        return row

    # --- перетаскивание безрамочного окна ---

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 56:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # --- стекло после показа ---

    def showEvent(self, e):
        super().showEvent(e)
        try:
            from .win_glass import apply_acrylic
            apply_acrylic(int(self.winId()), dark=True)
        except Exception:
            pass

    # --- действия ---

    def _on_3d_toggle(self, *_):
        on = self.to_3d.isChecked()
        self.path_3d_edit.setEnabled(on)
        self.browse_3d_btn.setEnabled(on)
        self.preview()

    def browse_folder(self):
        d = QFileDialog.getExistingDirectory(
            self, "Папка для сортировки", self.path_edit.text() or "")
        if d:
            self.path_edit.setText(d)
            self.preview()

    def browse_3d_folder(self):
        d = QFileDialog.getExistingDirectory(
            self, "Папка для 3D-моделей", self.path_3d_edit.text() or "")
        if d:
            self.path_3d_edit.setText(d)
            self.preview()

    def _sync_config(self):
        """Переносит значения полей в конфиг."""
        self.config.downloads_path = self.path_edit.text().strip()
        if not isinstance(self.config.external_3d, dict):
            self.config.external_3d = {}
        self.config.external_3d["enabled"] = self.to_3d.isChecked()
        self.config.external_3d["path"] = self.path_3d_edit.text().strip()

    def _save_settings(self):
        self._sync_config()
        try:
            self.config.save(self.config_path)
        except OSError:
            pass

    def open_downloads(self):
        path = self.path_edit.text().strip()
        if not Path(path).is_dir():
            QMessageBox.information(self, "Папка не найдена", "Укажи существующую папку.")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def preview(self):
        self._sync_config()
        root = Path(self.config.downloads_path)
        if not self.config.downloads_path or not root.is_dir():
            self.table.setRowCount(0)
            self.moves = []
            self.status.setText("Папка не найдена — укажи существующий путь.")
            return
        files = scan(self.config.downloads_path, self.config)
        self.moves = plan(files, self.config, send_3d_external=self.to_3d.isChecked())
        self.table.setRowCount(len(self.moves))
        for r, mv in enumerate(self.moves):
            self.table.setItem(r, 0, QTableWidgetItem(rel_to(mv.src, root)))
            self.table.setItem(r, 1, QTableWidgetItem(rel_to(mv.dst, root)))
        self.status.setText(f"План готов: {len(self.moves)} файлов.")

    def _save_overrides(self):
        path = self.config_path.with_name("overrides.json")
        try:
            path.write_text(
                json.dumps(self.config.overrides, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError:
            pass

    def run_ai(self):
        self._sync_config()
        root = Path(self.config.downloads_path)
        if not root.is_dir():
            QMessageBox.information(self, "Папка не найдена", "Укажи существующую папку.")
            return
        key = ai.load_api_key(self.config_path.parent)
        if not key:
            QMessageBox.warning(
                self, "Нет ключа",
                "Положи ключ в файл deepseek_key.txt рядом с программой\n"
                "или задай переменную окружения DEEPSEEK_API_KEY.")
            return
        files = scan(self.config.downloads_path, self.config)
        if not files:
            self.status.setText("Файлов не найдено.")
            return
        names = [f.name for f in files]
        cats = list(self.config.categories.keys()) + [self.config.fallback_category]
        self.ai_btn.setEnabled(False)
        self.status.setText(f"Спрашиваю DeepSeek по {len(names)} файлам…")
        self._worker = _AiWorker(names, cats, key)
        self._worker.done.connect(self._ai_done)
        self._worker.failed.connect(self._ai_failed)
        self._worker.start()

    def _ai_done(self, mapping):
        self.ai_btn.setEnabled(True)
        if not mapping:
            self.status.setText("ИИ не вернул результатов.")
            return
        self.config.overrides.update(mapping)
        self._save_overrides()
        self.status.setText(f"ИИ разложил {len(mapping)} файлов.")
        self.preview()

    def _ai_failed(self, err):
        self.ai_btn.setEnabled(True)
        self.status.setText("Ошибка ИИ.")
        QMessageBox.warning(self, "Ошибка ИИ", err)

    def closeEvent(self, e):
        self._save_settings()
        super().closeEvent(e)

    def do_apply(self):
        if not self.moves:
            QMessageBox.information(self, "Нет плана", "Сначала нажми «Очистить».")
            return
        if not self.move_enabled.isChecked():
            QMessageBox.information(
                self, "Режим показа",
                "Включи «Перемещать файлы», чтобы применить.")
            return
        ok = QMessageBox.question(
            self, "Подтверждение", f"Переместить {len(self.moves)} файлов?")
        if ok != QMessageBox.StandardButton.Yes:
            return
        result = apply(self.moves, self.config, dry_run=False)
        self._save_settings()
        msg = f"Перемещено: {result.moved}, ошибок: {len(result.errors)}"
        self.status.setText(msg)
        (QMessageBox.warning if result.errors else QMessageBox.information)(
            self, "Готово", msg)
        self.preview()


def launch(config_path: Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = GlassWindow(config_path)
    win.show()
    app.exec()
