"""Tkinter desktop application for personal Italian vocabulary."""

import json
import queue
import shutil
import sqlite3
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from classifier import (ClassificationCancelled, SCHEMA_PATH,
                        build_prompt, load_json_strict, run_codex, validate_response)
from profiles import ProfileStore
from storage import ALL_STUDY_LEVELS, DIFFICULTIES, STUDY_LEVELS, VocabularyDB
from starter import LEVELS, load_starter_catalog, starter_counts
from study import StudySession, parse_session_size, recall_estimate, recall_label
from topics import ALL_TOPICS, TOPICS, topics_for_card


BG = "#0d1117"
SURFACE = "#171d26"
SURFACE_RAISED = "#202a36"
FIELD = "#111821"
BORDER = "#344252"
TEXT = "#f0f4f8"
MUTED = "#a7b4c5"
ACCENT = "#78b5ff"
ACCENT_HOVER = "#9ac8ff"
SELECTED = "#284b70"
SUCCESS = "#81d9b4"
WARNING = "#f0c478"
DANGER = "#ff9298"
FONT = "Noto Sans"
EVALUATION_BATCH_SIZE = 20
MAX_CONCURRENT_CODEX_JOBS = 5


def choose_profile(store, parent=None):
    """Show the first-run form or let an existing user open or create a profile."""
    window = tk.Tk() if parent is None else tk.Toplevel(parent)
    window.title("Choose vocabulary profile")
    window.geometry("510x430" if store.list_profiles() else "510x330")
    window.resizable(False, False)
    window.configure(bg=BG)
    if parent is not None:
        window.transient(parent)
        window.grab_set()
    selected = None
    content = tk.Frame(window, bg=BG, padx=24, pady=22)
    content.pack(fill="both", expand=True)
    tk.Label(content, text="Choose your vocabulary profile", bg=BG, fg=TEXT,
             font=(FONT, 18, "bold")).pack(anchor="w")
    tk.Label(content, text="Each profile has its own words, settings, and study history.",
             bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w", pady=(3, 17))

    profiles = store.list_profiles()
    if profiles:
        tk.Label(content, text="OPEN A PROFILE", bg=BG, fg=ACCENT,
                 font=(FONT, 9, "bold")).pack(anchor="w")
        current = tk.StringVar(value=store.active_profile()["name"] if store.active_profile() else
                               profiles[0]["name"])
        selector = ttk.Combobox(content, textvariable=current, state="readonly",
                                values=[item["name"] for item in profiles])
        selector.pack(fill="x", pady=(6, 7))

        def open_existing():
            nonlocal selected
            selected = next(item["id"] for item in profiles if item["name"] == current.get())
            try:
                store.database_path(selected)
                store.activate(selected)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Profile could not open", str(exc), parent=window)
                selected = None
                return
            window.destroy()

        ttk.Button(content, text="Open profile", command=open_existing).pack(anchor="w")

    tk.Label(content, text="CREATE A PROFILE", bg=BG, fg=ACCENT,
             font=(FONT, 9, "bold")).pack(anchor="w", pady=(18 if profiles else 0, 5))
    name_entry = ttk.Entry(content)
    name_entry.pack(fill="x")
    choice = tk.StringVar(value="default")
    count = store._preset()["card_count"]
    for value, label in (("default", f"Use the default deck ({count:,} ready cards)"),
                         ("empty", "Start from zero (add words later)")):
        tk.Radiobutton(content, text=label, variable=choice, value=value,
                       bg=BG, fg=TEXT, selectcolor=SURFACE_RAISED,
                       activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 10)).pack(anchor="w", pady=(5, 0))

    def create_new():
        nonlocal selected
        try:
            selected = store.create(name_entry.get(), choice.get())["id"]
        except (OSError, ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Profile could not be created", str(exc), parent=window)
            return
        window.destroy()

    ttk.Button(content, text="Create profile", style="Accent.TButton",
               command=create_new).pack(anchor="w", pady=(14, 0))
    name_entry.focus_set()
    window.protocol("WM_DELETE_WINDOW", window.destroy)
    if parent is None:
        window.mainloop()
    else:
        parent.wait_window(window)
    return selected


def _run_evaluation_batch(index, request, model, effort, timeout, cancel_event, events):
    if cancel_event.is_set():
        raise ClassificationCancelled("Evaluation cancelled.")
    events.put(("running", index))
    response = run_codex(request, model=model, effort=effort,
                         timeout=timeout, cancel_event=cancel_event)
    validate_response(response, request)
    return response


class VocabularyApp(tk.Tk):
    def __init__(self, profile_store, profile):
        super().__init__()
        self.profile_store = profile_store
        self.profile = profile
        self.next_profile_id = None
        self.title(f"Parola Vocab - {profile['name']}")
        self.geometry("1200x840")
        self.minsize(900, 630)
        self.db = VocabularyDB(profile_store.database_path(profile["id"]))
        self.events = queue.Queue()
        self.worker = None
        self._evaluation_in_progress = False
        self.cancel_event = None
        self._queued_recheck = False
        self._queued_include_unknown = False
        self.closing = False
        self.session = None
        self.detail_entry_id = None
        self.detail_loaded_revision = None
        self.detail_loaded_state = None
        self._refreshing_tree = False
        self._style()
        self._build()
        self._refresh_library()
        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.entry.focus_set()

    def _style(self):
        self.configure(bg=BG)
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=SURFACE)
        style.configure("Raised.TFrame", background=SURFACE_RAISED)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=(FONT, 24, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=(FONT, 10))
        style.configure("Status.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure("PanelTitle.TLabel", background=SURFACE, foreground=TEXT, font=(FONT, 13, "bold"))
        style.configure("PanelMuted.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 10))
        style.configure("Review.TLabel", background=SURFACE, foreground=WARNING, font=(FONT, 10))
        style.configure("Field.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 9, "bold"))
        style.configure("Eyebrow.TLabel", background=SURFACE, foreground=ACCENT, font=(FONT, 9, "bold"))
        style.configure("Card.TLabel", background=SURFACE, foreground=TEXT, font=(FONT, 30, "bold"))
        style.configure("Answer.TLabel", background=SURFACE_RAISED, foreground=TEXT, font=(FONT, 12))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("StatCaption.TLabel", background=SURFACE, foreground=MUTED, font=(FONT, 9, "bold"))
        for name, color in (("Unknown", ACCENT), ("Easy", SUCCESS),
                            ("Medium", WARNING), ("Hard", DANGER)):
            style.configure(f"{name}.Stat.TLabel", background=SURFACE,
                            foreground=color, font=(FONT, 21, "bold"))

        style.configure("TButton", background=SURFACE_RAISED, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=SURFACE_RAISED,
                        darkcolor=SURFACE_RAISED, relief="flat", borderwidth=1,
                        padding=(13, 8), font=(FONT, 10, "bold"))
        style.map("TButton", background=[("disabled", SURFACE_RAISED), ("pressed", SELECTED),
                                          ("active", "#2c3948")],
                  foreground=[("disabled", "#718093"), ("active", TEXT)])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG,
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                        padding=(16, 9), font=(FONT, 10, "bold"))
        style.map("Accent.TButton", background=[("disabled", SURFACE_RAISED),
                                                 ("pressed", "#5a9eea"), ("active", ACCENT_HOVER)],
                  foreground=[("disabled", "#718093"), ("active", BG)])
        style.configure("Danger.TButton", foreground=DANGER)
        style.map("Danger.TButton", foreground=[("active", "#ffc0c3")])
        style.configure("Success.TButton", foreground=SUCCESS)
        style.map("Success.TButton", foreground=[("active", "#b6f1d7")])

        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT,
                        background=FIELD, bordercolor=BORDER, lightcolor=BORDER,
                        darkcolor=BORDER, insertcolor=TEXT, padding=(10, 8), relief="flat")
        style.map("TEntry", bordercolor=[("focus", ACCENT)],
                  fieldbackground=[("disabled", SURFACE_RAISED)],
                  foreground=[("disabled", MUTED)])
        style.configure("Quick.TEntry", padding=(10, 7), font=(FONT, 11))
        style.configure("TCombobox", fieldbackground=FIELD, background=SURFACE_RAISED,
                        foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=(8, 7))
        style.map("TCombobox", fieldbackground=[("readonly", FIELD), ("focus", FIELD)],
                  foreground=[("readonly", TEXT)], bordercolor=[("focus", ACCENT)],
                  background=[("active", "#2c3948")])
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", SELECTED)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT,
                        indicatorbackground=FIELD, indicatorforeground=ACCENT,
                        bordercolor=BORDER, font=(FONT, 10))
        style.map("TCheckbutton", background=[("active", SURFACE)],
                  foreground=[("active", TEXT)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        borderwidth=0, padding=(18, 10), font=(FONT, 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", SURFACE_RAISED)],
                  foreground=[("selected", TEXT), ("active", TEXT)])
        style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, borderwidth=0, relief="flat", rowheight=32,
                        font=(FONT, 10))
        style.map("Treeview", background=[("selected", SELECTED)],
                  foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=SURFACE_RAISED, foreground=MUTED,
                        borderwidth=0, relief="flat", padding=(9, 9),
                        font=(FONT, 9, "bold"))
        style.map("Treeview.Heading", background=[("active", "#2c3948")])
        style.configure("Vertical.TScrollbar", background=SURFACE_RAISED,
                        troughcolor=SURFACE, bordercolor=SURFACE,
                        lightcolor=SURFACE_RAISED, darkcolor=SURFACE_RAISED,
                        arrowcolor=MUTED, width=11)
        style.configure("TPanedwindow", background=BG)
        style.configure("Sash", sashthickness=8, background=BG)

    def _build(self):
        outer = ttk.Frame(self, padding=(22, 18, 22, 14))
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 13))
        profile_label = self.profile["name"]
        if len(profile_label) > 24:
            profile_label = profile_label[:23] + "..."
        ttk.Button(heading, text=f"Profile: {profile_label}",
                   command=self._switch_profile).pack(side="right", anchor="n")
        title_area = ttk.Frame(heading)
        title_area.pack(side="left", fill="x", expand=True)
        ttk.Label(title_area, text="Parola Vocab", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_area, text="Capture a word now. Make it familiar later.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(1, 0))

        self.status = tk.StringVar(value="Ready. Entries are saved immediately.")
        ttk.Label(outer, textvariable=self.status, style="Status.TLabel").pack(
            side="bottom", fill="x", pady=(9, 0))
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        self.library_tab = ttk.Frame(self.tabs, padding=(0, 12, 0, 0))
        self.study_tab = ttk.Frame(self.tabs, padding=(0, 14, 0, 0))
        self.settings_tab = ttk.Frame(self.tabs, padding=(0, 14, 0, 0))
        self.tabs.add(self.library_tab, text="Library")
        self.tabs.add(self.study_tab, text="Study")
        self.tabs.add(self.settings_tab, text="Settings")
        self._build_library()
        self._build_study()
        self._build_settings()
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.bind("<space>", self._study_key)
        self.bind("<Key-r>", self._study_key)
        self.bind("<Key-a>", self._study_key)

    def _build_library(self):
        entry_bar = ttk.Frame(self.library_tab, style="Panel.TFrame", padding=(12, 8))
        entry_bar.pack(fill="x", pady=(0, 10))
        ttk.Label(entry_bar, text="ADD A WORD OR SHORT EXPRESSION",
                  style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 4))
        add_row = ttk.Frame(entry_bar, style="Panel.TFrame")
        add_row.pack(fill="x")
        self.entry = ttk.Entry(add_row, style="Quick.TEntry")
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self._add())
        ttk.Button(add_row, text="Add word", style="Accent.TButton",
                   command=self._add).pack(side="left", padx=(10, 0))

        stats = ttk.Frame(self.library_tab)
        stats.pack(fill="x", pady=(0, 10))
        self.count_vars = {}
        for index, difficulty in enumerate(DIFFICULTIES):
            stats.columnconfigure(index, weight=1)
            card = ttk.Frame(stats, style="Panel.TFrame", padding=(16, 9))
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 5,
                                                                  0 if index == 3 else 5))
            ttk.Label(card, text=difficulty.upper(), style="StatCaption.TLabel").pack(anchor="w")
            number = tk.StringVar(value="0")
            self.count_vars[difficulty] = number
            ttk.Label(card, textvariable=number,
                      style=f"{difficulty.title()}.Stat.TLabel").pack(anchor="w", pady=(0, 1))

        controls = ttk.Frame(self.library_tab, style="Panel.TFrame", padding=(12, 8))
        controls.pack(fill="x", pady=(0, 10))
        ttk.Label(controls, text="SEARCH LIBRARY", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        self.search = tk.StringVar()
        search_entry = ttk.Entry(controls, textvariable=self.search, width=24)
        search_entry.grid(row=0, column=1, sticky="w", padx=(0, 16))
        self.search.trace_add("write", lambda *_: self._refresh_library())
        ttk.Label(controls, text="SHOW", style="Field.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 7))
        self.library_filters = {}
        for index, difficulty in enumerate(DIFFICULTIES, 3):
            variable = tk.BooleanVar(value=True)
            self.library_filters[difficulty] = variable
            ttk.Checkbutton(controls, text=difficulty.title(), variable=variable,
                            command=self._refresh_library).grid(row=0, column=index, sticky="w", padx=5)
        starter_filter_row = ttk.Frame(controls, style="Panel.TFrame")
        starter_filter_row.grid(row=1, column=1, columnspan=6, sticky="w", pady=(5, 0))
        self.starter_filters = {}
        for level in (*LEVELS, "Personal"):
            variable = tk.BooleanVar(value=True)
            self.starter_filters[level] = variable
            ttk.Checkbutton(starter_filter_row, text=level, variable=variable,
                            command=self._refresh_library).pack(side="left", padx=(0, 9))

        actions = ttk.Frame(self.library_tab, style="Panel.TFrame", padding=(13, 9))
        actions.pack(fill="x", pady=(0, 10))
        primary_actions = ttk.Frame(actions, style="Panel.TFrame")
        primary_actions.pack(fill="x")
        self.evaluate_button = ttk.Button(primary_actions, text="Evaluate pending words",
                                           style="Accent.TButton", command=self._evaluate)
        self.evaluate_button.pack(side="left", padx=(0, 8))
        self.cancel_button = ttk.Button(primary_actions, text="Cancel evaluation",
                                         command=self._cancel_evaluation, state="disabled")
        self.cancel_button.pack(side="left")
        self.retry_reviewed_button = ttk.Button(primary_actions, text="Retry reviewed with Codex",
                                                 command=lambda: self._evaluate(retry_reviewed=True),
                                                 state="disabled")
        self.retry_reviewed_button.pack(side="left", padx=(8, 0))
        self.eval_progress = tk.StringVar(value="")
        ttk.Label(primary_actions, textvariable=self.eval_progress,
                  style="PanelMuted.TLabel").pack(side="right")
        self.review_only = tk.BooleanVar(value=False)
        self.review_filter_label = tk.StringVar(value="Show needs review")
        ttk.Checkbutton(actions, textvariable=self.review_filter_label, variable=self.review_only,
                        command=self._toggle_review_filter).pack(anchor="w", pady=(8, 0))
        utility_actions = ttk.Frame(actions, style="Panel.TFrame")
        utility_actions.pack(fill="x", pady=(8, 0))
        for label, command in (("Export prompt", self._export_unknown),
                               ("Copy prompt", self._copy_prompt),
                               ("Import AI JSON", self._import_ai),
                               ("Add A1-B2 starter collection", self._import_starter),
                               ("Export backup", self._export_backup),
                               ("Import backup", self._import_backup)):
            ttk.Button(utility_actions, text=label, command=command).pack(
                side="left", padx=(0, 7))

        pane = ttk.Panedwindow(self.library_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, style="Panel.TFrame", padding=13)
        pane.add(left, weight=3)
        left_heading = ttk.Frame(left, style="Panel.TFrame")
        left_heading.pack(fill="x", pady=(0, 10))
        ttk.Label(left_heading, text="Your words", style="PanelTitle.TLabel").pack(side="left")
        self.list_count = tk.StringVar(value="0 entries")
        ttk.Label(left_heading, textvariable=self.list_count,
                  style="PanelMuted.TLabel").pack(side="right")
        tree_frame = ttk.Frame(left, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("word", "level", "difficulty", "content", "reviews"),
                                 show="headings", selectmode="browse")
        self._tree_rows = {}
        self._tree_order = []
        for column, title, width in (("word", "Word / expression", 220),
                                     ("level", "CEFR", 62),
                                     ("difficulty", "Difficulty", 100),
                                     ("content", "Content", 90),
                                     ("reviews", "Study", 145)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=55,
                             stretch=(column == "word"))
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._load_detail())
        self.list_empty = tk.Label(tree_frame, text="No words here yet\n\nType an Italian word above and press Enter.",
                                   bg=SURFACE, fg=MUTED, font=(FONT, 11), justify="center")

        detail_frame = ttk.Frame(pane, style="Panel.TFrame", padding=13)
        pane.add(detail_frame, weight=2)
        ttk.Label(detail_frame, text="Entry details", style="PanelTitle.TLabel").pack(
            anchor="w", pady=(0, 10))
        self.detail_empty = ttk.Frame(detail_frame, style="Panel.TFrame")
        ttk.Label(self.detail_empty, text="Select a word", style="PanelTitle.TLabel").pack(
            anchor="center", pady=(55, 8))
        ttk.Label(self.detail_empty, text="Its context, notes, difficulty, and learning content will appear here.",
                  style="PanelMuted.TLabel", wraplength=290, justify="center").pack(anchor="center")
        self.detail_editor = ttk.Frame(detail_frame, style="Panel.TFrame")
        self.detail_editor.pack(fill="both", expand=True)
        canvas = tk.Canvas(self.detail_editor, bg=SURFACE, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        detail_scroll = ttk.Scrollbar(self.detail_editor, orient="vertical", command=canvas.yview)
        detail_scroll.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=detail_scroll.set)
        details = ttk.Frame(canvas, style="Panel.TFrame", padding=(0, 0, 9, 9))
        detail_window = canvas.create_window((0, 0), window=details, anchor="nw")
        details.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(detail_window, width=event.width))
        ttk.Label(details, text="Edit details and save your changes.",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 8))
        self.detail_word = tk.StringVar()
        self.detail_difficulty = tk.StringVar(value="unknown")
        self.detail_fields = {}
        self._entry_field(details, "Word or expression", self.detail_word)
        self._text_field(details, "Context sentence", "context", 3)
        self._text_field(details, "Personal notes", "notes", 3)
        ttk.Label(details, text="DIFFICULTY", style="Field.TLabel").pack(
            anchor="w", pady=(11, 5))
        difficulty_box = ttk.Combobox(details, textvariable=self.detail_difficulty,
                                      values=DIFFICULTIES, state="readonly")
        difficulty_box.pack(fill="x")
        self._text_field(details, "Definition in Italian", "definition_it", 3)
        self._text_field(details, "English gloss", "gloss_en", 2)
        self._text_field(details, "Italian example", "example_it", 3)
        self._text_field(details, "Difficulty reason", "difficulty_reason", 2)
        self.review_label = tk.StringVar(value="")
        ttk.Label(details, textvariable=self.review_label, wraplength=300,
                  style="Review.TLabel").pack(fill="x", pady=(8, 0))
        self.meta_label = tk.StringVar(value="")
        ttk.Label(details, textvariable=self.meta_label, style="PanelMuted.TLabel",
                  wraplength=300).pack(fill="x", pady=(6, 0))
        detail_actions = ttk.Frame(details, style="Panel.TFrame")
        detail_actions.pack(fill="x", pady=(14, 0))
        ttk.Button(detail_actions, text="Save changes", style="Accent.TButton",
                   command=self._save_detail).pack(side="left")
        ttk.Button(detail_actions, text="Delete entry", style="Danger.TButton",
                   command=self._delete).pack(side="left", padx=(8, 0))
        ttk.Label(details, text="Changing the word or context resets its classification. "
                  "Your notes and study history stay saved.", style="PanelMuted.TLabel",
                  wraplength=300).pack(fill="x", pady=(13, 0))

    def _entry_field(self, parent, label, variable):
        ttk.Label(parent, text=label.upper(), style="Field.TLabel").pack(
            anchor="w", pady=(11, 5))
        ttk.Entry(parent, textvariable=variable).pack(fill="x")

    def _text_field(self, parent, label, key, height):
        ttk.Label(parent, text=label.upper(), style="Field.TLabel").pack(
            anchor="w", pady=(11, 5))
        widget = tk.Text(parent, height=height, wrap="word", font=(FONT, 10), undo=True,
                         bg=FIELD, fg=TEXT, insertbackground=TEXT, selectbackground=SELECTED,
                         selectforeground=TEXT, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, padx=10, pady=8)
        widget.pack(fill="x")
        self.detail_fields[key] = widget

    def _build_study(self):
        ttk.Label(self.study_tab, text="Study your vocabulary", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.study_tab, text="Choose a topic, word level, and difficulty mix, then study.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(1, 13))
        options = ttk.Frame(self.study_tab, style="Panel.TFrame", padding=16)
        options.pack(fill="x", pady=(0, 12))
        option_row = ttk.Frame(options, style="Panel.TFrame")
        option_row.pack(fill="x")
        ttk.Label(option_row, text="INCLUDE", style="Field.TLabel").pack(side="left", padx=(0, 9))
        self.study_filters = {}
        for difficulty in DIFFICULTIES[1:]:
            var = tk.BooleanVar(value=True)
            self.study_filters[difficulty] = var
            ttk.Checkbutton(option_row, text=difficulty.title(), variable=var,
                            command=self._refresh_study_count).pack(side="left", padx=7)
        ttk.Label(option_row, text="SESSION SIZE", style="Field.TLabel").pack(side="left", padx=(24, 8))
        self.session_size = tk.StringVar(value="20")
        session_size_menu = ttk.Combobox(option_row, textvariable=self.session_size,
                                         values=("10", "20", "All", "Custom"),
                                         width=9, state="readonly")
        session_size_menu.pack(side="left")
        session_size_menu.bind("<<ComboboxSelected>>", self._on_session_size_selected)
        self.custom_session_size = tk.StringVar()
        self.custom_size_entry = ttk.Entry(option_row, textvariable=self.custom_session_size,
                                           width=8)
        ttk.Button(option_row, text="Start session", style="Accent.TButton",
                   command=self._start_study).pack(side="right")
        topic_row = ttk.Frame(options, style="Panel.TFrame")
        topic_row.pack(fill="x", pady=(10, 0))
        ttk.Label(topic_row, text="TOPIC", style="Field.TLabel").pack(side="left", padx=(0, 8))
        self.study_topic = tk.StringVar(value=ALL_TOPICS)
        topic_menu = ttk.Combobox(topic_row, textvariable=self.study_topic,
                                  values=(ALL_TOPICS, *TOPICS), state="readonly", width=28)
        topic_menu.pack(side="left")
        topic_menu.bind("<<ComboboxSelected>>", lambda _event: self._refresh_study_count())
        ttk.Label(topic_row, text="WORD LEVEL", style="Field.TLabel").pack(
            side="left", padx=(22, 8))
        self.study_level = tk.StringVar(value=ALL_STUDY_LEVELS)
        level_menu = ttk.Combobox(topic_row, textvariable=self.study_level,
                                  values=STUDY_LEVELS, state="readonly", width=13)
        level_menu.pack(side="left")
        level_menu.bind("<<ComboboxSelected>>", lambda _event: self._refresh_study_count())
        self.study_count = tk.StringVar()
        ttk.Label(options, textvariable=self.study_count, style="PanelMuted.TLabel",
                  wraplength=1000).pack(
            anchor="w", pady=(10, 0))
        self.study_evaluate_button = ttk.Button(options, text="Evaluate pending words",
                                                 command=self._evaluate)
        self.study_evaluate_button.pack(anchor="w", pady=(9, 0))
        ttk.Label(options, text="Sessions favor words you have trouble recalling. "
                  "New and well-remembered words remain eligible.",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(4, 0))

        card_frame = ttk.Frame(self.study_tab, style="Panel.TFrame", padding=22)
        card_frame.pack(fill="both", expand=True)
        card_heading = ttk.Frame(card_frame, style="Panel.TFrame")
        card_heading.pack(fill="x")
        ttk.Label(card_heading, text="FLASHCARD", style="Eyebrow.TLabel").pack(side="left")
        self.study_progress = tk.StringVar(value="Choose difficulties and start a session.")
        ttk.Label(card_heading, textvariable=self.study_progress,
                  style="PanelMuted.TLabel").pack(side="right")
        self.card_word = tk.StringVar(value="Ready to study")
        ttk.Label(card_frame, textvariable=self.card_word, style="Card.TLabel",
                  wraplength=760, justify="center").pack(anchor="center", pady=(27, 25))
        answer_box = ttk.Frame(card_frame, style="Raised.TFrame", padding=(18, 17))
        answer_box.pack(fill="x", pady=(0, 18))
        self.card_answer = tk.StringVar(value="Reveal to see the Italian example sentence.")
        ttk.Label(answer_box, textvariable=self.card_answer, style="Answer.TLabel",
                  wraplength=760, justify="left").pack(anchor="w", fill="x")
        buttons = ttk.Frame(card_frame, style="Panel.TFrame")
        buttons.pack(anchor="center")
        self.reveal_button = ttk.Button(buttons, text="Reveal example  (Space)", style="Accent.TButton",
                                        command=self._reveal, state="disabled")
        self.reveal_button.pack(side="left", padx=5)
        self.remembered_button = ttk.Button(buttons, text="Remembered  (R)", style="Success.TButton",
                                             command=lambda: self._answer(True), state="disabled")
        self.remembered_button.pack(side="left", padx=5)
        self.again_button = ttk.Button(buttons, text="Again  (A)",
                                       command=lambda: self._answer(False), state="disabled")
        self.again_button.pack(side="left", padx=5)
        self.another_button = ttk.Button(card_frame, text="Another round with Again words",
                                          command=self._another_round, state="disabled")
        self.another_button.pack(anchor="center", pady=(20, 0))
        ttk.Label(self.study_tab, text="Keyboard shortcuts work while this tab is open and no text field has focus. "
                  "Remembered and Again do not change difficulty.",
                  style="Muted.TLabel").pack(anchor="w", pady=(9, 0))

    def _build_settings(self):
        ttk.Label(self.settings_tab, text="Evaluation settings", style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.settings_tab, text="Tune vocabulary difficulty for your learner profile.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(1, 13))
        settings_panel = ttk.Frame(self.settings_tab, style="Panel.TFrame", padding=20)
        settings_panel.pack(fill="x")
        ttk.Label(settings_panel, text="CODEX CLASSIFICATION", style="Eyebrow.TLabel").pack(
            anchor="w", pady=(0, 6))
        ttk.Label(settings_panel, text="Difficulty is an estimate relative to the selected learner level.",
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(0, 13))
        form = ttk.Frame(settings_panel, style="Panel.TFrame")
        form.pack(anchor="w", fill="x")
        self.model_var = tk.StringVar(value=self.db.get_setting("model", "gpt-6-sol"))
        self.effort_var = tk.StringVar(value=self.db.get_setting("effort", "high"))
        self.level_var = tk.StringVar(value=self.db.get_setting("level", "B1"))
        self.timeout_var = tk.StringVar(value=self.db.get_setting("timeout", "900"))
        for row, (label, variable, values) in enumerate((
            ("Model", self.model_var, None),
            ("Reasoning effort", self.effort_var, ("low", "medium", "high", "xhigh", "max")),
            ("Learner level", self.level_var, ("A1", "A2", "B1", "B2", "C1", "C2")),
            ("Timeout per batch (seconds)", self.timeout_var, None),
        )):
            ttk.Label(form, text=label.upper(), style="Field.TLabel").grid(
                row=row, column=0, sticky="w", pady=8, padx=(0, 25))
            if values:
                widget = ttk.Combobox(form, textvariable=variable, values=values,
                                      state="readonly", width=22)
            else:
                widget = ttk.Entry(form, textvariable=variable, width=25)
            widget.grid(row=row, column=1, sticky="ew", pady=8)
            if label == "Learner level":
                widget.bind("<<ComboboxSelected>>", lambda _event: self._save_settings())
        ttk.Button(settings_panel, text="Save settings", style="Accent.TButton",
                   command=self._save_settings).pack(anchor="w", pady=(17, 0))
        self.recheck_status = tk.StringVar()
        ttk.Label(settings_panel, textvariable=self.recheck_status,
                  style="PanelMuted.TLabel").pack(anchor="w", pady=(12, 0))
        self._refresh_recheck_status()
        ttk.Label(self.settings_tab, text="Automatic evaluation requires the official Codex CLI "
                  "and 'codex login' with ChatGPT. If the selected model is unavailable, choose another "
                  "model here. It uses your ChatGPT account's Codex allowance and may be subject to limits. "
                  "Offline study and word management still work.",
                  wraplength=760, style="Muted.TLabel").pack(anchor="w", pady=(16, 0))

    def _message(self, text):
        self.status.set(text)

    def _refresh_recheck_status(self):
        count = self.db.pending_recheck_count()
        self.recheck_status.set(
            f"{count} previously evaluated entr{'y' if count == 1 else 'ies'} awaiting re-evaluation."
            if count else "No previously evaluated entries are waiting for re-evaluation."
        )

    def _selected_id(self):
        selection = self.tree.selection()
        return int(selection[0]) if selection else None

    def _add(self):
        discard_details = self._detail_has_unsaved()
        if discard_details and not messagebox.askyesno(
            "Unsaved changes", "Discard unsaved changes in the details pane and add this word?", parent=self
        ):
            return
        try:
            entry_id, created = self.db.add(self.entry.get())
        except (ValueError, sqlite3.Error, OSError) as exc:
            self._message(str(exc))
            self.entry.focus_set()
            return
        if discard_details:
            self._clear_detail()
        if not created:
            self.search.set("")
            for variable in self.library_filters.values():
                variable.set(True)
        else:
            self.search.set("")
            self.library_filters["unknown"].set(True)
        self.entry.delete(0, "end")
        self._refresh_library(select_id=entry_id)
        self.tabs.select(self.library_tab)
        self.entry.focus_set()
        self._message("Saved under Unknown." if created else "Already saved. Selected the existing entry.")

    def _render_library_rows(self, display_rows):
        previous = self._tree_rows
        old_order = self._tree_order
        new_order = [entry_id for entry_id, _values in display_rows]
        new_ids = set(new_order)
        removed = [entry_id for entry_id in old_order if entry_id not in new_ids]
        if removed:
            self.tree.delete(*removed)
        reordered = ([entry_id for entry_id in old_order if entry_id in new_ids] !=
                     [entry_id for entry_id in new_order if entry_id in previous])
        for index, (entry_id, values) in enumerate(display_rows):
            if entry_id in previous:
                if previous[entry_id] != values:
                    self.tree.item(entry_id, values=values)
                if reordered:
                    self.tree.move(entry_id, "", index)
            else:
                self.tree.insert("", index, iid=entry_id, values=values)
        self._tree_rows = dict(display_rows)
        self._tree_order = new_order

    def _on_tab_changed(self, _event=None):
        if self.tabs.select() == str(self.study_tab):
            self._refresh_study_count()

    def _refresh_library(self, select_id=None):
        if not hasattr(self, "tree"):
            return
        if select_id is None:
            select_id = self._selected_id() or self.detail_entry_id
        preserve_editor = self._detail_has_unsaved()
        filters = [key for key, var in self.library_filters.items() if var.get()]
        levels = [key for key, var in self.starter_filters.items() if var.get()]
        rows = self.db.list_entries(self.search.get(), filters, levels,
                                    review_only=self.review_only.get(), library_view=True)
        recent = self.db.recent_first_round_outcomes([row["id"] for row in rows])
        pending_ids = self.db.pending_recheck_ids()
        self._refreshing_tree = True
        try:
            display_rows = []
            for row in rows:
                study = f"{row['remembered_count']}/{row['study_attempts']} - " \
                    f"{recall_label(row, recent.get(row['id'], ()))}"
                content = ("Needs review" if row["difficulty"] == "unknown" and row["review_note"] else
                           "Unknown" if row["difficulty"] == "unknown" else
                           "Ready" if row["content_ready"] else "Incomplete")
                if row["id"] in pending_ids:
                    content += " - recheck due"
                display_rows.append((str(row["id"]),
                                     (row["original_text"], row["starter_level"] or "-",
                                      row["difficulty"].title(), content, study)))
            self._render_library_rows(display_rows)
            if (select_id is not None and self.tree.exists(str(select_id)) and
                    self._selected_id() != select_id):
                self.tree.selection_set(str(select_id))
                self.tree.see(str(select_id))
        finally:
            self._refreshing_tree = False
        if not preserve_editor:
            if select_id is not None and self.tree.exists(str(select_id)):
                self._load_detail()
            else:
                self._clear_detail()
        counts = self.db.counts()
        for difficulty in DIFFICULTIES:
            self.count_vars[difficulty].set(str(counts[difficulty]))
        reviewed = self.db.review_count()
        self.review_filter_label.set(f"Show needs review ({reviewed:,})")
        self.retry_reviewed_button.configure(
            state="normal" if reviewed and not self._evaluation_in_progress else "disabled")
        self.list_count.set(f"{len(rows)} shown")
        if rows:
            self.list_empty.place_forget()
        else:
            message = ("No words here yet\n\nType an Italian word above and press Enter."
                       if not sum(counts.values()) else
                       "No words match this view.\n\nTry another search or difficulty filter.")
            self.list_empty.configure(text=message)
            self.list_empty.place(relx=0.5, rely=0.5, anchor="center")
        if self.tabs.select() == str(self.study_tab):
            self._refresh_study_count()
        if hasattr(self, "recheck_status"):
            self._refresh_recheck_status()

    def _toggle_review_filter(self):
        if self.review_only.get():
            self.library_filters["unknown"].set(True)
        self._refresh_library()

    def _clear_detail(self):
        self.detail_entry_id = None
        self.detail_loaded_revision = None
        self.detail_loaded_state = None
        self.detail_word.set("")
        self.detail_difficulty.set("unknown")
        for widget in self.detail_fields.values():
            widget.delete("1.0", "end")
        self.review_label.set("")
        self.meta_label.set("")
        self.detail_editor.pack_forget()
        if not self.detail_empty.winfo_manager():
            self.detail_empty.pack(fill="both", expand=True)

    def _load_detail(self):
        if self._refreshing_tree:
            return
        entry_id = self._selected_id()
        if self._detail_has_unsaved() and (entry_id is None or entry_id == self.detail_entry_id):
            return
        if entry_id != self.detail_entry_id and self._detail_has_unsaved():
            if not messagebox.askyesno("Unsaved changes", "Discard unsaved changes in the details pane?",
                                       parent=self):
                if self.detail_entry_id is not None and self.tree.exists(str(self.detail_entry_id)):
                    self.tree.selection_set(str(self.detail_entry_id))
                else:
                    self.tree.selection_remove(*self.tree.selection())
                return
        row = self.db.get(entry_id) if entry_id is not None else None
        if row is None:
            self._clear_detail()
            return
        self.detail_empty.pack_forget()
        if not self.detail_editor.winfo_manager():
            self.detail_editor.pack(fill="both", expand=True)
        self.detail_entry_id = entry_id
        self.detail_loaded_revision = row["revision"]
        self.detail_word.set(row["original_text"])
        self.detail_difficulty.set(row["difficulty"])
        for key, widget in self.detail_fields.items():
            widget.delete("1.0", "end")
            widget.insert("1.0", row[key] or "")
        self.review_label.set("Needs review: " + row["review_note"] if row["review_note"] else "")
        recent = self.db.recent_first_round_outcomes([entry_id]).get(entry_id, ())
        attempts = row["study_attempts"]
        success = (f"Study success: {row['remembered_count']}/{attempts} "
                   f"({row['remembered_count'] / attempts:.0%})" if attempts else "No study attempts yet")
        estimate = f"Estimated recall: {recall_estimate(row, recent):.0%} ({recall_label(row, recent)})"
        self.meta_label.set(f"Topics: {', '.join(topics_for_card(row))}  |  "
                            f"Added {row['created_at']}  |  Revision {row['revision']}  |  "
                            f"{success}  |  {estimate}" +
                            (f"  |  Starter {row['starter_level']} {row['starter_kind']} "
                             "(estimated source level)" if row["starter_level"] else "") +
                            (f"  |  Classified by {row['classification_model']}" if row["classification_model"] else ""))
        self.detail_loaded_state = {"original_text": row["original_text"],
                                    "difficulty": row["difficulty"],
                                    **{key: row[key] or "" for key in self.detail_fields}}

    def _detail_has_unsaved(self):
        if self.detail_entry_id is None or not hasattr(self, "detail_fields"):
            return False
        loaded = self.detail_loaded_state
        if loaded is None:
            return False
        if self.detail_word.get() != loaded["original_text"] or self.detail_difficulty.get() != loaded["difficulty"]:
            return True
        return any(widget.get("1.0", "end-1c") != loaded[key]
                   for key, widget in self.detail_fields.items())

    def _save_detail(self):
        entry_id = self.detail_entry_id
        if entry_id is None:
            self._message("Select an entry first.")
            return
        old = self.db.get(entry_id)
        values = {key: widget.get("1.0", "end-1c") for key, widget in self.detail_fields.items()}
        try:
            updated = self.db.update(entry_id, original_text=self.detail_word.get(),
                                     difficulty=self.detail_difficulty.get(),
                                     expected_revision=self.detail_loaded_revision, **values)
        except (ValueError, sqlite3.Error, OSError) as exc:
            messagebox.showerror("Could not save", str(exc), parent=self)
            return
        self.detail_entry_id = None
        self._refresh_library(select_id=entry_id)
        if updated["difficulty"] == "unknown" and (
            updated["original_text"] != old["original_text"] or updated["context"] != old["context"]
        ):
            self._message("Saved. Word or context changed, so difficulty returned to Unknown.")
        else:
            self._message("Changes saved.")

    def _delete(self):
        entry_id = self.detail_entry_id
        row = self.db.get(entry_id) if entry_id is not None else None
        if row is None:
            self._message("Select an entry first.")
            return
        if not messagebox.askyesno("Delete entry", f"Delete '{row['original_text']}' and its study history?",
                                   parent=self):
            return
        try:
            self.db.delete(entry_id)
        except sqlite3.Error as exc:
            messagebox.showerror("Could not delete", str(exc), parent=self)
            return
        self._clear_detail()
        self._refresh_library()
        self._message("Entry deleted.")

    def _save_settings(self, auto_recheck=True):
        model = self.model_var.get().strip()
        effort = self.effort_var.get()
        level = self.level_var.get()
        try:
            timeout = int(self.timeout_var.get())
        except ValueError:
            timeout = 0
        if not model or len(model) > 100 or any(ch.isspace() for ch in model):
            messagebox.showerror("Settings", "Enter a valid model name without spaces.", parent=self)
            return False
        if effort not in ("low", "medium", "high", "xhigh", "max") or level not in (
            "A1", "A2", "B1", "B2", "C1", "C2"
        ) or not 30 <= timeout <= 3600:
            messagebox.showerror("Settings", "Choose valid effort and level, and a timeout of 30 to 3600 seconds.",
                                 parent=self)
            return False
        try:
            changed_level, pending = self.db.save_evaluation_settings(
                {"model": model, "effort": effort, "level": level, "timeout": str(timeout)})
        except sqlite3.Error as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self)
            return False
        self._refresh_recheck_status()
        if changed_level:
            self._refresh_library()
            if self._evaluation_in_progress:
                self._queued_recheck = True
                self._queued_include_unknown = True
                self.cancel_event.set()
                self._message(f"Learner level changed to {level}. Restarting evaluation at the new level.")
            elif pending and auto_recheck:
                self._queued_recheck = True
                self._queued_include_unknown = False
                self.after(0, self._start_queued_recheck)
                self._message(f"Learner level changed to {level}. Re-evaluating {pending} saved AI results.")
            elif pending:
                self._message(f"Learner level changed to {level}. {pending} AI results need re-evaluation.")
            else:
                self._message(f"Learner level changed to {level}.")
        else:
            self._message("Settings saved.")
        return True

    def _requests_for_pending(self, *, recheck_only=False, snapshot=None):
        if snapshot is None:
            snapshot = self.db.pending_snapshot(recheck_only=recheck_only)
        return [self.db.create_request(snapshot[i:i + EVALUATION_BATCH_SIZE],
                                       self.level_var.get(), self.model_var.get())
                for i in range(0, len(snapshot), EVALUATION_BATCH_SIZE)]

    def _evaluate(self, *, recheck_only=False, retry_reviewed=False):
        if self._evaluation_in_progress or self.worker and self.worker.is_alive():
            self._message("An evaluation is already running.")
            return
        if not self._save_settings(auto_recheck=False):
            return
        try:
            snapshot = (self.db.review_snapshot() if retry_reviewed else
                        self.db.pending_snapshot(recheck_only=recheck_only))
            if retry_reviewed and snapshot:
                names = [entry["original_text"] for entry in snapshot[:20]]
                preview = "\n".join(", ".join(names[i:i + 8]) for i in range(0, len(names), 8))
                if len(snapshot) > len(names):
                    preview += f"\n... and {len(snapshot) - len(names):,} more"
                limit = simpledialog.askinteger(
                    "Retry reviewed entries",
                    f"{len(snapshot):,} entries already received a needs-review result. "
                    "These entries will be retried in database order:\n"
                    f"{preview}\n\n"
                    "Check their review notes first. Single letters, incomplete words, and "
                    "unclear spellings need edits; retrying them unchanged uses your Codex "
                    "allowance and will likely give the same result. How many should this run retry?",
                    parent=self, initialvalue=min(EVALUATION_BATCH_SIZE, len(snapshot)),
                    minvalue=1, maxvalue=len(snapshot),
                )
                if limit is None:
                    return
                snapshot = snapshot[:limit]
            elif len(snapshot) > 100 and not recheck_only:
                limit = simpledialog.askinteger(
                    "Choose evaluation size",
                    f"{len(snapshot):,} entries are pending. How many should this run evaluate? "
                    f"Up to {MAX_CONCURRENT_CODEX_JOBS} Codex jobs run at once, "
                    f"with up to {EVALUATION_BATCH_SIZE} entries each. "
                    "Every job uses your Codex allowance.",
                    parent=self, initialvalue=50, minvalue=1, maxvalue=len(snapshot),
                )
                if limit is None:
                    return
                snapshot = snapshot[:limit]
            requests = self._requests_for_pending(snapshot=snapshot)
        except (sqlite3.Error, OSError, ValueError) as exc:
            messagebox.showerror("Could not start evaluation", str(exc), parent=self)
            return
        if not requests:
            reviewed = self.db.review_count()
            self._message(
                f"No new entries to evaluate. {reviewed:,} need review in Library. "
                "Add context or explicitly retry them with Codex."
                if reviewed else "There are no pending entries to evaluate."
            )
            return
        self._queued_recheck = False
        self._queued_include_unknown = False
        self._evaluation_in_progress = True
        self.cancel_event = threading.Event()
        self.evaluate_button.configure(state="disabled")
        self.study_evaluate_button.configure(state="disabled")
        self.retry_reviewed_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.eval_progress.set(f"0/{len(requests)} batches, 0/{sum(len(r['entries']) for r in requests)} entries")
        self._eval_done = 0
        self._eval_applied = 0
        self._eval_review = 0
        self._eval_stale = 0
        self._eval_total = sum(len(r["entries"]) for r in requests)
        self._eval_batches = len(requests)
        self.worker = threading.Thread(target=self._evaluation_worker,
                                       args=(requests, self.model_var.get(), self.effort_var.get(),
                                             int(self.timeout_var.get()), self.cancel_event), daemon=True)
        try:
            self.worker.start()
        except RuntimeError as exc:
            self._finish_evaluation()
            messagebox.showerror("Could not start evaluation", str(exc), parent=self)
            return
        self._message("Evaluation started. You can keep using the library.")

    def _evaluation_worker(self, requests, model, effort, timeout, cancel_event):
        failure = None
        try:
            with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_CODEX_JOBS,
                                                    len(requests))) as executor:
                futures = {
                    executor.submit(_run_evaluation_batch, index, request, model, effort,
                                    timeout, cancel_event, self.events): (index, request)
                    for index, request in enumerate(requests, 1)
                }
                for future in as_completed(futures):
                    if cancel_event.is_set():
                        break
                    index, request = futures[future]
                    try:
                        response = future.result()
                    except ClassificationCancelled:
                        cancel_event.set()
                        break
                    except Exception as exc:
                        failure = (index, str(exc))
                        cancel_event.set()
                        break
                    acknowledgment = threading.Event()
                    self.events.put(("batch", index, request, response, acknowledgment))
                    while not acknowledgment.wait(0.1):
                        if cancel_event.is_set():
                            break
                    if cancel_event.is_set():
                        break
        except Exception as exc:
            failure = (0, str(exc))
            cancel_event.set()
        if failure:
            self.events.put(("error", *failure))
        elif cancel_event.is_set():
            self.events.put(("cancelled",))
        else:
            self.events.put(("done",))

    def _poll_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "running":
                self._message(f"Evaluating batch {event[1]} of {self._eval_batches}...")
            elif kind == "batch":
                _, index, request, response, acknowledgment = event
                try:
                    if not self.cancel_event.is_set():
                        counts = self.db.apply_results(response, request)
                        self._eval_done += 1
                        self._eval_applied += counts["applied"]
                        self._eval_review += counts["needs_review"]
                        self._eval_stale += counts["skipped_stale"]
                        processed = self._eval_applied + self._eval_review + self._eval_stale
                        self.eval_progress.set(f"{self._eval_done}/{self._eval_batches} batches, "
                                               f"{processed}/{self._eval_total} entries processed")
                        self._refresh_library()
                        self._message(f"Batch {index} saved: {counts['applied']} classified, "
                                      f"{counts['needs_review']} need review, {counts['skipped_stale']} stale.")
                except (ValueError, OSError, sqlite3.Error) as exc:
                    self.cancel_event.set()
                    self._message(f"Batch {index} was rejected: {exc}")
                    messagebox.showerror("Evaluation result rejected", str(exc), parent=self)
                finally:
                    acknowledgment.set()
            elif kind == "error":
                self._finish_evaluation()
                subject = f"Batch {event[1]}" if event[1] else "Evaluation"
                self._message(f"{subject} failed: {event[2]}")
                messagebox.showerror("Evaluation failed", f"{subject} failed. "
                                     f"Saved batches remain saved.\n\n{event[2]}", parent=self)
                self._queued_recheck = False
                self._queued_include_unknown = False
            elif kind == "cancelled":
                self._finish_evaluation()
                self._message("Evaluation cancelled. Saved batches remain; pending work can be retried.")
                self._schedule_queued_recheck()
            elif kind == "done":
                self._finish_evaluation()
                self._message(f"Evaluation finished: {self._eval_applied} classified, "
                              f"{self._eval_review} need review, {self._eval_stale} skipped as stale.")
                self._schedule_queued_recheck()
        if not self.closing:
            self.after(100, self._poll_events)

    def _finish_evaluation(self):
        self._evaluation_in_progress = False
        self.evaluate_button.configure(state="normal")
        self.study_evaluate_button.configure(state="normal")
        self.retry_reviewed_button.configure(
            state="normal" if self.db.review_count() else "disabled")
        self.cancel_button.configure(state="disabled")

    def _schedule_queued_recheck(self):
        if self._queued_recheck and not self.closing:
            self.after(100, self._start_queued_recheck)

    def _start_queued_recheck(self):
        if self.closing or not self._queued_recheck:
            return
        if self._evaluation_in_progress or self.worker and self.worker.is_alive():
            self.after(100, self._start_queued_recheck)
            return
        include_unknown = self._queued_include_unknown
        self._queued_recheck = False
        self._queued_include_unknown = False
        self._evaluate(recheck_only=not include_unknown)

    def _cancel_evaluation(self):
        if self.cancel_event:
            self._queued_recheck = False
            self._queued_include_unknown = False
            self.cancel_event.set()
            self._message("Cancelling evaluation...")

    def _export_unknown(self):
        if not self._save_settings(auto_recheck=False):
            return
        snapshot = self.db.pending_snapshot()
        if not snapshot:
            self._message("There are no pending entries to export.")
            return
        directory = filedialog.askdirectory(parent=self, title="Choose a folder for evaluation files")
        if not directory:
            return
        try:
            requests = self._requests_for_pending()
        except (sqlite3.Error, OSError, ValueError) as exc:
            messagebox.showerror("Could not prepare export", str(exc), parent=self)
            return
        if not requests:
            return
        target = Path(directory) / f"italian-vocabulary-{requests[0]['request_id']}"
        try:
            target.mkdir(mode=0o700)
            for index, request in enumerate(requests, 1):
                (target / f"prompt-{index:02d}.txt").write_text(build_prompt(request), encoding="utf-8")
                (target / f"request-{index:02d}.json").write_text(
                    json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
            shutil.copyfile(SCHEMA_PATH, target / "result_schema.json")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)
            return
        self._message(f"Exported {len(snapshot)} pending entries in {len(requests)} tracked batches to {target}.")

    def _copy_prompt(self):
        if not self._save_settings(auto_recheck=False):
            return
        snapshot = self.db.pending_snapshot()[:EVALUATION_BATCH_SIZE]
        if not snapshot:
            self._message("There are no pending entries to copy.")
            return
        try:
            request = self.db.create_request(snapshot, self.level_var.get(), self.model_var.get())
        except (sqlite3.Error, ValueError) as exc:
            messagebox.showerror("Could not copy prompt", str(exc), parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(build_prompt(request))
        self._message(f"Copied prompt for {len(snapshot)} entries. Request ID: {request['request_id']}. "
                      "Import the resulting JSON with Import AI JSON.")

    def _import_ai(self):
        path = filedialog.askopenfilename(parent=self, title="Import classifier JSON",
                                          filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            response = load_json_strict(Path(path).read_text(encoding="utf-8"))
            if not isinstance(response, dict) or not isinstance(response.get("request_id"), str):
                raise ValueError("JSON has no request ID.")
            request = self.db.get_request(response["request_id"])
            if request is None:
                raise ValueError("Request ID is not in this database. Import the matching backup first.")
            counts = self.db.apply_results(response, request)
        except (OSError, ValueError, sqlite3.Error) as exc:
            messagebox.showerror("AI JSON rejected", str(exc), parent=self)
            return
        self._refresh_library()
        self._message(f"Imported: {counts['applied']} classified, {counts['needs_review']} need review, "
                      f"{counts['skipped_stale']} stale.")

    def _export_backup(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export vocabulary backup",
                                            defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            count = self.db.export_backup(path)
        except (OSError, sqlite3.Error) as exc:
            messagebox.showerror("Backup export failed", str(exc), parent=self)
            return
        self._message(f"Exported {count} entries to {path}.")

    def _import_starter(self):
        try:
            collection = load_starter_catalog()
            counts = starter_counts(collection)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Starter collection unavailable", str(exc), parent=self)
            return
        summary = ", ".join(f"{level}: {counts[level]:,}" for level in LEVELS)
        if not messagebox.askyesno(
            "Add starter collection",
            f"Add up to {len(collection):,} words and expressions?\n\n{summary}\n\n"
            "Existing entries and study history stay unchanged. Duplicates are skipped. "
            "New entries start as Unknown. AI evaluation is separate and uses your Codex allowance. "
            "Source levels are approximate and may contain errors.",
            parent=self,
        ):
            return
        try:
            result = self.db.import_starter(collection)
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Starter import failed", str(exc), parent=self)
            return
        self._refresh_library()
        self._message(f"Starter collection: {result['added']:,} added, {result['skipped']:,} already saved.")

    def _import_backup(self):
        path = filedialog.askopenfilename(parent=self, title="Import vocabulary backup",
                                          filetypes=[("JSON", "*.json"), ("All files", "*")])
        if not path:
            return
        try:
            counts = self.db.import_backup(path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Backup import failed", str(exc), parent=self)
            return
        self.model_var.set(self.db.get_setting("model", "gpt-6-sol"))
        self.effort_var.set(self.db.get_setting("effort", "high"))
        self.level_var.set(self.db.get_setting("level", "B1"))
        self.timeout_var.set(self.db.get_setting("timeout", "900"))
        self._refresh_library()
        self._message(f"Backup import: {counts['added']} added, {counts['skipped']} skipped, "
                      f"{counts['conflicting']} conflicting entries kept unchanged.")

    def _chosen_study_difficulties(self):
        return [key for key, var in self.study_filters.items() if var.get()]

    def _on_session_size_selected(self, _event=None):
        if self.session_size.get() == "Custom":
            self.custom_size_entry.pack(side="left", padx=(8, 0))
            self.custom_size_entry.focus_set()
        else:
            self.custom_size_entry.pack_forget()

    def _refresh_study_count(self):
        if not hasattr(self, "study_count"):
            return
        chosen = self._chosen_study_difficulties()
        ready_count, incomplete = self.db.study_pool_counts(chosen, topic=self.study_topic.get(),
                                                            level=self.study_level.get())
        unknown = self.db.counts()["unknown"]
        reviewed = self.db.review_count()
        if not chosen:
            self.study_count.set("Select at least one difficulty to study.")
        elif not ready_count:
            if self.study_topic.get() != ALL_TOPICS or self.study_level.get() != ALL_STUDY_LEVELS:
                message = "No study-ready cards match this topic, word level, and difficulty selection."
                if incomplete:
                    message += f" {incomplete:,} matching entries need learning content."
                self.study_count.set(message)
            elif unknown:
                message = (f"No cards ready for these difficulties. {unknown:,} Unknown entries "
                           "are excluded.")
                if incomplete:
                    message += f" {incomplete:,} selected entries lack learning content."
                if unknown > reviewed:
                    message += " Evaluate pending words to prepare new entries."
                if reviewed:
                    message += f" {reviewed:,} need review; add context or learning content in Library."
                self.study_count.set(message)
            elif incomplete:
                self.study_count.set(
                    f"No cards ready. {incomplete:,} selected entries need a definition, "
                    "English gloss, or Italian example. Complete them in Library."
                )
            else:
                self.study_count.set("No cards match this topic, word level, and difficulty selection.")
        else:
            noun = "entry" if ready_count == 1 else "entries"
            verb = "matches" if ready_count == 1 else "match"
            message = f"{ready_count:,} study-ready {noun} {verb} your selection."
            if incomplete:
                message += f" {incomplete:,} selected entries need a definition, gloss, or example."
            if unknown:
                message += f" {unknown:,} Unknown entries are excluded; {reviewed:,} need review."
            self.study_count.set(message)

    def _start_study(self):
        chosen = self._chosen_study_difficulties()
        if not chosen:
            self._message("Select at least one study difficulty.")
            return
        ready, incomplete = self.db.study_pool(chosen, topic=self.study_topic.get(),
                                               level=self.study_level.get(), compact=True)
        if not ready:
            unknown = self.db.counts()["unknown"]
            reviewed = self.db.review_count()
            if self.study_topic.get() != ALL_TOPICS or self.study_level.get() != ALL_STUDY_LEVELS:
                self._message("No study-ready cards match this topic, word level, and difficulty selection.")
            elif unknown > reviewed:
                self._message("No cards ready. Evaluate new words or complete entries in Library.")
            elif reviewed:
                self._message("No cards ready. Review flagged entries and add context in Library.")
            elif incomplete:
                self._message("No cards ready. Complete learning content in Library.")
            else:
                self._message("No cards match this topic, word level, and difficulty selection.")
            return
        try:
            size = parse_session_size(self.session_size.get(), self.custom_session_size.get())
        except ValueError as exc:
            self._message(str(exc))
            self.custom_size_entry.focus_set()
            return
        recent = self.db.recent_first_round_outcomes([row["id"] for row in ready])
        self.session = StudySession(ready, size, recent_outcomes=recent)
        self.another_button.configure(state="disabled")
        self._show_card()
        self._message(f"Started a session with {len(self.session.cards)} cards. {incomplete} incomplete excluded.")

    def _show_card(self):
        card = self.session.current if self.session else None
        if card is None:
            self.card_word.set("Round complete")
            if self.session:
                self.study_progress.set(f"Round {self.session.round_number} complete. "
                                        f"{self.session.remembered} remembered, {self.session.again} again so far.")
                self.card_answer.set("You can review the Again words in another round." if self.session.again_cards
                                     else "All words in this round were remembered.")
                self.another_button.configure(state="normal" if self.session.again_cards else "disabled")
            self.reveal_button.configure(state="disabled")
            self.remembered_button.configure(state="disabled")
            self.again_button.configure(state="disabled")
            return
        self.study_progress.set(f"Round {self.session.round_number}  |  Card {self.session.index + 1} "
                                f"of {self.session.round_total}")
        self.card_word.set(card["original_text"])
        self.card_answer.set("Reveal to see the Italian example sentence.")
        self.reveal_button.configure(text="Reveal example  (Space)", state="normal")
        self.remembered_button.configure(state="disabled")
        self.again_button.configure(state="disabled")

    def _reveal(self):
        if not self.session:
            return
        stage = self.session.reveal()
        if not stage:
            return
        card = self.session.current
        if stage == 1:
            self.card_answer.set(f"Example: {card['example_it']}\n\n"
                                 "Reveal again for the English meaning and Italian definition.")
            self.reveal_button.configure(text="Reveal answer  (Space)")
        else:
            self.card_answer.set(f"Example: {card['example_it']}\n\nEnglish: {card['gloss_en']}\n\n"
                                 f"Italian: {card['definition_it']}")
            self.reveal_button.configure(state="disabled")
            self.remembered_button.configure(state="normal")
            self.again_button.configure(state="normal")

    def _answer(self, remembered):
        if not self.session or not self.session.revealed:
            return
        entry_id = self.session.current["id"]
        try:
            self.db.record_study(entry_id, remembered, session_id=self.session.session_id,
                                 round_number=self.session.round_number)
        except (sqlite3.Error, ValueError) as exc:
            messagebox.showerror("Could not save study result", str(exc), parent=self)
            return
        self.session.answer(remembered)
        self._show_card()
        self._refresh_study_result(entry_id)

    def _refresh_study_result(self, entry_id):
        entry_key = str(entry_id)
        values = self._tree_rows.get(entry_key)
        if values is None:
            return
        row = self.db.get(entry_id)
        recent = self.db.recent_first_round_outcomes([entry_id]).get(entry_id, ())
        study = f"{row['remembered_count']}/{row['study_attempts']} - {recall_label(row, recent)}"
        updated = (*values[:-1], study)
        self.tree.item(entry_key, values=updated)
        self._tree_rows[entry_key] = updated
        if self._selected_id() == entry_id and not self._detail_has_unsaved():
            self._load_detail()

    def _another_round(self):
        if self.session and self.session.next_round():
            self.another_button.configure(state="disabled")
            self._show_card()

    def _study_key(self, event):
        if self.tabs.select() != str(self.study_tab):
            return
        focused = self.focus_get()
        if focused is not None and focused.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox"):
            return
        if event.keysym == "space":
            self._reveal()
        elif event.keysym.lower() == "r":
            self._answer(True)
        elif event.keysym.lower() == "a":
            self._answer(False)

    def _switch_profile(self):
        if self._evaluation_in_progress:
            messagebox.showinfo("Evaluation in progress",
                                "Finish or cancel the evaluation before switching profiles.", parent=self)
            return
        selected = choose_profile(self.profile_store, parent=self)
        if selected and selected != self.profile["id"]:
            self.next_profile_id = selected
            self._close()

    def _close(self):
        self.closing = True
        if self.cancel_event:
            self.cancel_event.set()
        if self.worker and self.worker.is_alive():
            self._message("Stopping evaluation before closing...")
            self.after(100, self._finish_close)
        else:
            self.db.close()
            self.destroy()

    def _finish_close(self):
        if self.worker and self.worker.is_alive():
            self.after(100, self._finish_close)
        else:
            self.db.close()
            self.destroy()


def main():
    store = ProfileStore()
    try:
        store.migrate_legacy_if_needed()
        profile = store.active_profile()
    except (OSError, ValueError, sqlite3.Error) as exc:
        messagebox.showerror("Profiles could not open", str(exc))
        return
    profile_id = profile["id"] if profile else choose_profile(store)
    while profile_id:
        profile = next(item for item in store.list_profiles() if item["id"] == profile_id)
        app = VocabularyApp(store, profile)
        app.mainloop()
        profile_id = app.next_profile_id


if __name__ == "__main__":
    main()
