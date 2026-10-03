# Start using Parola Vocab

This folder contains the complete app and its bundled 7,695-card Italian deck.
You do not need Git, GitHub, or the sender's account. Your words and study
history are saved in your own local profile.

## First: extract the ZIP

Extract the entire ZIP to a folder you want to keep, such as Documents. Open
the extracted app folder. Do not launch the app from inside the ZIP.
Keep all the files together; the app loads its deck from this folder.

Python and the UI packages are required. Internet access is needed to install
them once. After setup, Library and Study work offline.

## Windows 10 (1809 or later) or Windows 11

1. Install Python 3.10 or newer from https://www.python.org/downloads/windows/.
   Include the Python launcher if the installer offers it.
2. Open the extracted app folder in File Explorer. Click the address
   bar, type `cmd`, and press Enter to open a terminal in that folder.
3. Run these two commands, one at a time:

   ```bat
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.lock
   ```

4. Double-click `run_windows.bat` to open Parola Vocab. Use this same file for
   later launches; installation is only needed once.

If `py` is unavailable but `python --version` reports Python 3.10 or newer,
use `python -m venv .venv` for the first command instead.

The Windows launcher is included, but this release was validated on Linux.

## Linux

Install Python 3.10 or newer with pip and virtual environment support. Open
a terminal in the extracted app folder and run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
bash run.sh
```

For later launches, run `bash run.sh`. Using Bash explicitly also works when
your ZIP extractor does not preserve the launcher's executable permission.
On Fedora, if pip is missing, install it with `sudo dnf install python3-pip`
and retry the setup commands.

For a KDE Plasma or GNOME application menu shortcut, optionally run:

```bash
python3 create_desktop_launcher.py --install
```

## First launch

Create a profile and choose the bundled deck for 7,695 ready-to-study cards,
or choose an empty library to add your own words. Open Study, choose a card
count, and start. Space reveals a card; after revealing the complete answer,
R records Remembered and A records Again.

AI evaluation is optional and requires a separate Codex CLI installation
and your own login. It is not required for the bundled deck or offline study.

See [GUIDE.md](GUIDE.md) for the full usage guide, profile storage, and backup options.
The ZIP contains the app and bundled deck, not the sender's personal profiles,
credentials, backups, or study history.
