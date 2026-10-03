"""Create a project launcher and optionally install it for the current user."""

import argparse
import os
from pathlib import Path


def desktop_quote(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"') + '"'


def applications_directory():
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home and Path(xdg_data_home).is_absolute() else Path.home() / ".local" / "share"
    return base / "applications"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="add the launcher to this user's application menu")
    parser.add_argument("--replace", action="store_true", help="replace an existing different launcher when installing")
    args = parser.parse_args()
    if args.replace and not args.install:
        parser.error("--replace requires --install")

    project = Path(__file__).resolve().parent
    launcher = project / "Vocabulary.desktop"
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Vocabulary\n"
        "Comment=Save and study Italian words\n"
        "Keywords=Italian;Vocabulary;Flashcards;Words;\n"
        "Exec=/usr/bin/bash " + desktop_quote(project / "run.sh") + "\n"
        "Icon=accessories-dictionary\n"
        "Terminal=false\n"
        "Categories=Education;\n"
    )
    launcher.write_text(content, encoding="utf-8")
    print(launcher)
    if args.install:
        target = applications_directory() / "italian-vocabulary.desktop"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_text(encoding="utf-8") == content:
                print("Already installed:", target)
                return
            if not args.replace:
                raise FileExistsError(f"Different launcher already exists at {target}. Use --install --replace to replace it.")
        target.write_text(content, encoding="utf-8")
        print("Installed:", target)


if __name__ == "__main__":
    main()
