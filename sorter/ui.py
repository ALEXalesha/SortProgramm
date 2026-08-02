"""Окно Tkinter в стиле Mouzi: кнопка очистки, превью плана, применить."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

from .config import Config
from .planner import build_plan, Move
from .mover import apply
from .util import rel_to as _rel_to


class SorterApp:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.config_path = config_path
        self.config = Config.load(config_path)
        self.moves: list[Move] = []

        root.title("Сортировщик загрузок")
        root.geometry("760x520")
        root.minsize(620, 420)

        self._build_header(root)
        self._build_table(root)
        self._build_footer(root)

    # --- разметка ---

    def _build_header(self, root):
        top = ttk.Frame(root, padding=12)
        top.pack(fill="x")

        self.path_var = tk.StringVar(value=self.config.downloads_path)
        ttk.Label(top, text="Папка:").pack(side="left")
        ttk.Label(top, textvariable=self.path_var, foreground="#555").pack(side="left", padx=6)

        ttk.Button(top, text="📂 Открыть", command=self.open_downloads).pack(side="right")
        ttk.Button(top, text="🧹 Очистить", command=self.preview).pack(side="right", padx=6)

    def _build_table(self, root):
        frame = ttk.Frame(root, padding=(12, 0))
        frame.pack(fill="both", expand=True)

        cols = ("file", "dest")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        self.tree.heading("file", text="Файл")
        self.tree.heading("dest", text="Куда поедет")
        self.tree.column("file", width=320)
        self.tree.column("dest", width=380)

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _build_footer(self, root):
        bottom = ttk.Frame(root, padding=12)
        bottom.pack(fill="x")

        self.move_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text="Перемещать файлы (иначе только показ)",
            variable=self.move_enabled,
        ).pack(side="left")

        self.to_3d = tk.BooleanVar(value=bool(self.config.external_3d.get("enabled", False)))
        ttk.Checkbutton(
            bottom, text="3D-модели → All_3d",
            variable=self.to_3d, command=self.preview,
        ).pack(side="left", padx=12)

        self.resort = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text="Переразложить старое",
            variable=self.resort, command=self.preview,
        ).pack(side="left")

        self.apply_btn = ttk.Button(bottom, text="Применить", command=self.do_apply)
        self.apply_btn.pack(side="right")

        self.status = tk.StringVar(value="Нажми «Очистить», чтобы построить план.")
        ttk.Label(root, textvariable=self.status, padding=(12, 0, 12, 10), foreground="#333").pack(fill="x")

    # --- действия ---

    def open_downloads(self):
        path = self.config.downloads_path
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                os.system(f'xdg-open "{path}"')
        except OSError as exc:
            messagebox.showerror("Ошибка", str(exc))

    def preview(self):
        # Галочка «Переразложить старое» включает разбор папок, которые программа
        # создала сама. Чужие папки не трогаются ни в каком режиме.
        self.moves = build_plan(
            self.config,
            send_3d_external=self.to_3d.get(),
            deep=self.resort.get(),
        )
        self.tree.delete(*self.tree.get_children())
        root = Path(self.config.downloads_path)
        for mv in self.moves:
            dest = _rel_to(mv.dst, root)
            if mv.note:
                dest = f"{dest}   ({mv.note})"
            self.tree.insert("", "end", values=(_rel_to(mv.src, root), dest))
        self.status.set(f"План готов: {len(self.moves)} шт. к перемещению.")

    def do_apply(self):
        if not self.moves:
            messagebox.showinfo("Нет плана", "Сначала нажми «Очистить».")
            return
        if not self.move_enabled.get():
            messagebox.showinfo(
                "Режим показа",
                "Включи галочку «Перемещать файлы», чтобы применить.",
            )
            return
        if not messagebox.askyesno("Подтверждение", f"Переместить {len(self.moves)} файлов?"):
            return

        result = apply(self.moves, self.config, dry_run=False)
        msg = f"Перемещено: {result.moved}, ошибок: {len(result.errors)}"
        self.status.set(msg)
        if result.errors:
            messagebox.showwarning("Готово с ошибками", msg)
        else:
            messagebox.showinfo("Готово", msg)
        self.preview()


def launch(config_path: Path) -> None:
    root = tk.Tk()
    SorterApp(root, config_path)
    root.mainloop()
