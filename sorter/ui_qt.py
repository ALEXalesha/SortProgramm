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
    QLineEdit, QFileDialog, QDialog,
)

from .classifier import find_override
from .config import Config
from .scanner import scan
from .planner import build_plan, external_3d_warning, goes_by_extension, Move
from .mover import apply
from .util import rel_to, listing, report
from . import ai
from . import history


class _AiWorker(QThread):
    """Фоновый запрос к DeepSeek, чтобы окно не зависало.

    Длинный список уходит пачками (`ai.classify_many`), поэтому по дороге
    прилетает прогресс — иначе на сотне имён окно молчит полминуты и кажется
    зависшим.
    """
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int)

    def __init__(self, filenames, categories, api_key, hints=None, fallback="Others"):
        super().__init__()
        self._filenames = filenames
        self._categories = categories
        self._api_key = api_key
        self._hints = hints or {}
        self._fallback = fallback
        self._stopped = False

    def stop(self):
        """Просит бросить остаток списка. Запрос в полёте не прерывает."""
        self._stopped = True

    def run(self):
        try:
            result = ai.classify_many(
                self._filenames,
                self._categories,
                self._api_key,
                on_progress=lambda done, total: self.progress.emit(done, total),
                should_stop=lambda: self._stopped,
                hints=self._hints,
                fallback=self._fallback,
            )
            self.done.emit(result)
        except Exception as exc:  # сеть, ключ, разбор — наружу как текст
            self.failed.emit(str(exc))

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
        self.ops = history.list_operations(self.downloads_path)
        self.table.setRowCount(len(self.ops))
        for r, op in enumerate(self.ops):
            when = op.when.strftime("%d.%m.%Y  %H:%M:%S")
            self.table.setItem(r, 0, QTableWidgetItem(when))
            self.table.setItem(r, 1, QTableWidgetItem(str(op.count)))
        empty = not self.ops
        self.undo_btn.setEnabled(not empty)
        self.hint.setText(
            "История пуста — ещё ничего не перемещалось." if empty
            else f"Записей: {len(self.ops)}. Выбери строку, чтобы откатить.")
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

        if self.config.problems:
            # Без правил всё уедет в Others. Сказать надо до «Применить», а не
            # после — иначе пользователь увидит последствия, а не причину.
            QMessageBox.warning(
                self, "Настройки прочитаны не полностью",
                "\n".join(self.config.problems)
                + "\n\nПрограмма запустилась, но раскладка может быть неверной.")

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
        history_btn = QPushButton("🕘 История")
        history_btn.clicked.connect(self.show_history)
        row.addWidget(browse)
        row.addWidget(open_btn)
        row.addWidget(clean_btn)
        row.addWidget(self.ai_btn)
        row.addWidget(history_btn)
        return row

    def _row_3d(self):
        row = QHBoxLayout()
        self.to_3d = QCheckBox("3D-модели → отдельная папка")
        self.to_3d.setChecked(bool(self.config.external_3d.get("enabled", False)))
        self.to_3d.stateChanged.connect(self._on_3d_toggle)
        row.addWidget(self.to_3d)
        self.path_3d_edit = QLineEdit(self.config.external_3d.get("path", ""))
        self.path_3d_edit.setPlaceholderText("Путь к папке для 3D-моделей (3mf/obj/stl/gcode)")
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
        self._sync_config()
        try:
            self.config.save(self.config_path)
        except OSError:
            pass

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

    def preview(self, deep: bool = False):
        """Строит план.

        Глубину задаёт либо аргумент (после ИИ она всегда полная), либо галочка
        «Переразложить старое». Папка All_3d разбирается в любом случае.
        """
        deep = deep or self.resort.isChecked()
        self._sync_config()
        root = Path(self.config.downloads_path)
        if not self.config.downloads_path or not root.is_dir():
            self.table.setRowCount(0)
            self.moves = []
            self.status.setText("Папка не найдена — укажи существующий путь.")
            return
        self.moves = build_plan(
            self.config, send_3d_external=self.to_3d.isChecked(), deep=deep)
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
        self.status.setText(f"План готов: {len(self.moves)} шт.{tail}")

    def _save_overrides(self) -> str:
        """Пишет overrides.json. Возвращает текст ошибки или пустую строку.

        Раньше OSError глотался целиком, и это худший вид молчания: правила
        живут в памяти до закрытия окна, поэтому и план, и раскладка выглядят
        как надо. Пропадает ответ ИИ уже потом — при следующем запуске, когда
        связать пропажу с той кнопкой не с чем.
        """
        path = self.config_path.with_name("overrides.json")
        try:
            path.write_text(
                json.dumps(self.config.overrides, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except OSError as exc:
            return str(exc)
        return ""

    def _has_rule(self, filename: str) -> bool:
        """Есть ли для имени готовое правило в overrides.

        Ищем ровно тем же способом, каким его найдёт разбор
        (`classifier.find_override`): точное имя, имя без служебного номера и
        то же самое по правилам файловой системы. Правило `клип.mp4` покрывает
        и `клип (1).mp4` — номер приписала сама программа при конфликте имён,
        файл от этого другим не стал, и вопрос про него уже оплачен.

        Расходиться с разбором тут нельзя ни в какую сторону: спросим лишнего —
        заплатим за правило, которое уже есть; не спросим нужного — файл
        останется неразобранным, а окно отчитается «нечего разбирать».
        """
        return find_override(self.config.overrides, filename) is not None

    def run_ai(self):
        """Спрашивает DeepSeek про то же, что разбирает обычная уборка.

        Глубину задаёт галочка «Переразложить старое», как и у «🧹 Очистить».
        Раньше кнопка всегда уходила вглубь, и на разобранной папке это било
        дважды: запрос раздувался с десятка имён до тысячи, а правила для уже
        разложенных файлов оседали в overrides.json и перетасовывали папки,
        которые никто не просил трогать.

        Имена, у которых правило уже есть, в запрос не уходят. Ответ модели их
        всё равно не трогает (`_ai_done` бережёт решение, принятое руками), а
        деньги и минуты за них платились наравне со всеми: на разобранной папке
        второе нажатие «✨ИИ» превращалось в оплаченную пустышку — запрос на
        сотню имён и «ИИ разложил 0 шт.» в ответ.

        Модели, которые поедут во внешнюю папку 3D, не уходят в запрос по той же
        причине. Место им выбирает расширение, категория при этом не
        спрашивается вовсе (`planner.goes_by_extension`), так что ответ модели
        оседает в overrides.json и не делает ничего. Заметить это было нельзя:
        окно отчитывалось «ИИ разложил 30 шт.», а в плане те же тридцать строк
        стояли с пометкой «по расширению» — отчёт спорил с планом, лежащим
        рядом. Правило вдобавок пустое по смыслу: каждое расширение из
        `external_3d.extensions` и так стоит словом в категории «3D» (это
        держит тест `test_every_external_3d_extension_is_a_3d_keyword`), то есть
        модель платно повторяла то, что правила знают и без неё.

        Пустое поле пути отбивается отдельно: `Path("")` — это текущая папка, и
        `is_dir()` на ней отвечает True. Без этой проверки ИИ разбирал папку
        самой программы — её имена уходили в DeepSeek, ответы записывались
        правилами.
        """
        self._sync_config()
        root = Path(self.config.downloads_path)
        if not self.config.downloads_path or not root.is_dir():
            QMessageBox.information(self, "Папка не найдена", "Укажи существующую папку.")
            return
        key = ai.load_api_key(self.config_path.parent)
        if not key:
            QMessageBox.warning(
                self, "Нет ключа",
                "Положи ключ в файл deepseek_key.txt рядом с программой\n"
                "или задай переменную окружения DEEPSEEK_API_KEY.")
            return
        files = scan(
            self.config.downloads_path, self.config, deep=self.resort.isChecked())
        # Одно имя — один вопрос. Ключ в overrides.json это имя без пути, поэтому
        # второй `клип.mp4` из соседней папки не добавляет вопросу ничего: ответ
        # будет тот же и распространится на оба файла. Раньше повторы уходили в
        # запрос по разу на файл — лишние деньги, лишние пачки, и счёт
        # «спрашиваю по N именам» из-за них врал.
        seen = list(dict.fromkeys(f.name for f in files))
        to_3d = self.to_3d.isChecked()
        by_ext = [n for n in seen if goes_by_extension(n, self.config, to_3d)]
        askable = [n for n in seen if n not in set(by_ext)]
        names = [name for name in askable if not self._has_rule(name)]
        covered = len(askable) - len(names)
        # Почему часть имён не спрашиваем. Молчать нельзя: «спрашиваю по 3
        # именам» на папке из тридцати файлов выглядит как потерянный список.
        skipped = []
        if covered:
            skipped.append(f"{covered} уже с правилами")
        if by_ext:
            skipped.append(f"{len(by_ext)} поедут по расширению")
        reasons = ", ".join(skipped)
        if not names:
            self.status.setText(
                f"Нечего разбирать: {reasons}." if reasons
                else "Нечего разбирать.")
            return
        cats = list(self.config.categories.keys()) + [self.config.fallback_category]
        # Сколько имён ушло в запрос. Ответ приходит один, без вопроса, а
        # посчитать оставшихся без решения можно только сравнив одно с другим.
        self._ai_asked = len(names)
        self.ai_btn.setEnabled(False)
        tail = f" ({reasons} — не спрашиваем)" if reasons else ""
        self.status.setText(f"Спрашиваю DeepSeek по {len(names)} именам…{tail}")
        self._worker = _AiWorker(
            names, cats, key, self.config.category_hints,
            self.config.fallback_category)
        self._worker.done.connect(self._ai_done)
        self._worker.failed.connect(self._ai_failed)
        self._worker.progress.connect(self._ai_progress)
        self._worker.start()

    def _ai_progress(self, done, total):
        self.status.setText(f"DeepSeek: пачка {done} из {total}…")

    def _ai_done(self, mapping):
        self.ai_btn.setEnabled(True)
        if not mapping:
            self.status.setText("ИИ не вернул результатов.")
            return
        # «Others» от модели — это «не знаю», а не решение. Правилом не пишем:
        # оно встало бы выше ключевых слов и закрыло файлу дорогу навсегда.
        rules = ai.useful_rules(mapping, self.config.fallback_category)
        # Правило, которое уже есть, не трогаем. `update` не спрашивал, было ли
        # там что-то, и решение, принятое руками, молча заменялось мнением
        # модели — включая разобранный в README случай, где `Puck_Launcher.step`
        # уезжает в «Игры» по слову launcher. Отменить это нечем: у
        # overrides.json нет ни истории, ни журнала отмены, а следующее нажатие
        # «✨ИИ» стирало починку снова. Файлы без правила модель разбирает
        # по-прежнему, поэтому повторный запрос после новых категорий работает
        # как работал.
        kept = [name for name in rules if name in self.config.overrides]
        fresh = {n: c for n, c in rules.items() if n not in self.config.overrides}
        self.config.overrides.update(fresh)
        failure = self._save_overrides() if fresh else ""
        # Глубина — та же, что была у запроса: план должен показывать ровно те
        # файлы, про которые спрашивали. Жёсткое deep=True вытаскивало в план
        # всё разложенное, хотя новые правила касались только корня.
        #
        # План строится ДО отчёта: `preview` пишет в ту же строку состояния, и
        # поставленный раньше итог она затирала молча — оба вызова идут внутри
        # одного слота, окно между ними не перерисовывается. Других слов у
        # кнопки ИИ нет, окон сообщений она не показывает, так что человек
        # ждал запроса, платил за него и не узнавал о нём ничего.
        self.preview()
        # Без решения — это про вопрос, а не про ответ. Раньше считали
        # `len(mapping) - len(rules)`, то есть одни лишь «Others»: имена, про
        # которые модель промолчала или ответила чужим ключом (сверка такой
        # отбрасывает), не попадали никуда — ни в правила, ни в счёт. Окно
        # отчитывалось «ИИ разложил 1 шт.», и человек, спросивший про сорок
        # файлов и заплативший за все сорок, не узнавал, что тридцать девять
        # остались неразобранными.
        asked = getattr(self, "_ai_asked", len(mapping))
        undecided = max(0, asked - len(rules))
        notes = []
        if undecided:
            notes.append(f"без решения: {undecided}")
        if kept:
            notes.append(f"свои правила сохранены: {len(kept)}")
        tail = f" ({'; '.join(notes)})" if notes else ""
        self.status.setText(f"ИИ разложил {len(fresh)} шт.{tail}")
        if failure:
            self.status.setText(
                f"ИИ разложил {len(fresh)} шт.{tail}, но правила не сохранены.")
            QMessageBox.warning(
                self, "Правила не сохранены",
                f"Ответ модели не удалось записать в overrides.json:\n{failure}\n\n"
                "Пока окно открыто, правила действуют, но после закрытия "
                "пропадут — запрос придётся повторить.")

    def _ai_failed(self, err):
        self.ai_btn.setEnabled(True)
        self.status.setText("Ошибка ИИ.")
        QMessageBox.warning(self, "Ошибка ИИ", err)

    def closeEvent(self, e):
        self._save_settings()
        self._stop_ai()
        super().closeEvent(e)

    def _stop_ai(self):
        """Дожидается фонового запроса к ИИ, если он ещё идёт.

        `_AiWorker` — это `QThread`, а Qt обрывает процесс, если объект потока
        уничтожается на ходу. Окно держит поток полем, поэтому цепочка была
        короткая: нажал «✨ИИ» на большой папке, передумал, закрыл окно — и
        вместо тихого выхода Windows показывал падение. Настройки к тому
        моменту уже сохранены, но выглядит это как поломка на ровном месте.

        Сначала просим бросить остаток списка, потом ждём. Запрос в полёте не
        прервать — там сидит `urlopen`, — но дольше одного таймаута ожидание
        не затянется, а обычно поток уходит сразу.
        """
        worker = getattr(self, "_worker", None)
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait()

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
        # Сначала новый план, потом итог: `preview` пишет в ту же строку
        # состояния, и поставленный раньше «Перемещено: 7» она затирала на
        # «План готов: 0 шт.». Последнее слово должно оставаться за тем, что
        # только что произошло с файлами.
        self.preview()
        # В строку статуса — короткий итог, в окно — полный отчёт с именами.
        # Само число ошибок ни о чём не говорит: какой файл не переехал и
        # почему, видно только из списка (`util.report`).
        self.status.setText(f"Перемещено: {result.moved}, ошибок: {len(result.errors)}")
        (QMessageBox.warning if result.errors or result.notes
         else QMessageBox.information)(self, "Готово", report(result))


def launch(config_path: Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = GlassWindow(config_path)
    win.show()
    app.exec()
