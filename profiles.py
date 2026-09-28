"""Local profile storage and the shared, study-ready starter deck."""

import json
import os
import sqlite3
import tempfile
import unicodedata
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from storage import VocabularyDB, data_directory, normalize_word, now


DEFAULT_SETTINGS = Path(__file__).with_name("default_profile.json")
DEFAULT_CARDS = Path(__file__).with_name("default_cards.jsonl")
CARD_FIELDS = {"word", "difficulty", "definition_it", "gloss_en", "example_it",
               "difficulty_reason", "source_level", "kind", "source"}


def _profile_name(value):
    if not isinstance(value, str):
        raise ValueError("Enter a profile name.")
    name = " ".join(unicodedata.normalize("NFC", value).split())
    if not name or len(name) > 60 or any(unicodedata.category(ch).startswith("C") for ch in name):
        raise ValueError("Profile name must be 1 to 60 printable characters.")
    return name


class ProfileStore:
    def __init__(self, root=None, settings_path=None, cards_path=None):
        self.root = Path(root) if root is not None else data_directory()
        self.index_path = self.root / "profiles.json"
        self.profiles_path = self.root / "profiles"
        self.legacy_path = self.root / "vocabulary.sqlite3"
        self.settings_path = Path(settings_path) if settings_path else DEFAULT_SETTINGS
        self.cards_path = Path(cards_path) if cards_path else DEFAULT_CARDS

    def _index(self):
        if not self.index_path.exists():
            return {"format_version": 1, "active_id": None, "profiles": []}
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("The local profile list could not be read.") from exc
        if not isinstance(data, dict) or set(data) != {"format_version", "active_id", "profiles"} \
                or data["format_version"] != 1 or not isinstance(data["profiles"], list):
            raise ValueError("The local profile list is invalid.")
        ids = set()
        names = set()
        for profile in data["profiles"]:
            if not isinstance(profile, dict) or set(profile) != {"id", "name"} \
                    or not isinstance(profile["id"], str) or len(profile["id"]) != 32 \
                    or any(ch not in "0123456789abcdef" for ch in profile["id"]) \
                    or not isinstance(profile["name"], str) \
                    or _profile_name(profile["name"]) != profile["name"] \
                    or profile["id"] in ids or profile["name"].casefold() in names:
                raise ValueError("The local profile list is invalid.")
            ids.add(profile["id"])
            names.add(profile["name"].casefold())
        if data["active_id"] is not None and data["active_id"] not in ids:
            raise ValueError("The selected profile is missing from the local profile list.")
        return data

    def _write_index(self, data):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.root,
                                             prefix=".profiles-", delete=False) as file:
                temporary = Path(file.name)
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.index_path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def list_profiles(self):
        return list(self._index()["profiles"])

    def active_profile(self):
        data = self._index()
        return next((item for item in data["profiles"] if item["id"] == data["active_id"]), None)

    def database_path(self, profile_id):
        if not any(item["id"] == profile_id for item in self.list_profiles()):
            raise ValueError("Unknown profile.")
        path = self.profiles_path / f"{profile_id}.sqlite3"
        if not path.is_file():
            raise FileNotFoundError(f"Profile database is missing: {path}")
        return path

    def activate(self, profile_id):
        data = self._index()
        if not any(item["id"] == profile_id for item in data["profiles"]):
            raise ValueError("Unknown profile.")
        data["active_id"] = profile_id
        self._write_index(data)

    def _preset(self):
        data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"format_version", "card_count", "settings"} \
                or data["format_version"] != 1 or type(data["card_count"]) is not int \
                or data["card_count"] < 0 or not isinstance(data["settings"], dict) \
                or set(data["settings"]) != {"level", "model", "effort", "timeout"}:
            raise ValueError("The bundled profile settings are invalid.")
        settings = data["settings"]
        if settings["level"] not in ("A1", "A2", "B1", "B2", "C1", "C2") \
                or not isinstance(settings["model"], str) or not settings["model"] \
                or settings["effort"] not in ("low", "medium", "high", "xhigh", "max") \
                or not isinstance(settings["timeout"], str) or not settings["timeout"].isdigit() \
                or not 30 <= int(settings["timeout"]) <= 3600:
            raise ValueError("The bundled profile settings are invalid.")
        return data

    def _add_default_cards(self, db, preset):
        timestamp = now()
        seen = set()
        count = 0
        with self.cards_path.open("r", encoding="utf-8") as file, db.conn:
            for line in file:
                card = json.loads(line)
                if not isinstance(card, dict) or set(card) != CARD_FIELDS \
                        or card["difficulty"] not in ("easy", "medium", "hard") \
                        or card["source_level"] not in ("A1", "A2", "B1", "B2") \
                        or card["kind"] not in ("word", "phrase", "idiom", "proverb") \
                        or card["source"] not in ("community-cefr", "editorial") \
                        or any(not isinstance(card[key], str) or not card[key].strip()
                               for key in ("definition_it", "gloss_en", "example_it", "difficulty_reason")):
                    raise ValueError("The bundled card deck is invalid.")
                word, key = normalize_word(card["word"])
                if word != card["word"] or key in seen:
                    raise ValueError("The bundled card deck has a duplicate or invalid word.")
                seen.add(key)
                cursor = db.conn.execute(
                    "INSERT INTO entries (original_text, normalized_key, difficulty, definition_it, "
                    "gloss_en, example_it, difficulty_reason, created_at, modified_at, classified_at, "
                    "classification_model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (word, key, card["difficulty"], card["definition_it"], card["gloss_en"],
                     card["example_it"], card["difficulty_reason"], timestamp, timestamp,
                     timestamp, preset["settings"]["model"]),
                )
                db.conn.execute(
                    "INSERT INTO starter_catalog (entry_id, cefr_level, kind, source) VALUES (?, ?, ?, ?)",
                    (cursor.lastrowid, card["source_level"], card["kind"], card["source"]),
                )
                count += 1
            if count != preset["card_count"]:
                raise ValueError("The bundled card count is wrong.")
        db.set_settings(preset["settings"])

    def create(self, name, source="default"):
        name = _profile_name(name)
        if source not in ("default", "empty", "legacy"):
            raise ValueError("Choose default cards or an empty library.")
        data = self._index()
        if any(item["name"].casefold() == name.casefold() for item in data["profiles"]):
            raise ValueError("A profile with that name already exists.")
        if source == "legacy" and not self.legacy_path.is_file():
            raise FileNotFoundError("No existing vocabulary database was found.")
        profile_id = uuid4().hex
        self.profiles_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.profiles_path / f".{profile_id}.sqlite3.tmp"
        target = self.profiles_path / f"{profile_id}.sqlite3"
        try:
            if source == "legacy":
                with closing(sqlite3.connect(f"file:{self.legacy_path}?mode=ro", uri=True)) as old:
                    with closing(sqlite3.connect(temporary)) as new:
                        old.backup(new)
                with closing(sqlite3.connect(f"file:{temporary}?mode=ro", uri=True)) as check:
                    if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("Existing vocabulary database could not be copied safely.")
            else:
                db = VocabularyDB(temporary)
                try:
                    preset = self._preset()
                    if source == "default":
                        self._add_default_cards(db, preset)
                    else:
                        db.set_settings(preset["settings"])
                finally:
                    db.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            data["profiles"].append({"id": profile_id, "name": name})
            data["active_id"] = profile_id
            self._write_index(data)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return {"id": profile_id, "name": name}

    def migrate_legacy_if_needed(self):
        if not self.index_path.exists() and self.legacy_path.is_file():
            return self.create("Existing Library", source="legacy")
        return None
