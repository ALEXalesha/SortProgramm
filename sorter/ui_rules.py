"""Диалог «Правила раскладки»: категории и их слова из окна.

Диалог правит копию правок и файлов не пишет: «Сохранить» отдаёт правки окну,
«Отмена» просто закрывается. Запись и перестройка плана — дело окна
(`GlassWindow._write_rules`), одно место на диалог и контекстное меню.
"""
from __future__ import annotations

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)

from . import user_rules as ur

MINE = "  ✎"


class RulesDialog(QDialog):
    def __init__(self, config, names: list[str], parent=None):
        super().__init__(parent)
        self.config = config
        # Имена файлов текущего плана: по ним считается, сколько заберёт слово.
        self.names = list(names)
        self.edits = ur.norm(copy.deepcopy(config.user_rules))
        self.setWindowTitle("Правила раскладки")
        self.resize(680, 460)

        self.cats = QListWidget()
        self.cats.currentRowChanged.connect(lambda *_: self._fill_words())
        self.words = QListWidget()
        self.word_edit = QLineEdit()
        self.word_edit.setPlaceholderText("Новое слово для выбранной категории")
        self.word_edit.textChanged.connect(self._preview_word)
        self.word_edit.returnPressed.connect(self.add_word)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(QLabel("Категории (выше — главнее)"))
        left.addWidget(self.cats, stretch=1)
        row = QHBoxLayout()
        for text, slot in (("Добавить", self.add_category),
                           ("Переименовать", self.rename_category),
                           ("Удалить", self.remove_category)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            row.addWidget(button)
        left.addLayout(row)

        right = QVBoxLayout()
        right.addWidget(QLabel("Слова: достаточно, чтобы слово было частью имени файла"))
        right.addWidget(self.words, stretch=1)
        add_row = QHBoxLayout()
        add_row.addWidget(self.word_edit, stretch=1)
        add_btn = QPushButton("Добавить слово")
        add_btn.clicked.connect(self.add_word)
        add_row.addWidget(add_btn)
        right.addLayout(add_row)
        remove_btn = QPushButton("Убрать выбранное слово")
        remove_btn.clicked.connect(self.remove_word)
        right.addWidget(remove_btn)

        body = QHBoxLayout()
        body.addLayout(left, stretch=1)
        body.addLayout(right, stretch=1)

        footer = QHBoxLayout()
        footer.addWidget(QLabel(f"{MINE.strip()} — добавлено тобой"))
        footer.addStretch(1)
        save = QPushButton("Сохранить")
        save.clicked.connect(self.accept)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        footer.addWidget(save)
        footer.addWidget(cancel)

        root = QVBoxLayout(self)
        root.addLayout(body, stretch=1)
        root.addWidget(self.hint)
        root.addLayout(footer)
        self._fill_categories()

    # --- состояние ---

    def current(self):
        return ur.apply(self.config, self.edits)

    def category_names(self) -> list[str]:
        return list(self.current().categories)

    def selected(self) -> str | None:
        item = self.cats.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, name: str) -> None:
        for row in range(self.cats.count()):
            if self.cats.item(row).data(Qt.ItemDataRole.UserRole) == name:
                self.cats.setCurrentRow(row)
                return

    def word_names(self) -> list[str]:
        return [self.words.item(r).data(Qt.ItemDataRole.UserRole)
                for r in range(self.words.count())]

    def select_word(self, word: str) -> None:
        self.words.setCurrentRow(self.word_names().index(word))

    def is_mine(self, word: str) -> bool:
        name = self.selected()
        key = ur._find(self.edits["categories"], name) if name else None
        added = self.edits["categories"][key]["add"] if key else []
        return ur._has_word(added, word)

    def _category_is_mine(self, name: str) -> bool:
        """Создана или переименована человеком."""
        return ur._origin(self.config.base, self.edits, name) is None or any(
            ur.folder_key(new) == ur.folder_key(name)
            for new in self.edits["renamed"].values())

    # --- списки ---

    def _item(self, raw: str, mine: bool) -> QListWidgetItem:
        item = QListWidgetItem(raw + (MINE if mine else ""))
        item.setData(Qt.ItemDataRole.UserRole, raw)
        if mine:
            font = QFont()
            font.setItalic(True)
            item.setFont(font)
            item.setToolTip("Добавлено тобой, а не пришло с программой")
        return item

    def _fill_categories(self, select: str | None = None) -> None:
        keep = select or self.selected()
        names = self.category_names()
        self.cats.blockSignals(True)
        self.cats.clear()
        for name in names:
            self.cats.addItem(self._item(name, self._category_is_mine(name)))
        self.cats.blockSignals(False)
        found = ur._find(names, keep) if keep else None
        if found:
            self.select(found)
        elif self.cats.count():
            self.cats.setCurrentRow(0)
        self._fill_words()

    def _fill_words(self) -> None:
        self.words.clear()
        name = self.selected()
        if name is None:
            return
        for word in self.current().categories.get(name, []):
            self.words.addItem(self._item(word, self.is_mine(word)))
        self._preview_word(self.word_edit.text())

    # --- действия ---

    def _do(self, fn, *args) -> bool:
        try:
            self.edits = fn(self.edits, self.config.base, *args)
        except ValueError as exc:
            self.hint.setText(str(exc))
            return False
        self.hint.setText("")
        return True

    def add_category(self) -> None:
        name, ok = QInputDialog.getText(self, "Новая категория", "Имя категории:")
        if ok and self._do(ur.add_category, name):
            self._fill_categories(select=name)

    def rename_category(self) -> None:
        old = self.selected()
        if old is None:
            return
        new, ok = QInputDialog.getText(self, "Переименовать", "Новое имя:", text=old)
        if ok and self._do(ur.rename_category, old, new):
            self._fill_categories(select=new)

    def remove_category(self) -> None:
        name = self.selected()
        if name is not None and self._do(ur.remove_category, name):
            self._fill_categories()

    def add_word(self) -> None:
        name = self.selected()
        word = self.word_edit.text()
        if name is not None and self._do(ur.add_word, name, word):
            self.word_edit.clear()
            self._fill_words()

    def remove_word(self) -> None:
        name = self.selected()
        item = self.words.currentItem()
        if name is None or item is None:
            return
        if self._do(ur.remove_word, name, item.data(Qt.ItemDataRole.UserRole)):
            self._fill_words()

    def _preview_word(self, text: str) -> None:
        """Что сделает слово с текущим планом — до того, как его добавили."""
        name = self.selected()
        if not text.strip() or name is None:
            self.hint.setText("")
            return
        try:
            trial = ur.add_word(self.edits, self.config.base, name, text)
        except ValueError as exc:
            self.hint.setText(str(exc))
            return
        moved = ur.moved_by(self.names, self.current(), ur.apply(self.config, trial))
        if not moved:
            # «Таких файлов нет» врало бы про слово, которое в плане есть: файл
            # с ним может и так ехать сюда, или его держит правило или шаблон.
            hits = sum(text.lower() in name.lower() for name in self.names)
            self.hint.setText(
                f"Файлов с этим словом в плане: {hits}, но куда они едут, не изменится."
                if hits else "В текущем плане таких файлов нет.")
            return
        parts = ", ".join(f"{k}: {v}" for k, v in moved.most_common())
        self.hint.setText(f"Заберёт из плана: {sum(moved.values())} ({parts}).")
