# Italian Vocabulary

A native desktop workspace for collecting and studying Italian vocabulary. The interface uses PySide6 Qt Widgets; the existing Python SQLite, study, profile, and Codex CLI code remains the source of truth. Library and Study work offline after installation. AI evaluation is optional and uses your existing Codex CLI login, not an API key.

On a clean install, create a profile with either the bundled 7,695-card study-ready deck or an empty library. Profiles have separate words, settings, and study history. The existing profile directory and database format are unchanged.

## Set up and launch

Install Python 3.10 or newer. Create a project-local environment and install the pinned UI dependency once:

Fedora Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
./run.sh
```

Windows 10 (1809 or later) and Windows 11:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
run_windows.bat
```

The launchers work from paths with spaces. They use `.venv` when present and show installation instructions if PySide6 is missing. If Fedora does not include `python3-venv` or a working pip in your Python installation, install the corresponding Fedora Python packages. Normal launch requires no build step, internet access, or second terminal. Qt opens a native desktop window with system file pickers. The Windows launcher is provided but has not been run on Windows in this workspace.

The previous Tkinter entry point remains available with `python3 app.py` as a migration fallback. Normal launch uses `qt_app.py`. No profile data is copied or relocated by the UI migration.

To add the app to the KDE Plasma or GNOME application menu, run `python3 create_desktop_launcher.py --install`. If you move the project, rerun with `--install --replace`. This generates a user-level desktop entry that calls `run.sh` and does not need administrator privileges.

## Daily use

Library places quick add, search, compact counts, filters, and evaluation controls above a large vocabulary table. Select a row to edit word and context, English meaning, Italian definition and example, notes, difficulty, and classification explanation. Save changes explicitly. Changing word or context resets classification but keeps notes and study history. Unsaved edits are guarded when changing selection, profile, or closing. The detail pane and list can be resized with the divider.

The Filters panel separates difficulty from source CEFR level and includes needs-review entries. Counts describe all entries; the shown count describes the current result. More actions contains prompt export and copy, AI JSON import, text-only starter collection import, backup export and import, and explicit retry of reviewed entries. Native file pickers select real files and folders.

Study has setup, active card, and completed round states. Choose Easy, Medium, Hard, topic, source level, and 10, 20, All, or Custom cards. The count shows matching study-ready cards and incomplete entries. Evaluation and entry editing stay in Library. Active cards show only Italian at first, then reveal the example, then the English meaning and Italian definition. Remembered and Again become available only after the full answer. Another round uses Again cards. Results show round and cumulative answers. Study uses weighted recall selection, not a timed due-date schedule.

Keyboard: Enter adds from the Library quick-add field; Ctrl+F focuses Library search; Ctrl+S saves a dirty Library editor; Space reveals in an active card; R records Remembered; A records Again. Study shortcuts are ignored while typing in editable controls. Normal text editing and accented Italian input use Qt's native controls.

Settings shows learner level separately from Codex model, reasoning effort, and batch timeout. Changing learner level saves immediately, queues re-evaluation of AI-evaluated entries, and starts it when possible. Other settings use Save evaluation settings. Profile switching waits for evaluation to finish or be cancelled.

## Evaluation and data

Evaluation runs up to five Codex CLI jobs concurrently, with 20 entries per batch. A run snapshots pending entries; new words wait for another run. Each valid batch is saved as it completes. Cancel preserves already saved results. Edited, deleted, manually classified, and old learner-level results cannot overwrite current data. Needs-review entries are excluded from ordinary retries. Runs over 100 pending entries ask how many to process. The Codex CLI must be installed and signed in for AI evaluation. Check with `codex login status` and sign in with `codex login` if needed.

The bundled text-only A1-B2 starter collection and the default study-ready deck are distinct. The starter import merges 7,695 unique entries as Unknown, skipping existing accent-sensitive keys. `si` and `sì` remain distinct. Its levels are source suggestions, separate from learner difficulty. The word list comes from the [community CEFR vocabulary dataset](https://github.com/Talhakasikci/cefr-vocabulary-dataset), with personal and educational use permitted by its maintainer. Expressions were selected editorially with consultation of Wiktionary's Italian phrase, idiom, and proverb categories. Source levels are approximate, not an official syllabus.

On Linux, profile databases live at `$XDG_DATA_HOME/italian_vocabulary/profiles/` when `XDG_DATA_HOME` is absolute, or `~/.local/share/italian_vocabulary/profiles/` otherwise. `profiles.json` stores names and the active profile. Legacy `vocabulary.sqlite3` is copied safely on first profile migration; the original remains. Backup JSON version 4 includes entries, settings, review history, source tags, tracked requests, and pending rechecks. Versions 1-4 can be imported through the existing validator and transactional merge. Runtime data is never stored in this repository.

## Developer validation

Run from this directory:

```bash
python3 -m py_compile app.py qt_app.py qt_theme.py service.py classifier.py storage.py study.py starter.py profiles.py create_desktop_launcher.py tests/test_vocabulary.py tests/test_qt_service.py
python3 -m unittest discover -s tests -v
```

Tests use temporary profile stores and SQLite databases. Do not run GUI or Codex tests against a real profile merely for validation. The UI smoke checks in `tests/test_qt_service.py` use Qt's offscreen platform and temporary profiles.
