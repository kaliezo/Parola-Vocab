"""Native Qt interface for Italian Vocabulary."""

import sqlite3
import sys
import time
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QEvent
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QTableView, QTextEdit,
    QVBoxLayout, QWidget, QHeaderView,
)

from service import VocabularyService
from qt_theme import STYLE
from storage import ALL_STUDY_LEVELS, DIFFICULTIES, STUDY_LEVELS
from study import recall_estimate, recall_label
from topics import ALL_TOPICS, TOPICS, topics_for_card
from starter import LEVELS, load_starter_catalog, starter_counts


def label(text, role=None, wrap=False):
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback, role=None):
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    widget.clicked.connect(callback)
    return widget


def row(*widgets, stretch=False):
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget, 1 if stretch and widget is widgets[0] else 0)
    return layout


def section(parent, title):
    title_widget = label(title, "eyebrow")
    parent.addWidget(title_widget)
    return title_widget


def message(parent, title, text, *, question=False):
    if question:
        return QMessageBox.question(parent, title, text,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
    QMessageBox.critical(parent, title, str(text))
    return False


def sort_library_rows(rows, recent, column, first_click):
    """Sort filtered database rows without changing their saved values."""
    if column is None:
        return rows
    if column == 0:
        # The database already supplies the normal word order.
        return list(reversed(rows)) if first_click else rows
    if column == 1:
        levels = ("B2", "B1", "A2", "A1") if first_click else ("A1", "A2", "B1", "B2")
        rank = {level: index for index, level in enumerate(levels)}
        return sorted(rows, key=lambda item: rank.get(item["starter_level"], 4))
    if column == 2:
        difficulties = ("hard", "medium", "easy") if first_click else ("easy", "medium", "hard")
        rank = {difficulty: index for index, difficulty in enumerate(difficulties)}
        return sorted(rows, key=lambda item: rank.get(item["difficulty"], 3))
    if column == 3:
        def ready(item):
            return item["difficulty"] != "unknown" and bool(item["content_ready"])
        return sorted(rows, key=ready, reverse=not first_click)
    if column == 4:
        return sorted(rows,
                      key=lambda item: recall_estimate(item, recent.get(item["id"], ())),
                      reverse=not first_click)
    raise ValueError("Unknown library sort column.")


class LibraryModel(QAbstractTableModel):
    HEADERS = ("Word / expression", "Source level", "Difficulty", "Content", "Study")

    def __init__(self):
        super().__init__()
        self.rows = []
        self.recent = {}
        self.pending = set()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 5

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            if role == Qt.ItemDataRole.DisplayRole:
                return self.HEADERS[section]
            if role == Qt.ItemDataRole.ToolTipRole:
                return (
                    "Sort Z to A, then A to Z",
                    "Sort B2 to A1, then A1 to B2; Personal last",
                    "Sort Hard to Easy, then Easy to Hard; Unknown last",
                    "Show unready first, then ready first",
                    "Show lowest estimated recall first, then highest",
                )[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        item = self.rows[index.row()]
        if role == Qt.ItemDataRole.UserRole:
            return item["id"]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if index.column() == 0:
                return item["original_text"]
            if index.column() == 1:
                return item["starter_level"] or "Personal"
            if index.column() == 2:
                return item["difficulty"].title()
            if index.column() == 3:
                parts = []
                if item["review_note"]:
                    parts.append("Needs review")
                elif item["difficulty"] == "unknown":
                    parts.append("Unknown")
                else:
                    parts.append("Ready" if item["content_ready"] else "Incomplete")
                if item["id"] in self.pending:
                    parts.append("Recheck due")
                return " - ".join(parts)
            return f"{item['remembered_count']}/{item['study_attempts']} - " + recall_label(
                item, self.recent.get(item["id"], ()))
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 2):
            return Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        return None

    def replace(self, rows, recent, pending):
        if len(rows) == len(self.rows) and all(
                old["id"] == new["id"] for old, new in zip(self.rows, rows)):
            old_rows, old_recent, old_pending = self.rows, self.recent, self.pending
            self.rows, self.recent, self.pending = rows, recent, pending
            same_recent = old_recent is recent or old_recent == recent
            same_pending = old_pending is pending or old_pending == pending
            for index, (old, new) in enumerate(zip(old_rows, rows)):
                if old != new or (not same_pending and
                        (new["id"] in old_pending) != (new["id"] in pending)) or \
                        (not same_recent and old_recent.get(new["id"]) != recent.get(new["id"])):
                    self.dataChanged.emit(self.index(index, 0), self.index(index, 4))
            return
        self.beginResetModel()
        self.rows = rows
        self.recent = recent
        self.pending = pending
        self.endResetModel()

    def update_entry(self, entry_id, db):
        for index, item in enumerate(self.rows):
            if item["id"] == entry_id:
                fresh = db.get(entry_id)
                item.update({key: fresh[key] for key in ("study_attempts", "remembered_count", "again_count")})
                self.recent[entry_id] = db.recent_first_round_outcomes([entry_id]).get(entry_id, ())
                self.dataChanged.emit(self.index(index, 4), self.index(index, 4))
                return


class ProfileDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.selected = None
        self.setWindowTitle("Vocabulary profiles")
        self.resize(490, 360)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label("Choose a profile", "title"))
        layout.addWidget(label("Each profile has separate words, settings, and study history.", "muted", True))
        profiles = service.profiles()
        if profiles:
            section(layout, "OPEN EXISTING")
            self.existing = QComboBox()
            for profile in profiles:
                self.existing.addItem(profile["name"], profile["id"])
                self.existing.setItemData(self.existing.count() - 1, profile["name"], Qt.ItemDataRole.ToolTipRole)
            active = service.profile["id"] if service.profile else None
            for index, profile in enumerate(profiles):
                if profile["id"] == active:
                    self.existing.setCurrentIndex(index)
            layout.addWidget(self.existing)
            layout.addWidget(button("Open profile", self.open_existing))
        section(layout, "CREATE NEW")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Profile name")
        self.name.setAccessibleName("New profile name")
        layout.addWidget(self.name)
        self.source = QComboBox()
        self.source.setAccessibleName("New profile starting library")
        self.source.addItem(f"Use default deck - {service.store._preset()['card_count']:,} ready cards", "default")
        self.source.addItem("Start from zero - empty library", "empty")
        layout.addWidget(self.source)
        layout.addWidget(button("Create profile", self.create_new, "primary"))
        layout.addStretch(1)
        self.name.returnPressed.connect(self.create_new)
        self.name.setFocus()

    def open_existing(self):
        try:
            self.service.open_profile(self.existing.currentData())
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            message(self, "Profile could not open", exc)
            return
        self.selected = self.service.profile
        self.accept()

    def create_new(self):
        try:
            self.selected = self.service.create_profile(self.name.text(), self.source.currentData())
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            message(self, "Profile could not be created", exc)
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, service):
        super().__init__()
        self.service = service
        self.selected_id = None
        self.loaded_revision = None
        self.loaded_values = None
        self.loading_detail = False
        self.selecting = False
        self.closing_wait = False
        self.last_add_success = 0.0
        self.sort_column = None
        self.sort_first_click = True
        self.library_base_rows = []
        self.setWindowTitle("Italian Vocabulary")
        self.resize(1200, 840)
        self.setMinimumSize(900, 630)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 14, 18, 10)
        outer.setSpacing(9)
        header = QHBoxLayout()
        header.addWidget(label("Italian Vocabulary", "title"))
        header.addStretch(1)
        self.profile_button = button("Profile", self.choose_profile)
        header.addWidget(self.profile_button)
        outer.addLayout(header)
        self.tabs = QTabWidget()
        self.library_tab = QWidget()
        self.study_tab = QWidget()
        self.settings_tab = QWidget()
        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.study_tab, "Study")
        self.tabs.addTab(self.settings_tab, "Settings")
        outer.addWidget(self.tabs, 1)
        self.status = label("Ready. Changes are saved to your profile.", "muted", True)
        self.status.setAccessibleName("Application status")
        outer.addWidget(self.status)
        self.build_library()
        self.build_study()
        self.build_settings()
        self.tabs.currentChanged.connect(self.tab_changed)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.focus_search)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_shortcut)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_evaluation)
        self.poll_timer.start(100)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.refresh_library)
        self.load_profile_ui()
        self.quick_add.setFocus()

    def say(self, text):
        self.status.setText(str(text))

    def fail(self, title, exc):
        self.say(f"{title}: {exc}")
        message(self, title, exc)

    def load_profile_ui(self):
        self.profile_button.setText("Profile: " + self.service.profile["name"])
        self.profile_button.setToolTip(self.service.profile["name"])
        self.setWindowTitle("Italian Vocabulary - " + self.service.profile["name"])
        self.clear_detail()
        self.refresh_settings()
        self.refresh_library()
        self.refresh_study_count()
        self.study_pages.setCurrentIndex(0)
        self.tabs.setCurrentIndex(0)

    def choose_profile(self):
        if self.service.running or self.service.worker and self.service.worker.is_alive():
            self.fail("Evaluation in progress", "Finish or cancel evaluation before switching profiles.")
            return
        if not self.guard_dirty():
            return
        dialog = ProfileDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.load_profile_ui()
            self.say("Opened profile " + self.service.profile["name"] + ".")

    def build_library(self):
        layout = QVBoxLayout(self.library_tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        add_line = QHBoxLayout()
        self.quick_add = QLineEdit()
        self.quick_add.setPlaceholderText("Add a word or expression")
        self.quick_add.setAccessibleName("Add a word or expression")
        self.quick_add.setMaxLength(500)
        self.quick_add.returnPressed.connect(self.add_word)
        add_line.addWidget(self.quick_add, 1)
        add_line.addWidget(button("Add word", self.add_word, "primary"))
        layout.addLayout(add_line)
        self.add_error = label("", "error", True)
        self.add_error.hide()
        layout.addWidget(self.add_error)
        summary = QHBoxLayout()
        self.count_labels = {}
        for key in DIFFICULTIES:
            item = label("", "muted")
            self.count_labels[key] = item
            summary.addWidget(item)
        summary.addStretch(1)
        self.shown = label("", "muted")
        summary.addWidget(self.shown)
        layout.addLayout(summary)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search words, context, and notes")
        self.search.setAccessibleName("Search library")
        self.search.textChanged.connect(lambda: self.search_timer.start(180))
        search_row.addWidget(self.search, 1)
        search_row.addWidget(button("Clear search", self.search.clear))
        self.filter_button = button("Filters", self.toggle_filters)
        search_row.addWidget(self.filter_button)
        search_row.addWidget(button("Reset filters", self.reset_filters))
        layout.addLayout(search_row)
        self.filter_panel = QFrame()
        self.filter_panel.setProperty("role", "surface")
        filter_layout = QGridLayout(self.filter_panel)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.addWidget(label("DIFFICULTY", "eyebrow"), 0, 0)
        self.library_difficulties = {}
        for index, difficulty in enumerate(DIFFICULTIES, 1):
            check = QCheckBox(difficulty.title())
            check.setChecked(True)
            check.toggled.connect(lambda _checked: self.refresh_library())
            self.library_difficulties[difficulty] = check
            filter_layout.addWidget(check, 0, index)
        filter_layout.addWidget(label("SOURCE LEVEL", "eyebrow"), 1, 0)
        self.library_levels = {}
        for index, level in enumerate((*LEVELS, "Personal"), 1):
            check = QCheckBox(level)
            check.setChecked(True)
            check.toggled.connect(lambda _checked: self.refresh_library())
            self.library_levels[level] = check
            filter_layout.addWidget(check, 1, index)
        self.review_check = QCheckBox("Needs review only")
        self.review_check.toggled.connect(lambda _checked: self.refresh_library())
        filter_layout.addWidget(self.review_check, 2, 0, 1, 3)
        self.filter_panel.hide()
        layout.addWidget(self.filter_panel)
        actions = QHBoxLayout()
        self.evaluate_button = button("Evaluate pending words", self.evaluate, "primary")
        actions.addWidget(self.evaluate_button)
        self.cancel_button = button("Cancel evaluation", self.cancel_evaluation)
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)
        self.utilities = button("More actions", self.show_utilities)
        actions.addWidget(self.utilities)
        actions.addStretch(1)
        self.eval_progress = label("", "muted")
        actions.addWidget(self.eval_progress)
        layout.addLayout(actions)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableView()
        self.model = LibraryModel()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self.sort_library)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 260)
        for column, width in ((1, 90), (2, 90), (3, 115), (4, 135)):
            self.table.setColumnWidth(column, width)
        self.table.selectionModel().currentRowChanged.connect(self.selection_changed)
        self.splitter.addWidget(self.table)
        self.detail_panel = self.build_detail()
        self.splitter.addWidget(self.detail_panel)
        self.splitter.setSizes([720, 390])
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self.empty = label("", "muted", True)
        layout.addWidget(self.empty)

    def build_detail(self):
        panel = QFrame()
        panel.setProperty("role", "surface")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 10, 12, 10)
        panel_layout.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(label("Entry details", "section"))
        top.addStretch(1)
        self.dirty_label = label("", "muted")
        top.addWidget(self.dirty_label)
        panel_layout.addLayout(top)
        self.detail_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        empty_layout.addWidget(label("Select a word", "section"), alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(label("Its content, context, notes, and study history appear here.", "muted", True),
                               alignment=Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch(2)
        self.detail_stack.addWidget(empty)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)
        section(editor_layout, "WORD AND CONTEXT")
        self.detail_word = QLineEdit()
        self.detail_word.setAccessibleName("Word or expression")
        editor_layout.addWidget(label("Word or expression"))
        editor_layout.addWidget(self.detail_word)
        editor_layout.addWidget(label("Context sentence"))
        self.detail_fields = {}
        for key, height in (("context", 68),):
            field = QTextEdit()
            field.setAccessibleName("Context sentence")
            field.setFixedHeight(height)
            field.setAcceptRichText(False)
            self.detail_fields[key] = field
            editor_layout.addWidget(field)
        editor_layout.addWidget(label("Changing the word or context resets its classification. Notes and study history are preserved.", "muted", True))
        section(editor_layout, "LEARNING CONTENT")
        for key, title, height in (("gloss_en", "English meaning", 66),
                                   ("definition_it", "Italian definition", 82),
                                   ("example_it", "Italian example", 82)):
            editor_layout.addWidget(label(title))
            field = QTextEdit()
            field.setAccessibleName(title)
            field.setFixedHeight(height)
            field.setAcceptRichText(False)
            self.detail_fields[key] = field
            editor_layout.addWidget(field)
        section(editor_layout, "PERSONAL NOTES")
        field = QTextEdit()
        field.setAccessibleName("Personal notes")
        field.setFixedHeight(94)
        field.setAcceptRichText(False)
        self.detail_fields["notes"] = field
        editor_layout.addWidget(field)
        section(editor_layout, "DIFFICULTY")
        self.detail_difficulty = QComboBox()
        self.detail_difficulty.setAccessibleName("Difficulty")
        for value in DIFFICULTIES:
            self.detail_difficulty.addItem(value.title(), value)
        editor_layout.addWidget(self.detail_difficulty)
        editor_layout.addWidget(label("Classification explanation"))
        field = QTextEdit()
        field.setAccessibleName("Classification explanation")
        field.setFixedHeight(82)
        field.setAcceptRichText(False)
        self.detail_fields["difficulty_reason"] = field
        editor_layout.addWidget(field)
        section(editor_layout, "REVIEW AND STUDY")
        self.detail_meta = label("", "muted", True)
        self.detail_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        editor_layout.addWidget(self.detail_meta)
        editor_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(editor)
        self.detail_stack.addWidget(scroll)
        panel_layout.addWidget(self.detail_stack, 1)
        self.detail_footer = QWidget()
        footer = QHBoxLayout(self.detail_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self.save_button = button("Save changes", self.save_detail, "primary")
        footer.addWidget(self.save_button)
        footer.addStretch(1)
        footer.addWidget(button("Delete entry", self.delete_entry, "danger"))
        panel_layout.addWidget(self.detail_footer)
        self.detail_word.textChanged.connect(self.dirty_changed)
        self.detail_difficulty.currentIndexChanged.connect(self.dirty_changed)
        for field in self.detail_fields.values():
            field.textChanged.connect(self.dirty_changed)
        return panel

    def detail_values(self):
        return {"original_text": self.detail_word.text(),
                "difficulty": self.detail_difficulty.currentData(),
                **{key: field.toPlainText() for key, field in self.detail_fields.items()}}

    def is_dirty(self):
        return self.selected_id is not None and self.loaded_values is not None and self.detail_values() != self.loaded_values

    def dirty_changed(self):
        if not self.loading_detail:
            dirty = self.is_dirty()
            self.dirty_label.setText("Unsaved changes" if dirty else "")
            self.save_button.setEnabled(dirty)

    def clear_detail(self):
        self.loading_detail = True
        self.selected_id = None
        self.loaded_revision = None
        self.loaded_values = None
        self.detail_stack.setCurrentIndex(0)
        self.detail_footer.hide()
        self.save_button.setEnabled(False)
        self.dirty_label.setText("")
        self.loading_detail = False

    def load_detail(self, entry_id):
        record = self.service.db.get(entry_id)
        if not record:
            self.clear_detail()
            return
        self.loading_detail = True
        self.selected_id = entry_id
        self.loaded_revision = record["revision"]
        self.detail_word.setText(record["original_text"])
        self.detail_difficulty.setCurrentIndex(DIFFICULTIES.index(record["difficulty"]))
        for key, field in self.detail_fields.items():
            field.setPlainText(record[key] or "")
        recent = self.service.db.recent_first_round_outcomes([entry_id]).get(entry_id, ())
        attempts = record["study_attempts"]
        meta = ["Topics: " + ", ".join(topics_for_card(record)),
                "Source level: " + (record["starter_level"] or "Personal"),
                f"Study: {record['remembered_count']}/{attempts} remembered",
                f"Estimated recall: {recall_estimate(record, recent):.0%} ({recall_label(record, recent)})",
                "Added: " + record["created_at"],
                f"Revision: {record['revision']}"]
        if record["starter_kind"]:
            meta.insert(2, "Source type: " + record["starter_kind"])
        if record["review_note"]:
            meta.insert(0, "Needs review: " + record["review_note"])
        if record["classification_model"]:
            meta.append("Classified by " + record["classification_model"])
        self.detail_meta.setText("\n".join(meta))
        self.loaded_values = self.detail_values()
        self.detail_stack.setCurrentIndex(1)
        self.detail_footer.show()
        self.save_button.setEnabled(False)
        self.dirty_label.setText("")
        self.loading_detail = False

    def guard_dirty(self):
        if not self.is_dirty():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save changes before leaving this entry?")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == save:
            return self.save_detail()
        return box.clickedButton() == discard

    def selection_changed(self, current, previous):
        if self.selecting:
            return
        entry_id = self.model.data(current.siblingAtColumn(0), Qt.ItemDataRole.UserRole) if current.isValid() else None
        if entry_id == self.selected_id:
            return
        if not self.guard_dirty():
            self.selecting = True
            try:
                if previous.isValid():
                    self.table.setCurrentIndex(previous)
                else:
                    self.table.clearSelection()
            finally:
                self.selecting = False
            return
        self.load_detail(entry_id) if entry_id is not None else self.clear_detail()

    def sort_library(self, column):
        if column == self.sort_column:
            self.sort_first_click = not self.sort_first_click
        else:
            self.sort_column = column
            self.sort_first_click = True
        first_descending = column in (0, 1, 2)
        descending = first_descending == self.sort_first_click
        header = self.table.horizontalHeader()
        header.setSortIndicator(column, Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder)
        header.setSortIndicatorShown(True)
        selected_id = self.selected_id
        rows = sort_library_rows(self.library_base_rows, self.model.recent,
                                 self.sort_column, self.sort_first_click)
        self.selecting = True
        try:
            self.model.replace(rows, self.model.recent, self.model.pending)
            target = None if selected_id is None else next(
                (index for index, item in enumerate(rows) if item["id"] == selected_id), None)
            if target is not None:
                self.table.selectRow(target)
        finally:
            self.selecting = False
        self.table.verticalScrollBar().setValue(0)
        first_labels = (
            "Word: Z to A", "Source level: B2 to A1, Personal last",
            "Difficulty: Hard to Easy, Unknown last", "Content: unready first",
            "Study: lowest estimated recall first",
        )
        second_labels = (
            "Word: A to Z", "Source level: A1 to B2, Personal last",
            "Difficulty: Easy to Hard, Unknown last", "Content: ready first",
            "Study: highest estimated recall first",
        )
        self.say("Sorted by " + (first_labels if self.sort_first_click else second_labels)[column] + ".")

    def refresh_library(self, select_id=None):
        if self.service.db is None:
            return
        if select_id is None:
            select_id = self.selected_id
        scroll = self.table.verticalScrollBar().value()
        difficulties = [key for key, check in self.library_difficulties.items() if check.isChecked()]
        levels = [key for key, check in self.library_levels.items() if check.isChecked()]
        try:
            rows = self.service.library(self.search.text(), difficulties, levels,
                                        self.review_check.isChecked(), compact=True)
            ids = [item["id"] for item in rows]
            recent = self.service.db.recent_first_round_outcomes(ids)
            pending = self.service.db.pending_recheck_ids()
            counts = self.service.db.counts()
            reviewed = self.service.db.review_count()
            base_rows = rows
            rows = sort_library_rows(base_rows, recent, self.sort_column, self.sort_first_click)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Could not refresh library", exc)
            return
        self.selecting = True
        self.library_base_rows = base_rows
        self.model.replace(rows, recent, pending)
        target = None if select_id is None else next(
            (index for index, item in enumerate(rows) if item["id"] == select_id), None)
        if target is not None:
            self.table.selectRow(target)
            if not self.is_dirty() and (select_id != self.selected_id or
                    self.service.db.get(select_id)["revision"] != self.loaded_revision):
                self.load_detail(select_id)
        else:
            self.table.clearSelection()
            if not self.is_dirty():
                self.clear_detail()
        self.table.verticalScrollBar().setValue(scroll)
        self.selecting = False
        total = sum(counts.values())
        for key in DIFFICULTIES:
            self.count_labels[key].setText(f"{key.title()} {counts[key]:,}")
        self.shown.setText(f"{len(rows):,} shown / {total:,} total")
        self.review_check.setText(f"Needs review only ({reviewed:,})")
        active = (self.search.text() or len(difficulties) != 4 or len(levels) != 5 or self.review_check.isChecked())
        self.filter_button.setText("Filters - active" if active else "Filters")
        self.empty.setText("No words yet. Add a word above." if not total else
                           "No words match. Clear search or reset filters." if not rows else "")
        self.empty.setVisible(not rows)
        self.refresh_evaluation_ui()

    def toggle_filters(self):
        self.filter_panel.setVisible(not self.filter_panel.isVisible())

    def reset_filters(self):
        self.search.clear()
        for check in (*self.library_difficulties.values(), *self.library_levels.values()):
            check.blockSignals(True)
            check.setChecked(True)
            check.blockSignals(False)
        self.review_check.blockSignals(True)
        self.review_check.setChecked(False)
        self.review_check.blockSignals(False)
        self.refresh_library()

    def add_word(self):
        if not self.quick_add.text().strip() and time.monotonic() - self.last_add_success < 0.5:
            return
        if not self.guard_dirty():
            return
        try:
            entry_id, created = self.service.add(self.quick_add.text())
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.add_error.setText(str(exc))
            self.add_error.show()
            self.quick_add.setFocus()
            return
        self.add_error.hide()
        self.last_add_success = time.monotonic()
        self.quick_add.clear()
        self.reset_filters()
        self.refresh_library(select_id=entry_id)
        self.table.scrollTo(self.table.currentIndex())
        self.quick_add.setFocus()
        self.say("Saved under Unknown." if created else "Already saved. Selected the existing entry.")

    def save_detail(self):
        if self.selected_id is None:
            return False
        entry_id = self.selected_id
        before = self.service.db.get(entry_id)
        try:
            updated = self.service.update(entry_id, self.detail_values(), self.loaded_revision)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Could not save changes", exc)
            return False
        self.loaded_revision = updated["revision"]
        self.loaded_values = self.detail_values()
        self.dirty_changed()
        self.refresh_library(select_id=entry_id)
        self.load_detail(entry_id)
        if entry_id not in [row["id"] for row in self.model.rows]:
            self.say("Changes saved. This entry no longer matches the current filters.")
        elif updated["difficulty"] == "unknown" and (updated["original_text"] != before["original_text"] or
                                                       updated["context"] != before["context"]):
            self.say("Saved. Word or context changed, so classification returned to Unknown.")
        else:
            self.say("Changes saved.")
        self.refresh_study_count()
        return True

    def delete_entry(self):
        if self.selected_id is None:
            return
        record = self.service.db.get(self.selected_id)
        if not message(self, "Delete entry", f"Delete '{record['original_text']}' and its study history?", question=True):
            return
        try:
            self.service.delete(self.selected_id)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Could not delete entry", exc)
            return
        self.clear_detail()
        self.refresh_library()
        self.refresh_study_count()
        self.say("Entry deleted.")

    def show_utilities(self):
        menu = QMenu(self)
        for title, callback in (("Export prompt", self.export_prompt), ("Copy prompt", self.copy_prompt),
                                ("Import AI JSON", self.import_ai),
                                ("Add A1-B2 starter collection", self.import_starter),
                                ("Export backup", self.export_backup),
                                ("Import backup", self.import_backup),
                                ("Retry reviewed with Codex", self.retry_reviewed)):
            menu.addAction(title, callback)
        menu.exec(self.utilities.mapToGlobal(self.utilities.rect().bottomLeft()))

    def export_prompt(self):
        if not self.save_settings(auto_recheck=False):
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder for evaluation files")
        if not folder:
            return
        try:
            path, count, batches = self.service.export_prompt(folder)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Prompt export failed", exc)
            return
        self.say(f"Exported {count:,} entries in {batches} tracked batches to {path}.")

    def copy_prompt(self):
        if not self.save_settings(auto_recheck=False):
            return
        try:
            prompt, request_id, count = self.service.copy_prompt()
            QApplication.clipboard().setText(prompt)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Could not copy prompt", exc)
            return
        self.say(f"Copied prompt for {count} entries. Request ID: {request_id}.")

    def import_ai(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import classifier JSON", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        try:
            counts = self.service.import_ai(path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("AI JSON rejected", exc)
            return
        self.refresh_library()
        self.refresh_study_count()
        self.say(f"Imported: {counts['applied']} classified, {counts['needs_review']} need review, {counts['skipped_stale']} stale.")

    def import_starter(self):
        try:
            collection = load_starter_catalog()
            counts = starter_counts(collection)
        except (OSError, ValueError) as exc:
            self.fail("Starter collection unavailable", exc)
            return
        summary = ", ".join(f"{level}: {counts[level]:,}" for level in LEVELS)
        if not message(self, "Add starter collection",
                       f"Add up to {len(collection):,} words and expressions?\n\n{summary}\n\n"
                       "Existing entries and study history stay unchanged. New entries start as Unknown. "
                       "AI evaluation is separate and uses your Codex allowance.", question=True):
            return
        try:
            result = self.service.db.import_starter(collection)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Starter import failed", exc)
            return
        self.refresh_library()
        self.refresh_study_count()
        self.say(f"Starter collection: {result['added']:,} added, {result['skipped']:,} already saved.")

    def export_backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export vocabulary backup", "italian-vocabulary-backup.json", "JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            count = self.service.export_backup(path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Backup export failed", exc)
            return
        self.say(f"Exported {count:,} entries to {path}.")

    def import_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import vocabulary backup", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        if not self.guard_dirty():
            return
        try:
            counts = self.service.import_backup(path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.fail("Backup import failed", exc)
            return
        self.refresh_settings()
        self.refresh_library()
        self.refresh_study_count()
        self.say(f"Backup import: {counts['added']} added, {counts['skipped']} skipped, "
                 f"{counts['conflicting']} conflicts kept unchanged.")

    def build_study(self):
        layout = QVBoxLayout(self.study_tab)
        layout.setContentsMargins(0, 8, 0, 0)
        self.study_pages = QStackedWidget()
        layout.addWidget(self.study_pages, 1)
        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        setup = QWidget()
        setup_layout = QVBoxLayout(setup)
        setup_layout.setSpacing(14)
        setup_layout.addWidget(label("Build a study session", "title"))
        setup_layout.addWidget(label("Choose the cards you want to practise. Study uses your saved content offline.", "muted", True))
        section(setup_layout, "DIFFICULTY")
        difficulties = QHBoxLayout()
        self.study_difficulties = {}
        for difficulty in DIFFICULTIES[1:]:
            check = QCheckBox(difficulty.title())
            check.setChecked(True)
            check.toggled.connect(self.refresh_study_count)
            self.study_difficulties[difficulty] = check
            difficulties.addWidget(check)
        difficulties.addStretch(1)
        setup_layout.addLayout(difficulties)
        selectors = QHBoxLayout()
        self.study_topic = QComboBox()
        self.study_topic.setAccessibleName("Study topic")
        self.study_topic.addItems((ALL_TOPICS, *TOPICS))
        self.study_topic.currentIndexChanged.connect(self.refresh_study_count)
        self.study_level = QComboBox()
        self.study_level.setAccessibleName("Study source word level")
        self.study_level.addItems(STUDY_LEVELS)
        self.study_level.currentIndexChanged.connect(self.refresh_study_count)
        topic_col = QVBoxLayout()
        topic_col.addWidget(label("Topic"))
        topic_col.addWidget(self.study_topic)
        level_col = QVBoxLayout()
        level_col.addWidget(label("Source word level"))
        level_col.addWidget(self.study_level)
        selectors.addLayout(topic_col, 2)
        selectors.addLayout(level_col, 1)
        setup_layout.addLayout(selectors)
        section(setup_layout, "SESSION SIZE")
        size_row = QHBoxLayout()
        self.study_size = QComboBox()
        self.study_size.setAccessibleName("Study session size")
        self.study_size.addItems(("10", "20", "All", "Custom"))
        self.study_size.setCurrentText("20")
        self.study_size.currentTextChanged.connect(self.size_changed)
        size_row.addWidget(self.study_size)
        self.custom_size = QLineEdit()
        self.custom_size.setPlaceholderText("Positive whole number")
        self.custom_size.setAccessibleName("Custom session size")
        self.custom_size.hide()
        size_row.addWidget(self.custom_size)
        size_row.addStretch(1)
        setup_layout.addLayout(size_row)
        self.study_count = label("", "muted", True)
        setup_layout.addWidget(self.study_count)
        self.start_button = button("Start session", self.start_study, "primary")
        setup_layout.addWidget(self.start_button, alignment=Qt.AlignmentFlag.AlignLeft)
        setup_layout.addWidget(label(
            "Missing cards? Evaluate Unknown words or complete entries in Library.", "muted", True))
        setup_layout.addWidget(label("Lower estimated recall increases selection chance. This is not a due-date schedule.", "muted", True))
        setup_layout.addStretch(1)
        setup_scroll.setWidget(setup)
        self.study_pages.addWidget(setup_scroll)

        active = QWidget()
        active_layout = QVBoxLayout(active)
        active_layout.setContentsMargins(4, 8, 4, 0)
        active_layout.setSpacing(12)
        heading = QHBoxLayout()
        self.study_progress = label("", "muted")
        heading.addWidget(self.study_progress)
        heading.addStretch(1)
        heading.addWidget(button("End session", self.end_study))
        active_layout.addLayout(heading)
        card = QFrame()
        card.setProperty("role", "surface")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 22, 22, 22)
        self.card_word = label("", "card", True)
        self.card_word.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.card_word)
        answer_scroll = QScrollArea()
        answer_scroll.setWidgetResizable(True)
        answer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        answer_content = QWidget()
        answer_layout = QVBoxLayout(answer_content)
        answer_layout.setSpacing(11)
        answer_layout.addStretch(1)
        self.reveal_hint = label("Reveal the Italian example to continue.", "muted", True)
        self.reveal_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        answer_layout.addWidget(self.reveal_hint)
        self.example_section = QWidget()
        example_layout = QVBoxLayout(self.example_section)
        example_layout.setContentsMargins(0, 0, 0, 0)
        example_layout.addWidget(label("ITALIAN EXAMPLE", "eyebrow"))
        self.card_example = label("", None, True)
        self.card_example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        example_layout.addWidget(self.card_example)
        answer_layout.addWidget(self.example_section)
        self.answer_section = QWidget()
        answer_inner = QVBoxLayout(self.answer_section)
        answer_inner.setContentsMargins(0, 0, 0, 0)
        answer_inner.addWidget(label("ENGLISH MEANING", "eyebrow"))
        self.card_gloss = label("", None, True)
        self.card_gloss.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        answer_inner.addWidget(self.card_gloss)
        answer_inner.addWidget(label("ITALIAN DEFINITION", "eyebrow"))
        self.card_definition = label("", None, True)
        self.card_definition.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        answer_inner.addWidget(self.card_definition)
        answer_layout.addWidget(self.answer_section)
        answer_layout.addStretch(1)
        answer_scroll.setWidget(answer_content)
        card_layout.addWidget(answer_scroll, 1)
        active_layout.addWidget(card, 1)
        action_row = QHBoxLayout()
        self.reveal_button = button("Reveal example  Space", self.reveal, "primary")
        self.remembered_button = button("Remembered  R", lambda: self.answer(True))
        self.again_button = button("Again  A", lambda: self.answer(False))
        action_row.addStretch(1)
        action_row.addWidget(self.reveal_button)
        action_row.addWidget(self.remembered_button)
        action_row.addWidget(self.again_button)
        action_row.addStretch(1)
        active_layout.addLayout(action_row)
        active_layout.addWidget(label("Remembered and Again record study history. They do not change difficulty.", "muted", True))
        self.study_pages.addWidget(active)

        complete = QWidget()
        complete_layout = QVBoxLayout(complete)
        complete_layout.setSpacing(16)
        complete_layout.addStretch(1)
        complete_layout.addWidget(label("Round complete", "title"))
        self.complete_summary = label("", None, True)
        complete_layout.addWidget(self.complete_summary)
        self.another_button = button("Another round with Again words", self.another_round, "primary")
        complete_layout.addWidget(self.another_button, alignment=Qt.AlignmentFlag.AlignLeft)
        complete_layout.addWidget(button("Return to setup", self.end_study), alignment=Qt.AlignmentFlag.AlignLeft)
        complete_layout.addStretch(2)
        self.study_pages.addWidget(complete)

    def chosen_study_difficulties(self):
        return [key for key, check in self.study_difficulties.items() if check.isChecked()]

    def size_changed(self):
        custom = self.study_size.currentText() == "Custom"
        self.custom_size.setVisible(custom)
        if custom:
            self.custom_size.setFocus()

    def refresh_study_count(self):
        if not hasattr(self, "study_count") or self.service.db is None:
            return
        chosen = self.chosen_study_difficulties()
        if not chosen:
            self.study_count.setText("Select at least one difficulty.")
            self.start_button.setEnabled(False)
            return
        try:
            ready_count, incomplete = self.service.study_pool_counts(
                chosen, self.study_topic.currentText(), self.study_level.currentText())
            unknown = self.service.db.counts()["unknown"]
            reviewed = self.service.db.review_count()
        except (ValueError, sqlite3.Error) as exc:
            self.study_count.setText(str(exc))
            self.start_button.setEnabled(False)
            return
        if ready_count:
            info = f"{ready_count:,} study-ready cards match."
        else:
            info = "No study-ready cards match."
        if incomplete:
            info += f" {incomplete:,} selected entries need learning content."
        if unknown:
            info += f" {unknown:,} Unknown entries are excluded; {reviewed:,} need review."
        self.study_count.setText(info)
        self.start_button.setEnabled(bool(ready_count))

    def start_study(self):
        try:
            total, incomplete = self.service.start_session(
                self.chosen_study_difficulties(), self.study_topic.currentText(),
                self.study_level.currentText(), self.study_size.currentText(), self.custom_size.text())
        except (ValueError, sqlite3.Error) as exc:
            self.say(str(exc))
            if self.study_size.currentText() == "Custom":
                self.custom_size.setFocus()
            return
        self.study_pages.setCurrentIndex(1)
        self.show_card()
        self.say(f"Started {total} cards. {incomplete} incomplete entries excluded.")

    def show_card(self):
        session = self.service.session
        if not session:
            return
        card = session.current
        if card is None:
            self.study_pages.setCurrentIndex(2)
            round_answers = session.round_total
            round_again = len(session.again_cards)
            self.complete_summary.setText(
                f"Round {session.round_number}: {round_answers - round_again} remembered, {round_again} again.\n"
                f"All rounds: {session.remembered} remembered, {session.again} again.")
            self.another_button.setVisible(bool(session.again_cards))
            return
        self.study_progress.setText(f"Round {session.round_number} - Card {session.index + 1} of {session.round_total}")
        self.card_word.setText(card["original_text"])
        self.card_example.clear()
        self.card_gloss.clear()
        self.card_definition.clear()
        self.reveal_hint.show()
        self.example_section.hide()
        self.answer_section.hide()
        self.reveal_button.setText("Reveal example  Space")
        self.reveal_button.show()
        self.remembered_button.hide()
        self.again_button.hide()
        self.reveal_button.setFocus()

    def reveal(self):
        stage = self.service.reveal()
        if stage == 1:
            self.card_example.setText(self.service.session.current["example_it"])
            self.reveal_hint.hide()
            self.example_section.show()
            self.reveal_button.setText("Reveal answer  Space")
        elif stage == 2:
            card = self.service.session.current
            self.card_gloss.setText(card["gloss_en"])
            self.card_definition.setText(card["definition_it"])
            self.answer_section.show()
            self.reveal_button.hide()
            self.remembered_button.show()
            self.again_button.show()
            self.remembered_button.setFocus()

    def answer(self, remembered):
        if not self.service.session or not self.service.session.revealed:
            return
        self.remembered_button.setEnabled(False)
        self.again_button.setEnabled(False)
        try:
            entry_id = self.service.answer(remembered)
        except (ValueError, sqlite3.Error) as exc:
            self.fail("Could not save study result", exc)
            self.remembered_button.setEnabled(True)
            self.again_button.setEnabled(True)
            return
        self.model.update_entry(entry_id, self.service.db)
        if self.sort_column == 4:
            self.refresh_library()
        if self.selected_id == entry_id and not self.is_dirty():
            self.load_detail(entry_id)
        self.remembered_button.setEnabled(True)
        self.again_button.setEnabled(True)
        self.show_card()

    def another_round(self):
        if self.service.next_round():
            self.study_pages.setCurrentIndex(1)
            self.show_card()

    def end_study(self):
        self.service.end_session()
        self.study_pages.setCurrentIndex(0)
        self.refresh_study_count()

    def build_settings(self):
        outer = QVBoxLayout(self.settings_tab)
        outer.setContentsMargins(0, 8, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(13)
        layout.addWidget(label("Learner and evaluation settings", "title"))
        layout.addWidget(label("Your learner level affects difficulty estimates. Source CEFR belongs to the collection; Remembered and Again never change difficulty.", "muted", True))
        panel = QFrame()
        panel.setProperty("role", "surface")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(10)
        section(panel_layout, "LEARNER")
        form = QFormLayout()
        form.setSpacing(12)
        self.level_field = QComboBox()
        self.level_field.setAccessibleName("Learner level")
        self.level_field.addItems(("A1", "A2", "B1", "B2", "C1", "C2"))
        self.level_field.currentTextChanged.connect(self.level_changed)
        form.addRow("Learner level", self.level_field)
        panel_layout.addLayout(form)
        panel_layout.addWidget(label("Changing this saves immediately and re-evaluates previously AI-evaluated entries. Existing content stays visible until a valid result arrives.", "muted", True))
        section(panel_layout, "CODEX EVALUATION")
        advanced = QFormLayout()
        advanced.setSpacing(12)
        self.model_field = QLineEdit()
        self.model_field.setAccessibleName("Codex model")
        advanced.addRow("Model", self.model_field)
        self.effort_field = QComboBox()
        self.effort_field.setAccessibleName("Codex reasoning effort")
        self.effort_field.addItems(("low", "medium", "high", "xhigh", "max"))
        advanced.addRow("Reasoning effort", self.effort_field)
        self.timeout_field = QLineEdit()
        self.timeout_field.setAccessibleName("Timeout per batch in seconds")
        advanced.addRow("Timeout per batch (seconds)", self.timeout_field)
        panel_layout.addLayout(advanced)
        panel_layout.addWidget(button("Save evaluation settings", self.save_settings, "primary"),
                               alignment=Qt.AlignmentFlag.AlignLeft)
        self.recheck_label = label("", "muted", True)
        panel_layout.addWidget(self.recheck_label)
        layout.addWidget(panel)
        layout.addWidget(label("AI evaluation uses the official Codex CLI and your ChatGPT login. Library and Study work offline.", "muted", True))
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def refresh_settings(self):
        settings = self.service.settings()
        self.level_field.blockSignals(True)
        self.level_field.setCurrentText(settings["level"])
        self.level_field.blockSignals(False)
        self.model_field.setText(settings["model"])
        self.effort_field.setCurrentText(settings["effort"])
        self.timeout_field.setText(settings["timeout"])
        pending = self.service.db.pending_recheck_count()
        self.recheck_label.setText(f"{pending:,} previously evaluated entries await re-evaluation." if pending else
                                   "No previously evaluated entries await re-evaluation.")

    def settings_values(self):
        return {"level": self.level_field.currentText(), "model": self.model_field.text(),
                "effort": self.effort_field.currentText(), "timeout": self.timeout_field.text()}

    def save_settings(self, *, auto_recheck=True):
        try:
            changed, pending = self.service.save_settings(self.settings_values(), auto_recheck=auto_recheck)
        except (ValueError, sqlite3.Error) as exc:
            self.fail("Could not save settings", exc)
            return False
        self.refresh_settings()
        self.refresh_library()
        if changed and pending:
            self.say(f"Learner level saved. {pending:,} AI entries queued for re-evaluation.")
            if auto_recheck and not self.service.running:
                QTimer.singleShot(0, self.start_queued_recheck)
        elif changed:
            self.say("Learner level saved.")
        else:
            self.say("Evaluation settings saved.")
        return True

    def level_changed(self):
        self.save_settings()

    def start_queued_recheck(self):
        if self.service.running or self.service.worker and self.service.worker.is_alive():
            QTimer.singleShot(150, self.start_queued_recheck)
            return
        only = self.service.queued_recheck
        if only is None:
            return
        self.service.queued_recheck = None
        try:
            self.service.evaluate(recheck_only=only)
        except (RuntimeError, ValueError, sqlite3.Error) as exc:
            self.fail("Could not start re-evaluation", exc)
        self.refresh_evaluation_ui()

    def evaluate(self):
        if not self.save_settings(auto_recheck=False):
            return
        snapshot = self.service.evaluation_snapshot()
        if len(snapshot) > 100:
            count, accepted = QInputDialog.getInt(self, "Choose evaluation size",
                f"{len(snapshot):,} entries are pending. How many should this run evaluate? "
                "Up to five Codex jobs run at once, with up to 20 entries each. "
                "Every job uses your Codex allowance.", min(50, len(snapshot)), 1, len(snapshot))
            if not accepted:
                return
            snapshot = snapshot[:count]
        self.start_evaluation(snapshot)

    def retry_reviewed(self):
        snapshot = self.service.evaluation_snapshot(retry_reviewed=True)
        if not snapshot:
            self.say("No reviewed entries need retrying.")
            return
        preview = ", ".join(item["original_text"] for item in snapshot[:20])
        count, accepted = QInputDialog.getInt(self, "Retry reviewed entries",
            f"{len(snapshot):,} entries need review. First entries: {preview}\n\n"
            "Check review notes first. Unclear spellings and single letters usually need edits. "
            "Retrying unchanged uses Codex allowance. How many should this run retry?",
            min(20, len(snapshot)), 1, len(snapshot))
        if accepted and self.save_settings(auto_recheck=False):
            self.start_evaluation(snapshot[:count])

    def start_evaluation(self, snapshot):
        try:
            started = self.service.evaluate(snapshot)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self.fail("Could not start evaluation", exc)
            return
        self.refresh_evaluation_ui()
        self.say("Evaluation started. You can keep using the library." if started else
                 "No pending entries. Review flagged words in Library or add new words.")

    def cancel_evaluation(self):
        self.service.cancel_evaluation()
        self.refresh_evaluation_ui()
        self.say("Cancelling evaluation. Saved batches remain saved.")

    def refresh_evaluation_ui(self):
        state = self.service.evaluation
        running = self.service.running
        self.evaluate_button.setEnabled(not running)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(state["state"] == "running")
        if state["batches"]:
            self.eval_progress.setText(
                f"{state['state'].title()} - {state['done']}/{state['batches']} batches, "
                f"{state['applied'] + state['needs_review'] + state['stale']}/{state['total']} entries")
        else:
            self.eval_progress.setText("")
        self.recheck_label.setText(
            f"{self.service.db.pending_recheck_count():,} previously evaluated entries await re-evaluation.")

    def poll_evaluation(self):
        if self.service.db is None:
            return
        events = self.service.poll_events()
        for event in events:
            kind = event[0]
            if kind == "running":
                self.say(f"Evaluating batch {event[1]} of {self.service.evaluation['batches']}...")
            elif kind == "batch":
                counts = event[2]
                self.refresh_library()
                self.refresh_study_count()
                self.say(f"Batch {event[1]} saved: {counts['applied']} classified, "
                         f"{counts['needs_review']} need review, {counts['skipped_stale']} stale.")
            elif kind == "rejected":
                self.fail("Evaluation result rejected", event[2])
            elif kind == "failed":
                self.fail("Evaluation failed", f"Batch {event[1]} failed. Saved batches remain.\n\n{event[2]}")
            elif kind == "cancelled":
                if self.service.evaluation["state"] != "failed":
                    self.say("Evaluation cancelled. Saved batches remain; pending work can be retried.")
            elif kind == "completed":
                state = self.service.evaluation
                self.say(f"Evaluation finished: {state['applied']} classified, "
                         f"{state['needs_review']} need review, {state['stale']} stale.")
            elif kind == "queued_recheck":
                self.service.queued_recheck = event[1]
                QTimer.singleShot(150, self.start_queued_recheck)
        if events:
            self.refresh_evaluation_ui()

    def tab_changed(self, index):
        if index == 1 and self.study_pages.currentIndex() == 0:
            self.refresh_study_count()

    def focus_search(self):
        self.tabs.setCurrentIndex(0)
        self.search.setFocus()
        self.search.selectAll()

    def save_shortcut(self):
        if self.tabs.currentIndex() == 0 and self.is_dirty():
            self.save_detail()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and self.isActiveWindow() and \
                self.tabs.currentIndex() == 1 and self.study_pages.currentIndex() == 1:
            focused = QApplication.focusWidget()
            if isinstance(focused, (QLineEdit, QTextEdit, QComboBox)) or event.isAutoRepeat():
                return False
            key = event.key()
            if key == Qt.Key.Key_Space and not isinstance(focused, QPushButton):
                self.reveal()
                return True
            if key == Qt.Key.Key_R:
                self.answer(True)
                return True
            if key == Qt.Key.Key_A:
                self.answer(False)
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if self.tabs.currentIndex() == 1 and self.study_pages.currentIndex() == 1:
            focused = QApplication.focusWidget()
            if not isinstance(focused, (QLineEdit, QTextEdit, QComboBox)) and not event.isAutoRepeat():
                key = event.key()
                if key == Qt.Key.Key_Space:
                    # Focused buttons consume Space themselves, avoiding a second rating.
                    if not isinstance(focused, QPushButton):
                        self.reveal()
                    return
                if key == Qt.Key.Key_R:
                    self.answer(True)
                    return
                if key == Qt.Key.Key_A:
                    self.answer(False)
                    return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.closing_wait:
            if self.service.close():
                event.accept()
            else:
                event.ignore()
            return
        if not self.guard_dirty():
            event.ignore()
            return
        if self.service.close():
            event.accept()
        else:
            self.closing_wait = True
            self.say("Stopping evaluation before closing...")
            event.ignore()
            QTimer.singleShot(150, self.finish_close)

    def finish_close(self):
        if self.service.worker and self.service.worker.is_alive():
            self.service.poll_events()
            QTimer.singleShot(150, self.finish_close)
        else:
            self.close()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    try:
        service = VocabularyService()
    except (OSError, ValueError, sqlite3.Error) as exc:
        message(None, "Profiles could not open", exc)
        return 1
    if service.profile is None:
        dialog = ProfileDialog(service)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return 0
    window = MainWindow(service)
    app.installEventFilter(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
