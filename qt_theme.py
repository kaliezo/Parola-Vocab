"""Shared visual tokens and Qt Widgets styling."""

BG = "#0d1117"
SURFACE = "#171d26"
RAISED = "#202a36"
FIELD = "#111821"
BORDER = "#344252"
TEXT = "#f0f4f8"
MUTED = "#a7b4c5"
ACCENT = "#78b5ff"
SUCCESS = "#81d9b4"
WARNING = "#f0c478"
ERROR = "#ff9298"

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Noto Sans', 'Segoe UI', sans-serif; font-size: 13px; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel[role='muted'] {{ color: {MUTED}; }}
QLabel[role='eyebrow'] {{ color: {ACCENT}; font-size: 11px; font-weight: 700; }}
QLabel[role='title'] {{ font-size: 20px; font-weight: 700; }}
QLabel[role='section'] {{ font-size: 15px; font-weight: 700; }}
QLabel[role='card'] {{ font-size: 30px; font-weight: 700; }}
QLabel[role='error'] {{ color: {ERROR}; }}
QFrame[role='surface'], QScrollArea[role='surface'] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 9px; }}
QFrame[role='surface'] QLabel, QFrame[role='surface'] QCheckBox {{ background: transparent; }}
QLineEdit, QTextEdit, QComboBox, QSpinBox {{ background: {FIELD}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 9px; selection-background-color: #284b70; }}
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QPushButton:focus, QCheckBox:focus, QTableView:focus {{ border: 2px solid {ACCENT}; }}
QPushButton {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 6px; padding: 8px 13px; }}
QPushButton:hover {{ background: #2c3948; }}
QPushButton:disabled {{ color: #8291a3; background: #19212b; }}
QPushButton[role='primary'] {{ background: {ACCENT}; color: #091522; border-color: {ACCENT}; font-weight: 700; }}
QPushButton[role='primary']:hover {{ background: #9ac8ff; }}
QPushButton[role='danger'] {{ color: {ERROR}; }}
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 20px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:focus {{ border: 2px solid {ACCENT}; }}
QTableView {{ background: {SURFACE}; alternate-background-color: #1b2430; border: 1px solid {BORDER}; border-radius: 8px; gridline-color: {BORDER}; selection-background-color: #284b70; selection-color: {TEXT}; }}
QHeaderView::section {{ background: {RAISED}; color: {MUTED}; border: 0; border-bottom: 1px solid {BORDER}; padding: 9px; font-weight: 700; }}
QScrollBar:vertical {{ width: 11px; background: {BG}; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 25px; }}
QMenu {{ background: {RAISED}; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 7px 16px; }}
QMenu::item:selected {{ background: #284b70; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
"""
