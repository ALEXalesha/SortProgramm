"""Интерфейс в стиле Liquid Glass на PyQt6 + системный Acrylic (DWM)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QCheckBox, QVBoxLayout,
    QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QLineEdit, QFileDialog, QDialog, QMenu,
)

from . import tk_window_state, window_geometry
from .config import Config
from .planner import build_plan, external_3d_warning, Move
from .mover import apply
from .util import (PLAN_EMPTY, PLAN_NOT_BUILT, PLAN_NO_FOLDER, listing,
                   nothing_to_apply, rel_to, report, report_title,
                   settings_message)
from . import history
from . import user_rules
from .ui_rules import RulesDialog


STYLE = """
#glass {
    background: rgba(22, 24, 32, 0.55);
    /* Радиус совпадает со скруглением окна Windows 11 (DWMWCP_ROUND ≈ 8px):
       если панель скруглить сильнее, в углах просвечивает акрил — светлый ореол. */
    border-radius: 8px;
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


class HistoryDialog(QDialog):
    """Список прошлых сортировок с возможностью откатить любую из них."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        # Конфиг, а не один путь: откат убирает за собой опустевшие папки
        # программы, а для этого ему нужен список `managed_folders`.
        self.config = config
        self.downloads_path = config.downloads_path
        self.ops: list[history.Operation] = []

        self.setWindowTitle("История перемещений")
        self.resize(640, 580)
        self.setStyleSheet(STYLE + "QDialog { background: #171922; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(QLabel("История перемещений", objectName="title"))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Когда", "Файлов"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(self._show_details)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, stretch=1)

        root.addWidget(QLabel("Что куда переместилось:", objectName="status"))
        self.details = QTableWidget(0, 2)
        self.details.setHorizontalHeaderLabels(["Файл", "Куда переместили"])
        self.details.verticalHeader().setVisible(False)
        self.details.setShowGrid(False)
        self.details.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        dh = self.details.horizontalHeader()
        dh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        dh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.details, stretch=2)

        bar = QHBoxLayout()
        self.hint = QLabel("", objectName="status")
        bar.addWidget(self.hint)
        bar.addStretch(1)
        self.undo_btn = QPushButton("↩ Отменить выбранную")
        self.undo_btn.clicked.connect(self._undo_selected)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        bar.addWidget(self.undo_btn)
        bar.addWidget(close_btn)
        root.addLayout(bar)

        self._reload()

    def _reload(self):
        # Журнал, который не прочитался, — это сортировка, которой в списке
        # нет. Молчаливый пропуск делает окно неотличимым от «такой сортировки
        # и не было»: файлы разложены, а вернуть их назад отсюда уже нельзя.
        # Полный список имён уходит в подсказку мышью — строка под таблицей
        # короткая, а чинить файл всё равно идут в проводник.
        broken: list[str] = []
        self.ops = history.list_operations(self.downloads_path, broken)
        self.table.setRowCount(len(self.ops))
        for r, op in enumerate(self.ops):
            when = op.when.strftime("%d.%m.%Y  %H:%M:%S")
            self.table.setItem(r, 0, QTableWidgetItem(when))
            self.table.setItem(r, 1, QTableWidgetItem(str(op.count)))
        empty = not self.ops
        self.undo_btn.setEnabled(not empty)
        self.hint.setText(
            ("История пуста — ещё ничего не перемещалось." if empty
             else f"Записей: {len(self.ops)}. Выбери строку, чтобы откатить.")
            + (f"  Не прочитано журналов: {len(broken)} — эти сортировки "
               "отсюда не отменить." if broken else ""))
        self.hint.setToolTip("\n".join(broken))
        if empty:
            self.details.setRowCount(0)
        else:
            self.table.selectRow(0)
            self._show_details(0, 0, -1, -1)

    def _show_details(self, row, _col=0, _prev_row=-1, _prev_col=-1):
        if row < 0 or row >= len(self.ops):
            self.details.setRowCount(0)
            return
        root = Path(self.downloads_path)
        entries = self.ops[row].entries
        self.details.setRowCount(len(entries))
        for r, entry in enumerate(entries):
            src = Path(entry.get("src", ""))
            dst = Path(entry.get("dst", ""))
            self.details.setItem(r, 0, QTableWidgetItem(rel_to(src, root)))
            self.details.setItem(r, 1, QTableWidgetItem(rel_to(dst, root)))

    def _undo_selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.ops):
            QMessageBox.information(self, "Не выбрано", "Выбери строку в списке.")
            return
        op = self.ops[row]
        when = op.when.strftime("%d.%m.%Y %H:%M:%S")
        ok = QMessageBox.question(
            self, "Отменить сортировку",
            f"Вернуть {op.count} файлов на исходные места\n"
            f"(сортировка от {when})?")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            notes = history.undo_operation(op, self.config)
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка отмены", str(exc))
            return
        if notes:
            # Молчать нельзя: снаружи откат выглядит успешным, а часть данных
            # лежит под другими именами — как раз то, что легко не заметить.
            QMessageBox.warning(
                self, "Откат прошёл с оговорками",
                "Не всё вернулось ровно на своё место:\n\n" + listing(notes))
        self._reload()


class GlassWindow(QWidget):
    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.config = Config.load(config_path)
        # Путь, с которым окно открылось. Нужен на закрытии: пустое поле
        # настройкой не является, а записать его в config.json значит стереть
        # единственное место, где путь хранился (см. `_save_settings`).
        self.saved_path = self.config.downloads_path
        self.moves: list[Move] = []
        # Почему план пуст. Пустой список получается тремя путями, и на
        # «Применить» у них три разных ответа (`util.nothing_to_apply`).
        self.plan_state = PLAN_NOT_BUILT
        # Хвост строки состояния про папки, которые не удалось прочитать.
        # Держим полем, потому что дописывает его и итог применения.
        self.unread_tail = ""
        # Из чего построен показанный план (`_plan_key`). None — плана нет.
        self._planned_for: tuple | None = None
        self._drag_pos: QPoint | None = None

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(860, 600)
        self.setWindowTitle("Сортировщик загрузок")
        # Окно открывается там и такого размера, где его закрыли (4.2). window.json рядом
        # с config.json, а не в нём: место окна - не настройка, и Config о нём не знает.
        self.window_path = config_path.with_name("window.json")
        saved = tk_window_state.load(self.window_path)
        window_geometry.restore(self, saved.get("qt") if isinstance(saved, dict) else None)

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

        if self.config.problems or self.config.notices:
            # Без правил всё уедет в Others. Сказать надо до «Применить», а не
            # после — иначе пользователь увидит последствия, а не причину.
            #
            # Поломка и уведомление отчитываются разными окнами: тревожный
            # значок у строки «правило в Others пропущено» гнал человека
            # чинить то, что и так верно (`util.settings_message`).
            title, text, broken = settings_message(
                self.config.problems, self.config.notices)
            (QMessageBox.warning if broken else QMessageBox.information)(
                self, title, text)

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
        # Набранный руками путь пересобирает план так же, как «Обзор…». Без
        # этого окно спокойно показывало папку B и таблицу с планом для папки A
        # (см. `do_apply`).
        self.path_edit.editingFinished.connect(self.preview)
        row.addWidget(self.path_edit, stretch=1)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self.browse_folder)
        open_btn = QPushButton("📂")
        open_btn.clicked.connect(self.open_downloads)
        clean_btn = QPushButton("🧹 Очистить")
        clean_btn.clicked.connect(self.preview)
        history_btn = QPushButton("🕘 История")
        history_btn.clicked.connect(self.show_history)
        rules_btn = QPushButton("⚙ Правила")
        rules_btn.clicked.connect(self.show_rules)
        row.addWidget(browse)
        row.addWidget(open_btn)
        row.addWidget(clean_btn)
        row.addWidget(history_btn)
        row.addWidget(rules_btn)
        return row

    def _row_3d(self):
        row = QHBoxLayout()
        self.to_3d = QCheckBox("3D-модели → отдельная папка")
        self.to_3d.setChecked(bool(self.config.external_3d.get("enabled", False)))
        self.to_3d.stateChanged.connect(self._on_3d_toggle)
        row.addWidget(self.to_3d)
        self.path_3d_edit = QLineEdit(self.config.external_3d.get("path", ""))
        self.path_3d_edit.setPlaceholderText("Путь к папке для 3D-моделей (3mf/obj/stl/gcode)")
        self.path_3d_edit.editingFinished.connect(self.preview)
        # Поле остаётся живым и со снятой галочкой. Галочка решает только одно —
        # уезжают ли модели из загрузок; разбор самого корня All_3d по подпапкам
        # расширений идёт всегда, пока путь задан. Отключённое поле обещало
        # обратное: настройка выглядит выключенной, а файлы в All_3d при каждой
        # уборке продолжают переезжать, и убрать путь через окно нельзя — только
        # правкой config.json руками. Про негодный путь окно в этом состоянии
        # ещё и предупреждает («Папка 3D не разбирается»), то есть указывает на
        # поле, которое само же и запретило трогать.
        row.addWidget(self.path_3d_edit, stretch=1)
        self.browse_3d_btn = QPushButton("Обзор…")
        self.browse_3d_btn.clicked.connect(self.browse_3d_folder)
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
        # Правый клик по файлу: «всегда класть в…». Пишет my_rules.json сразу —
        # подтверждать нечего, результат виден в той же строке плана.
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        return self.table

    def _footer(self):
        row = QHBoxLayout()
        self.move_enabled = QCheckBox("Перемещать файлы")
        row.addWidget(self.move_enabled)
        self.resort = QCheckBox("Переразложить старое")
        self.resort.setToolTip(
            "Проверить заново и то, что программа уже разложила\n"
            "по своим папкам — чтобы новые категории и шаблоны\n"
            "применились к старым загрузкам.\n\n"
            "Чужие папки (распакованные архивы, миры игр,\n"
            "репозитории) не трогаются в любом случае."
        )
        self.resort.stateChanged.connect(lambda _: self.preview())
        row.addWidget(self.resort)
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
        # Поле пути не гасим: оно управляет разбором самой All_3d, который идёт
        # независимо от галочки (см. `_row_3d`).
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
        """Пишет настройки в config.json. Пустое поле пути не сохраняет.

        Поле можно очистить одним Ctrl+A и Delete — промахнулся мимо «Обзор…»,
        начал править и передумал, случайно зацепил. Дальше окно честно говорит
        «Папка не найдена», и это выглядит как ошибка одного прогона; на самом
        деле закрытие окна писало пустую строку в config.json поверх
        единственной копии пути. Следующий запуск уже не знал, какую папку
        человек выбирал: `Config.load` жаловался на ненайденную настройку и
        подставлял `~/Downloads`. Возвращать было неоткуда.

        Пустая строка настройкой не бывает никогда, поэтому её просто не
        записываем — остаётся то, с чем окно открылось. Ненайденная папка это
        не касается: диск могли отключить, флешку вынуть, и стирать из-за
        этого настроенный путь нельзя тем более.
        """
        self._sync_config()
        if not self.config.downloads_path:
            self.config.downloads_path = self.saved_path
        try:
            self.config.save(self.config_path)
        except OSError:
            return
        self.saved_path = self.config.downloads_path

    def open_downloads(self):
        path = self.path_edit.text().strip()
        # Пустая строка — это `Path(".")`, то есть папка самой программы: без
        # первой половины проверки кнопка открывала именно её.
        if not path or not Path(path).is_dir():
            QMessageBox.information(self, "Папка не найдена", "Укажи существующую папку.")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка", str(exc))

    def show_history(self):
        self._sync_config()
        root = Path(self.config.downloads_path)
        if not self.config.downloads_path or not root.is_dir():
            QMessageBox.information(self, "Папка не найдена", "Укажи существующую папку.")
            return
        dlg = HistoryDialog(self.config, self)
        dlg.exec()
        # После возможной отмены файлы вернулись — пересобираем план.
        self.preview()

    def show_rules(self):
        dlg = RulesDialog(self.config, [mv.src.name for mv in self.moves], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._write_rules(dlg.edits)

    def rule_menu(self, row: int):
        """Меню правил для строки плана. None — строки нет."""
        if not 0 <= row < len(self.moves):
            return None
        name = self.moves[row].src.name
        menu = QMenu(self)
        into = menu.addMenu("Всегда класть в")
        for category in self.config.categories:
            action = into.addAction(category)
            action.triggered.connect(
                lambda _=False, c=category: self.set_file_rule(name, c))
        if user_rules.file_rule(self.config.user_rules, name):
            action = menu.addAction("Убрать моё правило")
            action.triggered.connect(lambda _=False: self.clear_file_rule(name))
        return menu

    def _table_menu(self, pos):
        menu = self.rule_menu(self.table.rowAt(pos.y()))
        if menu is not None:
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def set_file_rule(self, name: str, category: str):
        self._change_rules(user_rules.set_file, name, category)

    def clear_file_rule(self, name: str):
        self._change_rules(user_rules.clear_file, name)

    def _change_rules(self, fn, *args):
        try:
            edits = fn(self.config.user_rules, self.config.base, *args)
        except ValueError as exc:
            QMessageBox.information(self, "Правило не задано", str(exc))
            return
        self._write_rules(edits)

    def _write_rules(self, edits) -> bool:
        """Пишет my_rules.json, перечитывает правила и перестраивает план.

        На место нечитаемого файла не пишем: там правки человека, взять их
        больше неоткуда (`user_rules.read`).
        """
        if self.config.user_rules_unreadable:
            QMessageBox.warning(
                self, "Правила не сохранены",
                f"{user_rules.USER_RULES_FILENAME} не читается, и окно не пишет "
                "поверх, чтобы не стереть правки. Поправь его в редакторе или "
                "удали, и попробуй снова.")
            return False
        try:
            user_rules.write(user_rules.path_for(self.config_path), edits)
        except OSError as exc:
            QMessageBox.critical(self, "Правила не сохранены", str(exc))
            return False
        self.config = Config.load(self.config_path)
        self.preview()
        return True

    def _plan_key(self) -> tuple:
        """Всё, от чего зависит план: обе папки и обе галочки.

        Нужен, чтобы «Применить» могло заметить, что показанный план построен
        не для того, что сейчас написано в полях (см. `do_apply`).
        """
        return (self.path_edit.text().strip(), self.path_3d_edit.text().strip(),
                self.to_3d.isChecked(), self.resort.isChecked())

    def preview(self, deep: bool = False):
        """Строит план. Глубину задаёт галочка «Переразложить старое».

        Папка All_3d разбирается в любом случае, пока задан её путь.

        Аргумент оставлен, потому что `clicked` у кнопки приносит с собой `bool`:
        слот принимает его и складывает с галочкой, а не подменяет ею глубину.
        """
        deep = deep or self.resort.isChecked()
        self._sync_config()
        self._planned_for = self._plan_key()
        root = Path(self.config.downloads_path)
        if not self.config.downloads_path or not root.is_dir():
            self.table.setRowCount(0)
            self.moves = []
            self.plan_state = PLAN_NO_FOLDER
            self.unread_tail = ""
            self.status.setText("Папка не найдена — укажи существующий путь.")
            return
        # Папки, которые не удалось прочитать: права, отключённый сетевой диск,
        # вынутая флешка. Их файлы в план не попали, и без этой строки «План
        # готов: 0 шт.» неотличим от прибранных загрузок — тот самый исход, ради
        # которого окно вообще научили говорить «Папка не найдена». Здесь он
        # тише: папка на месте, путь верный, а половины файлов в плане нет.
        #
        # В строку — счёт, в подсказку мышью — сами имена: строка состояния
        # короткая, а чинить всё равно идут в проводник. Так же устроен и
        # список непрочитанных журналов в «🕘 Истории».
        unread: list[str] = []
        self.moves = build_plan(
            self.config, send_3d_external=self.to_3d.isChecked(), deep=deep,
            problems=unread)
        self.table.setRowCount(len(self.moves))
        for r, mv in enumerate(self.moves):
            self.table.setItem(r, 0, QTableWidgetItem(rel_to(mv.src, root)))
            target = rel_to(mv.dst, root)
            if mv.note:
                target = f"{target}   ({mv.note})"
            self.table.setItem(r, 1, QTableWidgetItem(target))
        # Галочка «3D → отдельная папка» без годного пути не делает ничего:
        # модели уезжают в обычные категории, и по плану это видно только тому,
        # кто помнит, куда они должны были поехать. При старте о такой настройке
        # предупреждает `Config`, но галочку жмут и посреди работы, а путь
        # правят прямо в поле рядом.
        #
        # Текст берётся общий на три интерфейса (`external_3d_warning`): своё
        # условие здесь знало только про пустое поле и молчало о пути без
        # диска — а тот не выключает вынос, а уводит модели в рабочую папку
        # программы.
        warning = external_3d_warning(self.config, self.to_3d.isChecked())
        tail = f"   {warning}" if warning else ""
        # Ту же строку дописывает и итог применения. `do_apply` зовёт `preview`
        # и тут же затирает его строку — нарочно, чтобы последнее слово
        # осталось за тем, что случилось с файлами, — а вместе со строкой
        # уезжала и жалоба. Выходило, что в момент, когда человек читает отчёт,
        # окно докладывает о безупречном прогоне: «ошибок: 0», при том что
        # файлы непрочитанной папки лежат неразобранными. Ошибкой это не
        # считается и в список неудач не попадает: в плане их не было вовсе.
        self.unread_tail = (f"   Не прочитано папок: {len(unread)} — их файлы "
                            "в план не попали." if unread else "")
        tail += self.unread_tail
        self.status.setToolTip("\n".join(unread))
        self.plan_state = PLAN_EMPTY if not self.moves else ""
        self.status.setText(f"План готов: {len(self.moves)} шт.{tail}")

    def closeEvent(self, e):
        self._save_settings()
        self._save_window()
        super().closeEvent(e)

    def _save_window(self):
        """Ключ qt в window.json; ключи старого окна Tkinter (--tk) остаются как были."""
        saved = tk_window_state.load(self.window_path)
        state = saved if isinstance(saved, dict) else {}
        state["qt"] = window_geometry.encode(self)
        tk_window_state.save(self.window_path, state)

    def do_apply(self):
        """Выполняет показанный план.

        План, переставший соответствовать полям, пересобирается перед
        подтверждением. Строит его «🧹 Очистить», а поля правятся руками и
        никого об этом не спрашивают: `preview` зовут «Обзор…» и обе галочки, а
        набранный текст не звал ничего. Между двумя нажатиями окно поэтому
        спокойно показывало папку B, таблицу с планом для папки A и кнопку,
        которая применит именно A; подтверждение спрашивает «Переместить 5
        файлов?» и папку не называет, так что заметить подмену не по чему.

        Хуже последствий вторая половина. Журнал отмены ложится туда, откуда
        унесли файлы, — в A; следом `_save_settings` записывает в config.json
        уже B, и «🕘 История» смотрит в B. То есть только что сделанную
        сортировку окном не отменить вовсе.

        Само по себе пустое «Применить» плана не строит: два шага — сначала
        посмотреть, потом применить — это и есть защита от случайного нажатия.
        """
        if self.moves and self._planned_for != self._plan_key():
            self.preview()
        if not self.moves:
            # Три разных «двигать нечего» — три разных ответа. Общий с окном
            # Tkinter, чтобы совет не расходился между интерфейсами.
            QMessageBox.information(self, *nothing_to_apply(self.plan_state))
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
        # Сначала новый план, потом итог: `preview` пишет в ту же строку
        # состояния, и поставленный раньше «Перемещено: 7» она затирала на
        # «План готов: 0 шт.». Последнее слово должно оставаться за тем, что
        # только что произошло с файлами.
        self.preview()
        # В строку статуса — короткий итог, в окно — полный отчёт с именами.
        # Само число ошибок ни о чём не говорит: какой файл не переехал и
        # почему, видно только из списка (`util.report`).
        self.status.setText(f"Перемещено: {result.moved}, ошибок: "
                            f"{len(result.errors)}{self.unread_tail}")
        # Заголовок общий с окном Tkinter (`util.report_title`): тревожный
        # значок из окна уходит вместе с окном, а слово «Готово» над списком
        # неудач остаётся в уведомлениях Windows.
        (QMessageBox.warning if result.errors or result.notes or result.undo_failed
         else QMessageBox.information)(self, report_title(result), report(result))


def launch(config_path: Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    # Тот же расчёт BASE_DIR, что и в main.py: рядом с .exe в сборке,
    # рядом с исходниками при запуске из Python. Без иконки на уровне
    # приложения окно и диалог истории брали стандартную иконку Qt.
    base_dir = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path(__file__).parent.parent)
    icon_path = base_dir / "icon.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = GlassWindow(config_path)
    win.show()
    app.exec()
