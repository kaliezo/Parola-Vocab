# Parola Vocab guide

See [README.md](README.md) for an introduction. This guide covers installation,
launching, everyday use, and backups.

## Set up and launch

Download **Code > Download ZIP** from the repository, or use a ZIP someone
sent you. Extract the entire archive into a folder you want to keep. Open
that folder and keep its files together; do not launch from inside the ZIP.
Git and a GitHub account are not needed.

Use Python **3.10-3.14**. Internet access is needed once to install the UI
packages; Library and Study then work offline. Linux has been validated here;
Windows and macOS setup instructions are provided but have not been tested here.

### Linux

Install Python, pip, and virtual environment support through your distribution.
On Fedora, use `sudo dnf install python3 python3-pip`; on Ubuntu/Debian, use
`sudo apt install python3 python3-venv python3-pip` if they are missing.
Open a terminal in the extracted folder and run these commands one at a time:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
bash run.sh
```

For later launches, run only `bash run.sh` from the app folder. For a KDE Plasma
or GNOME menu shortcut, optionally run
`python3 create_desktop_launcher.py --install`. To update an existing shortcut
or its location, add `--replace`.

### Windows 10 (1809 or later) or Windows 11

Install a standard 64-bit [Python 3.10-3.14](https://www.python.org/downloads/windows/).
Include the Python launcher if offered. In File Explorer, open the extracted
folder, type `cmd` in its address bar, and press Enter. Run:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
run_windows.bat
```

For later launches, double-click `run_windows.bat`. If `py` is unavailable,
use `python -m venv .venv` instead, after checking `python --version`. If you
have multiple Python versions, select a supported one, for example with
`py -3.14 -m venv .venv`.

### macOS 13 or newer

Install a standard [Python 3.10-3.14](https://www.python.org/downloads/macos/)
for Intel or Apple Silicon. Complete the installer by running
`Install Certificates.command` in its Python folder under Applications, as
explained in the [Python installation guide](https://docs.python.org/3/using/mac.html).
Open Terminal, type `cd `, drag the extracted app folder into the terminal,
and press Enter. Then run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python qt_app.py
```

For later launches, open Terminal in the app folder and run only
`.venv/bin/python qt_app.py`.

### First launch and setup problems

Create a profile with the bundled 7,695-card deck or an empty library, then
open Study and start a session. AI evaluation is optional and requires a
separate Codex CLI installation and your own login.

If installation says no matching package was found, check `python3 --version`
(or `py -3 --version` on Windows) and compare your OS with the
[pinned UI package requirements](https://pypi.org/project/PySide6-Essentials/6.11.2/).
Linux wheels require glibc 2.34+ on x86-64 or 2.39+ on ARM64. If your default
Python is unsupported, create the environment with an installed supported
version, such as `python3.14 -m venv .venv`.

The app stores your profiles locally. A shared release ZIP contains the app
and bundled deck, not the sender's personal profiles, credentials, or history.

## Daily use

Library places quick add, search, compact counts, filters, and evaluation controls above a large vocabulary table. Select a row to edit word and context, English meaning, Italian definition and example, notes, difficulty, and classification explanation. Save changes explicitly. Changing word or context resets classification but keeps notes and study history. Unsaved edits are guarded when changing selection, profile, or closing. The detail pane and list can be resized with the divider.

The Filters panel separates difficulty from source CEFR level and includes needs-review entries. Counts describe all entries; the shown count describes the current result. Click a table heading to sort the filtered results and click it again to reverse that order. Word starts Z to A, source level starts B2 to A1 with Personal last, difficulty starts Hard to Easy with Unknown last, Content starts with entries not ready to study, and Study starts with the lowest estimated recall. The untouched table keeps its original word order. More actions contains prompt export and copy, AI JSON import, text-only starter collection import, backup export and import, and explicit retry of reviewed entries. Native file pickers select real files and folders.

Study has setup, active card, and completed round states. Choose Easy, Medium, Hard, topic, source level, and 10, 20, All, or Custom cards. The count shows matching study-ready cards and incomplete entries and updates quickly with large decks. Starting a session also uses less memory on large profiles. Evaluation and entry editing stay in Library. Active cards show only Italian at first, then reveal the example, then the English meaning and Italian definition. Remembered and Again become available only after the full answer. Another round uses Again cards. Results show round and cumulative answers. Study uses weighted recall selection, not a timed due-date schedule.

Keyboard: Enter adds from the Library quick-add field; Ctrl+F focuses Library search; Ctrl+S saves a dirty Library editor; Space reveals in an active card; R records Remembered; A records Again. Study shortcuts are ignored while typing in editable controls. Normal text editing and accented Italian input use Qt's native controls.

Settings shows learner level separately from Codex model, reasoning effort, and batch timeout. Changing learner level saves immediately, queues re-evaluation of AI-evaluated entries, and starts it when possible. Other settings use Save evaluation settings. Profile switching waits for evaluation to finish or be cancelled.

## Evaluation and data

Evaluation runs up to five Codex CLI jobs concurrently, with 20 entries per batch. A run snapshots pending entries; new words wait for another run. Each valid batch is saved as it completes. Cancel preserves already saved results. Edited, deleted, manually classified, and old learner-level results cannot overwrite current data. Needs-review entries are excluded from ordinary retries. Runs over 100 pending entries ask how many to process. The Codex CLI must be installed and signed in for AI evaluation. Check with `codex login status` and sign in with `codex login` if needed.

The bundled text-only A1-B2 starter collection and the default study-ready deck are distinct. The starter import merges 7,695 unique entries as Unknown, skipping existing accent-sensitive keys. `si` and `sì` remain distinct. Its levels are source suggestions, separate from learner difficulty. The word list comes from the [community CEFR vocabulary dataset](https://github.com/Talhakasikci/cefr-vocabulary-dataset), with personal and educational use permitted by its maintainer. Expressions were selected editorially with consultation of Wiktionary's Italian phrase, idiom, and proverb categories. Source levels are approximate, not an official syllabus.

On Linux, profile databases live at `$XDG_DATA_HOME/italian_vocabulary/profiles/` when `XDG_DATA_HOME` is absolute, or `~/.local/share/italian_vocabulary/profiles/` otherwise. `profiles.json` stores names and the active profile. Legacy `vocabulary.sqlite3` is copied safely on first profile migration; the original remains. Backup JSON version 4 includes entries, settings, review history, source tags, tracked requests, and pending rechecks. Versions 1-4 can be imported through the existing validator and transactional merge. Runtime data is never stored in this repository.

## Developer validation

Normal launch uses `qt_app.py`. The legacy Tkinter entry point remains
available with `python3 app.py` as a migration fallback.

Run from this directory:

```bash
python3 -m py_compile app.py qt_app.py qt_theme.py service.py classifier.py storage.py study.py starter.py profiles.py create_desktop_launcher.py tests/test_vocabulary.py tests/test_qt_service.py
python3 -m unittest discover -s tests -v
```

Tests use temporary profile stores and SQLite databases. Do not run GUI or Codex tests against a real profile merely for validation. The UI smoke checks in `tests/test_qt_service.py` use Qt's offscreen platform and temporary profiles.

For a repeatable Linux performance and RAM check, run
`python3 benchmarks/profile_app.py --runs 5 --output /tmp/italian-vocabulary-performance.json`.
It opens the Qt interface offscreen using temporary profiles with the bundled
7,695-card deck, measures Library and 20-card Study operations, and separately
tests 100,000 synthetic study answers. It never opens a real user profile or
calls Codex. Measurements include resident RAM (RSS), proportional RAM (PSS),
private RAM, and peak RSS. See [PERFORMANCE.md](PERFORMANCE.md) for the recorded
before/after comparison and its limits.

The table uses smaller in-memory records, short sessions retain learning content
only for selected cards, and recall lookup fetches at most the requested number
of recent answers per word. SQLite adds word-order and first-round history
indexes automatically on opening a profile; this can add a one-time indexing
cost and some database disk space. Saved entries, history, filtering, weighted
selection, and all study controls retain their behavior.
