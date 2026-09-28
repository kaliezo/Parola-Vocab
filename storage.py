"""SQLite storage for a single user's vocabulary and study history."""

import json
import os
import sqlite3
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DIFFICULTIES = ("unknown", "easy", "medium", "hard")
MAX_WORD_LENGTH = 120
ENTRY_COLUMNS = (
    "id", "original_text", "normalized_key", "context", "notes", "difficulty",
    "definition_it", "gloss_en", "example_it", "difficulty_reason", "review_note",
    "created_at", "modified_at", "classified_at", "classification_model", "revision",
    "study_attempts", "remembered_count", "again_count",
)
TEXT_LIMITS = {
    "context": 2000, "notes": 4000, "definition_it": 500, "gloss_en": 300,
    "example_it": 500, "difficulty_reason": 500, "review_note": 500,
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def data_directory():
    base = os.environ.get("XDG_DATA_HOME")
    if base and Path(base).is_absolute():
        return Path(base) / "italian_vocabulary"
    return Path.home() / ".local" / "share" / "italian_vocabulary"


def normalize_word(value):
    if not isinstance(value, str):
        raise ValueError("Word must be text.")
    word = " ".join(unicodedata.normalize("NFC", value).split())
    if not word:
        raise ValueError("Enter a word or short expression.")
    if len(word) > MAX_WORD_LENGTH:
        raise ValueError(f"Entries are limited to {MAX_WORD_LENGTH} characters.")
    if any(unicodedata.category(ch).startswith("C") for ch in word):
        raise ValueError("Entries cannot contain control characters.")
    return word, word.casefold()


def checked_text(value, field):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = unicodedata.normalize("NFC", value.strip())
    if len(value) > TEXT_LIMITS[field]:
        raise ValueError(f"{field} is limited to {TEXT_LIMITS[field]} characters.")
    return value


class VocabularyDB:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else data_directory() / "vocabulary.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def close(self):
        self.conn.close()

    def _create_schema(self):
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY,
                    original_text TEXT NOT NULL,
                    normalized_key TEXT NOT NULL UNIQUE,
                    context TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    difficulty TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (difficulty IN ('unknown', 'easy', 'medium', 'hard')),
                    definition_it TEXT, gloss_en TEXT, example_it TEXT,
                    difficulty_reason TEXT, review_note TEXT,
                    created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
                    classified_at TEXT, classification_model TEXT,
                    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
                    study_attempts INTEGER NOT NULL DEFAULT 0 CHECK (study_attempts >= 0),
                    remembered_count INTEGER NOT NULL DEFAULT 0 CHECK (remembered_count >= 0),
                    again_count INTEGER NOT NULL DEFAULT 0 CHECK (again_count >= 0)
                );
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    model TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reclassification_pending (
                    entry_id INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS study_reviews (
                    review_id TEXT PRIMARY KEY,
                    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL CHECK (round_number >= 1),
                    remembered INTEGER NOT NULL CHECK (remembered IN (0, 1)),
                    reviewed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS study_reviews_recent
                    ON study_reviews(entry_id, round_number, reviewed_at DESC);
                CREATE TABLE IF NOT EXISTS starter_catalog (
                    entry_id INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
                    cefr_level TEXT NOT NULL CHECK (cefr_level IN ('A1', 'A2', 'B1', 'B2')),
                    kind TEXT NOT NULL CHECK (kind IN ('word', 'phrase', 'idiom', 'proverb')),
                    source TEXT NOT NULL CHECK (source IN ('community-cefr', 'editorial'))
                );
                CREATE INDEX IF NOT EXISTS starter_catalog_level ON starter_catalog(cefr_level);
            """)

    def add(self, value):
        word, key = normalize_word(value)
        timestamp = now()
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO entries (original_text, normalized_key, created_at, modified_at) VALUES (?, ?, ?, ?)",
                    (word, key, timestamp, timestamp),
                )
            return cur.lastrowid, True
        except sqlite3.IntegrityError:
            row = self.conn.execute("SELECT id FROM entries WHERE normalized_key=?", (key,)).fetchone()
            if row is None:
                raise
            return row["id"], False

    def get(self, entry_id):
        row = self.conn.execute(
            "SELECT e.*, s.cefr_level AS starter_level, s.kind AS starter_kind "
            "FROM entries e LEFT JOIN starter_catalog s ON s.entry_id=e.id WHERE e.id=?",
            (entry_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_entries(self, search="", difficulties=None, starter_levels=None, review_only=False):
        difficulties = tuple(DIFFICULTIES if difficulties is None else difficulties)
        if not difficulties:
            return []
        clauses = ["e.difficulty IN (" + ",".join("?" for _ in difficulties) + ")"]
        args = list(difficulties)
        if starter_levels is not None:
            selected = tuple(starter_levels)
            if not selected or any(level not in ("A1", "A2", "B1", "B2", "Personal") for level in selected):
                return []
            options = []
            source_levels = [level for level in selected if level != "Personal"]
            if source_levels:
                options.append("s.cefr_level IN (" + ",".join("?" for _ in source_levels) + ")")
                args.extend(source_levels)
            if "Personal" in selected:
                options.append("s.entry_id IS NULL")
            clauses.append("(" + " OR ".join(options) + ")")
        if search.strip():
            clauses.append("(e.original_text LIKE ? OR e.context LIKE ? OR e.notes LIKE ?)")
            term = "%" + search.strip() + "%"
            args.extend((term, term, term))
        if review_only:
            clauses.append("e.difficulty='unknown' AND e.review_note IS NOT NULL")
        rows = self.conn.execute(
            "SELECT e.*, s.cefr_level AS starter_level, s.kind AS starter_kind "
            "FROM entries e LEFT JOIN starter_catalog s ON s.entry_id=e.id WHERE " +
            " AND ".join(clauses) + " ORDER BY e.original_text COLLATE NOCASE, e.id",
            args,
        ).fetchall()
        return [dict(row) for row in rows]

    def starter_counts(self):
        counts = dict.fromkeys(("A1", "A2", "B1", "B2"), 0)
        for row in self.conn.execute(
            "SELECT cefr_level, COUNT(*) AS n FROM starter_catalog GROUP BY cefr_level"
        ):
            counts[row["cefr_level"]] = row["n"]
        return counts

    def import_starter(self, collection):
        """Merge a validated starter collection without changing existing entries."""
        from starter import KINDS, LEVELS, SOURCES

        if not isinstance(collection, list):
            raise ValueError("Starter collection must be a list.")
        validated = []
        seen = set()
        for item in collection:
            if not isinstance(item, dict) or set(item) != {"text", "key", "cefr_level", "kind", "source"}:
                raise ValueError("Starter entry has invalid fields.")
            word, key = normalize_word(item["text"])
            if word != item["text"] or key != item["key"] or key in seen \
                    or item["cefr_level"] not in LEVELS or item["kind"] not in KINDS \
                    or item["source"] not in SOURCES:
                raise ValueError("Starter collection has an invalid or duplicate entry.")
            seen.add(key)
            validated.append(item)
        existing = {row[0] for row in self.conn.execute("SELECT normalized_key FROM entries")}
        added = skipped = 0
        timestamp = now()
        with self.conn:
            for item in validated:
                if item["key"] in existing:
                    skipped += 1
                    continue
                cursor = self.conn.execute(
                    "INSERT INTO entries(original_text, normalized_key, created_at, modified_at) "
                    "VALUES (?, ?, ?, ?)", (item["text"], item["key"], timestamp, timestamp),
                )
                self.conn.execute(
                    "INSERT INTO starter_catalog(entry_id, cefr_level, kind, source) VALUES (?, ?, ?, ?)",
                    (cursor.lastrowid, item["cefr_level"], item["kind"], item["source"]),
                )
                existing.add(item["key"])
                added += 1
        return {"added": added, "skipped": skipped}

    def counts(self):
        result = dict.fromkeys(DIFFICULTIES, 0)
        for row in self.conn.execute("SELECT difficulty, COUNT(*) AS n FROM entries GROUP BY difficulty"):
            result[row["difficulty"]] = row["n"]
        return result

    def update(self, entry_id, *, original_text, context, notes, difficulty,
               definition_it, gloss_en, example_it, difficulty_reason, expected_revision=None):
        old = self.get(entry_id)
        if old is None:
            raise ValueError("The selected entry was deleted.")
        if expected_revision is not None and old["revision"] != expected_revision:
            raise ValueError("This entry changed since it was opened. Copy your edits, select it again to reload, "
                             "then apply your changes to the latest version.")
        word, key = normalize_word(original_text)
        values = {"context": context, "notes": notes, "definition_it": definition_it,
                  "gloss_en": gloss_en, "example_it": example_it,
                  "difficulty_reason": difficulty_reason}
        values = {name: checked_text(value, name) for name, value in values.items()}
        if difficulty not in DIFFICULTIES:
            raise ValueError("Choose a valid difficulty.")
        changed_meaning = word != old["original_text"] or values["context"] != old["context"]
        if changed_meaning:
            difficulty = "unknown"
            for field in ("definition_it", "gloss_en", "example_it", "difficulty_reason"):
                values[field] = ""
        review_note = None if changed_meaning or difficulty != "unknown" else old["review_note"]
        changed_learning_content = any(
            (values[field] or None) != old[field]
            for field in ("definition_it", "gloss_en", "example_it", "difficulty_reason")
        )
        keep_ai = not changed_meaning and difficulty == old["difficulty"] and not changed_learning_content
        classified_at = old["classified_at"] if keep_ai else None
        classification_model = old["classification_model"] if classified_at else None
        try:
            with self.conn:
                self.conn.execute("""
                    UPDATE entries SET original_text=?, normalized_key=?, context=?, notes=?, difficulty=?,
                        definition_it=?, gloss_en=?, example_it=?, difficulty_reason=?, review_note=?,
                        classified_at=?, classification_model=?, modified_at=?, revision=revision+1
                    WHERE id=?
                """, (word, key, values["context"], values["notes"], difficulty,
                      values["definition_it"] or None, values["gloss_en"] or None,
                      values["example_it"] or None, values["difficulty_reason"] or None,
                      review_note, classified_at, classification_model, now(), entry_id))
                if not keep_ai:
                    self.conn.execute("DELETE FROM reclassification_pending WHERE entry_id=?", (entry_id,))
        except sqlite3.IntegrityError as exc:
            raise ValueError("Another entry already has that word or expression.") from exc
        return self.get(entry_id)

    def delete(self, entry_id):
        with self.conn:
            return self.conn.execute("DELETE FROM entries WHERE id=?", (entry_id,)).rowcount

    def unknown_snapshot(self):
        rows = self.conn.execute(
            "SELECT id, revision, original_text, context FROM entries WHERE difficulty='unknown' ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def pending_snapshot(self, *, recheck_only=False):
        condition = "p.entry_id IS NOT NULL" if recheck_only else \
            "(e.difficulty='unknown' AND e.review_note IS NULL) OR p.entry_id IS NOT NULL"
        rows = self.conn.execute(
            "SELECT e.id, e.revision, e.original_text, e.context FROM entries e "
            "LEFT JOIN reclassification_pending p ON p.entry_id=e.id WHERE " + condition + " ORDER BY e.id"
        ).fetchall()
        return [dict(row) for row in rows]

    def review_snapshot(self):
        rows = self.conn.execute(
            "SELECT id, revision, original_text, context FROM entries "
            "WHERE difficulty='unknown' AND review_note IS NOT NULL ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def review_count(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM entries WHERE difficulty='unknown' AND review_note IS NOT NULL"
        ).fetchone()[0]

    def pending_recheck_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM reclassification_pending").fetchone()[0]

    def pending_recheck_ids(self):
        return {row[0] for row in self.conn.execute("SELECT entry_id FROM reclassification_pending")}

    def create_request(self, entries, level, model):
        if not entries:
            raise ValueError("No entries to evaluate.")
        if level not in ("A1", "A2", "B1", "B2", "C1", "C2"):
            raise ValueError("Choose a level from A1 to C2.")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Enter a model name.")
        request_id = str(uuid4())
        snapshot = [dict(item) for item in entries]
        with self.conn:
            self.conn.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                (request_id, now(), level, model.strip(), json.dumps(snapshot, ensure_ascii=False)),
            )
        return {"schema_version": 1, "request_id": request_id, "level": level,
                "model": model.strip(), "entries": snapshot}

    def get_request(self, request_id):
        row = self.conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            return None
        return {"schema_version": 1, "request_id": request_id, "level": row["level"],
                "model": row["model"], "entries": json.loads(row["snapshot_json"])}

    def apply_results(self, response, request):
        from classifier import validate_response

        results = validate_response(response, request)
        applied = skipped = needs_review = 0
        with self.conn:
            if request["level"] != self.get_setting("level", "B1"):
                return {"applied": 0, "needs_review": 0, "skipped_stale": len(results)}
            for result in results:
                row = self.conn.execute(
                    "SELECT e.revision, e.original_text, e.difficulty, e.classification_model, "
                    "p.entry_id AS pending_id FROM entries e LEFT JOIN reclassification_pending p "
                    "ON p.entry_id=e.id WHERE e.id=?", (result["id"],)
                ).fetchone()
                if row is None or row["revision"] != result["revision"] or \
                        row["original_text"] != result["original_text"] or \
                        (row["difficulty"] != "unknown" and row["pending_id"] is None):
                    skipped += 1
                    continue
                if result["status"] == "needs_review":
                    self.conn.execute(
                        "UPDATE entries SET difficulty='unknown', definition_it=NULL, gloss_en=NULL, "
                        "example_it=NULL, difficulty_reason=NULL, review_note=?, classified_at=?, "
                        "classification_model=?, modified_at=?, revision=revision+1 WHERE id=?",
                        (result["review_note"], now(), request["model"], now(), result["id"]),
                    )
                    needs_review += 1
                else:
                    self.conn.execute("""
                        UPDATE entries SET difficulty=?, definition_it=?, gloss_en=?, example_it=?,
                            difficulty_reason=?, review_note=NULL, classified_at=?, classification_model=?,
                            modified_at=?, revision=revision+1 WHERE id=?
                    """, (result["difficulty"], result["definition_it"], result["gloss_en"],
                          result["example_it"], result["difficulty_reason"], now(), request["model"],
                          now(), result["id"]))
                    applied += 1
                self.conn.execute("DELETE FROM reclassification_pending WHERE entry_id=?", (result["id"],))
        return {"applied": applied, "needs_review": needs_review, "skipped_stale": skipped}

    def study_pool(self, difficulties):
        chosen = tuple(x for x in difficulties if x in DIFFICULTIES[1:])
        if not chosen:
            return [], 0
        rows = self.list_entries(difficulties=chosen)
        ready = [row for row in rows if all((row[field] or "").strip()
                 for field in ("definition_it", "gloss_en", "example_it"))]
        return ready, len(rows) - len(ready)

    def recent_first_round_outcomes(self, entry_ids, limit=5):
        ids = list(dict.fromkeys(entry_ids))
        if not ids:
            return {}
        outcomes = {}
        for offset in range(0, len(ids), 500):
            chunk = ids[offset:offset + 500]
            rows = self.conn.execute(
                "SELECT entry_id, remembered FROM study_reviews WHERE round_number=1 AND entry_id IN (" +
                ",".join("?" for _ in chunk) + ") ORDER BY reviewed_at DESC, rowid DESC", chunk
            )
            for row in rows:
                recent = outcomes.setdefault(row["entry_id"], [])
                if len(recent) < limit:
                    recent.append(row["remembered"])
        return outcomes

    def record_study(self, entry_id, remembered, *, session_id=None, round_number=1):
        if type(remembered) is not bool or type(round_number) is not int or round_number < 1:
            raise ValueError("Study result or round number is invalid.")
        session_id = str(uuid4()) if session_id is None else session_id
        if not isinstance(session_id, str) or not session_id or len(session_id) > 64:
            raise ValueError("Study session ID is invalid.")
        with self.conn:
            updated = self.conn.execute(
                "UPDATE entries SET study_attempts=study_attempts+1, remembered_count=remembered_count+?, "
                "again_count=again_count+? WHERE id=?",
                (int(remembered), int(not remembered), entry_id),
            )
            if updated.rowcount != 1:
                raise ValueError("This word was deleted before its study result could be saved.")
            self.conn.execute(
                "INSERT INTO study_reviews(review_id, entry_id, session_id, round_number, remembered, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), entry_id, session_id, round_number, int(remembered), now()),
            )

    def get_setting(self, key, default):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_settings(self, values):
        with self.conn:
            self.conn.executemany("INSERT INTO settings(key, value) VALUES (?, ?) "
                                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", values.items())

    def save_evaluation_settings(self, values):
        level = values["level"]
        if level not in ("A1", "A2", "B1", "B2", "C1", "C2"):
            raise ValueError("Choose a level from A1 to C2.")
        with self.conn:
            changed = self.get_setting("level", "B1") != level
            if changed:
                self.conn.execute(
                    "INSERT OR IGNORE INTO reclassification_pending(entry_id) "
                    "SELECT id FROM entries WHERE classification_model IS NOT NULL"
                )
            self.conn.executemany("INSERT INTO settings(key, value) VALUES (?, ?) "
                                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", values.items())
        return changed, self.pending_recheck_count()

    def export_backup(self, path):
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM entries ORDER BY id")]
        requests = []
        for row in self.conn.execute("SELECT * FROM requests ORDER BY created_at, request_id"):
            requests.append({"request_id": row["request_id"], "created_at": row["created_at"],
                             "level": row["level"], "model": row["model"],
                             "entries": json.loads(row["snapshot_json"])})
        pending = [row[0] for row in self.conn.execute(
            "SELECT entry_id FROM reclassification_pending ORDER BY entry_id")]
        settings = {row["key"]: row["value"] for row in self.conn.execute("SELECT key, value FROM settings")}
        reviews = [dict(row) for row in self.conn.execute("SELECT * FROM study_reviews ORDER BY rowid")]
        starter = [dict(row) for row in self.conn.execute("SELECT * FROM starter_catalog ORDER BY entry_id")]
        payload = {"format_version": 4, "exported_at": now(), "entries": rows,
                   "requests": requests, "pending_reclassification": pending, "settings": settings,
                   "study_reviews": reviews, "starter_catalog": starter}
        target = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                             prefix=".italian-vocabulary-", delete=False) as file:
                temporary = Path(file.name)
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return len(rows)

    def import_backup(self, path):
        from classifier import load_json_strict

        payload = load_json_strict(Path(path).read_text(encoding="utf-8"))
        fields_by_version = {
            1: {"format_version", "exported_at", "entries", "requests"},
            2: {"format_version", "exported_at", "entries", "requests", "pending_reclassification", "settings"},
            3: {"format_version", "exported_at", "entries", "requests", "pending_reclassification", "settings",
                "study_reviews"},
            4: {"format_version", "exported_at", "entries", "requests", "pending_reclassification", "settings",
                "study_reviews", "starter_catalog"},
        }
        if not isinstance(payload, dict) or type(payload.get("format_version")) is not int \
                or set(payload) != fields_by_version.get(payload["format_version"]) \
                or not isinstance(payload["exported_at"], str) or not isinstance(payload["entries"], list) \
                or not isinstance(payload["requests"], list):
            raise ValueError("Unsupported or invalid backup format.")
        pending = payload.get("pending_reclassification", [])
        if not isinstance(pending, list):
            raise ValueError("Backup re-evaluation queue is invalid.")
        reviews = payload.get("study_reviews", [])
        if not isinstance(reviews, list):
            raise ValueError("Backup study reviews are invalid.")
        starter = payload.get("starter_catalog", [])
        if not isinstance(starter, list):
            raise ValueError("Backup starter metadata is invalid.")
        settings = payload.get("settings", {})
        if not isinstance(settings, dict) or set(settings) - {"model", "effort", "level", "timeout"} \
                or any(not isinstance(value, str) for value in settings.values()) \
                or ("model" in settings and (not settings["model"] or len(settings["model"]) > 100
                    or any(ch.isspace() for ch in settings["model"]))) \
                or ("effort" in settings and settings["effort"] not in
                    ("low", "medium", "high", "xhigh", "max")) \
                or ("level" in settings and settings["level"] not in
                    ("A1", "A2", "B1", "B2", "C1", "C2")) \
                or ("timeout" in settings and
                    (not settings["timeout"].isdigit() or not 30 <= int(settings["timeout"]) <= 3600)):
            raise ValueError("Backup settings are invalid.")
        seen = set()
        seen_ids = set()
        for item in payload["entries"]:
            self._validate_backup_entry(item)
            if item["normalized_key"] in seen or item["id"] in seen_ids:
                raise ValueError("Backup contains duplicate words or IDs.")
            seen.add(item["normalized_key"])
            seen_ids.add(item["id"])
        seen_requests = set()
        for request in payload["requests"]:
            self._validate_backup_request(request)
            if request["request_id"] in seen_requests:
                raise ValueError("Backup contains duplicate evaluation requests.")
            seen_requests.add(request["request_id"])
        if any(type(entry_id) is not int or entry_id not in seen_ids for entry_id in pending) \
                or len(pending) != len(set(pending)):
            raise ValueError("Backup re-evaluation queue is invalid.")
        evaluated_ids = {item["id"] for item in payload["entries"]
                         if item["classification_model"] is not None}
        if any(entry_id not in evaluated_ids for entry_id in pending):
            raise ValueError("Backup re-evaluation queue contains an entry without an AI result.")
        reviews_seen = set()
        review_counts = {}
        for review in reviews:
            self._validate_backup_review(review)
            if review["review_id"] in reviews_seen or review["entry_id"] not in seen_ids:
                raise ValueError("Backup study reviews contain a duplicate ID or unknown word.")
            reviews_seen.add(review["review_id"])
            totals = review_counts.setdefault(review["entry_id"], [0, 0])
            totals[0] += 1
            totals[1] += review["remembered"]
        entries_by_id = {item["id"]: item for item in payload["entries"]}
        for entry_id, (attempts, remembered) in review_counts.items():
            entry = entries_by_id[entry_id]
            if attempts > entry["study_attempts"] or remembered > entry["remembered_count"] \
                    or attempts - remembered > entry["again_count"]:
                raise ValueError("Backup study reviews do not match saved study counts.")
        seen_starter_ids = set()
        for item in starter:
            if not isinstance(item, dict) or set(item) != {"entry_id", "cefr_level", "kind", "source"} \
                    or type(item["entry_id"]) is not int or item["entry_id"] not in seen_ids \
                    or item["entry_id"] in seen_starter_ids \
                    or item["cefr_level"] not in ("A1", "A2", "B1", "B2") \
                    or item["kind"] not in ("word", "phrase", "idiom", "proverb") \
                    or item["source"] not in ("community-cefr", "editorial"):
                raise ValueError("Backup starter metadata is invalid.")
            seen_starter_ids.add(item["entry_id"])
        added = skipped = conflicting = 0
        id_map = {}
        target_has_data = bool(self.conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
                               or self.conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone())
        target_level = self.get_setting("level", "B1") if target_has_data else settings.get("level", "B1")
        with self.conn:
            if not target_has_data:
                self.conn.executemany("INSERT INTO settings(key, value) VALUES (?, ?)", settings.items())
            for item in payload["entries"]:
                current = self.conn.execute("SELECT * FROM entries WHERE normalized_key=?",
                                            (item["normalized_key"],)).fetchone()
                if current:
                    comparable = [x for x in ENTRY_COLUMNS if x not in ("id", "created_at", "modified_at")]
                    if all(current[key] == item[key] for key in comparable):
                        skipped += 1
                        id_map[item["id"]] = current["id"]
                    else:
                        conflicting += 1
                    continue
                occupied = self.conn.execute("SELECT 1 FROM entries WHERE id=?", (item["id"],)).fetchone()
                columns = list(ENTRY_COLUMNS) if not occupied else [x for x in ENTRY_COLUMNS if x != "id"]
                self.conn.execute(
                    "INSERT INTO entries (" + ",".join(columns) + ") VALUES (" +
                    ",".join("?" for _ in columns) + ")", [item[x] for x in columns],
                )
                mapped = self.conn.execute("SELECT id FROM entries WHERE normalized_key=?",
                                           (item["normalized_key"],)).fetchone()
                id_map[item["id"]] = mapped["id"]
                added += 1
            for request in payload["requests"]:
                if self.get_request(request["request_id"]):
                    continue
                remapped = []
                for entry in request["entries"]:
                    mapped_id = id_map.get(entry["id"])
                    if mapped_id is None or mapped_id != entry["id"]:
                        break
                    copy = dict(entry)
                    copy["id"] = mapped_id
                    remapped.append(copy)
                else:
                    self.conn.execute(
                        "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                        (request["request_id"], request["created_at"], request["level"],
                         request["model"], json.dumps(remapped, ensure_ascii=False)),
                    )
            for entry_id in pending:
                mapped_id = id_map.get(entry_id)
                if mapped_id is not None:
                    self.conn.execute("INSERT OR IGNORE INTO reclassification_pending(entry_id) VALUES (?)",
                                      (mapped_id,))
            for item in starter:
                mapped_id = id_map.get(item["entry_id"])
                if mapped_id is not None:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO starter_catalog(entry_id, cefr_level, kind, source) "
                        "VALUES (?, ?, ?, ?)",
                        (mapped_id, item["cefr_level"], item["kind"], item["source"]),
                    )
            if settings.get("level", "B1") != target_level:
                for item in payload["entries"]:
                    mapped_id = id_map.get(item["id"])
                    if mapped_id is not None and item["classification_model"] is not None:
                        self.conn.execute("INSERT OR IGNORE INTO reclassification_pending(entry_id) VALUES (?)",
                                          (mapped_id,))
            for review in reviews:
                mapped_id = id_map.get(review["entry_id"])
                if mapped_id is None:
                    continue
                existing = self.conn.execute("SELECT * FROM study_reviews WHERE review_id=?",
                                             (review["review_id"],)).fetchone()
                if existing:
                    if any(existing[key] != (mapped_id if key == "entry_id" else review[key]) for key in review):
                        raise ValueError("A study review ID conflicts with existing history.")
                    continue
                self.conn.execute(
                    "INSERT INTO study_reviews(review_id, entry_id, session_id, round_number, remembered, "
                    "reviewed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (review["review_id"], mapped_id, review["session_id"], review["round_number"],
                     review["remembered"], review["reviewed_at"]),
                )
            for entry_id in set(id_map.values()):
                summary = self.conn.execute(
                    "SELECT COUNT(*) AS attempts, COALESCE(SUM(remembered), 0) AS remembered "
                    "FROM study_reviews WHERE entry_id=?", (entry_id,)
                ).fetchone()
                entry = self.get(entry_id)
                if summary["attempts"] > entry["study_attempts"] \
                        or summary["remembered"] > entry["remembered_count"] \
                        or summary["attempts"] - summary["remembered"] > entry["again_count"]:
                    raise ValueError("Merged study history conflicts with saved study counts.")
        return {"added": added, "skipped": skipped, "conflicting": conflicting}

    @staticmethod
    def _validate_backup_entry(item):
        if not isinstance(item, dict) or set(item) != set(ENTRY_COLUMNS):
            raise ValueError("Backup entry has missing or extra fields.")
        if type(item["id"]) is not int or item["id"] < 1 or type(item["revision"]) is not int or item["revision"] < 1:
            raise ValueError("Backup entry has invalid IDs or revision.")
        for key in ("study_attempts", "remembered_count", "again_count"):
            if type(item[key]) is not int or item[key] < 0:
                raise ValueError("Backup entry has invalid study counts.")
        if item["remembered_count"] + item["again_count"] != item["study_attempts"]:
            raise ValueError("Backup study counts do not match.")
        word, normalized = normalize_word(item["original_text"])
        if word != item["original_text"] or normalized != item["normalized_key"]:
            raise ValueError("Backup entry has an invalid normalized key.")
        if item["difficulty"] not in DIFFICULTIES:
            raise ValueError("Backup entry has invalid difficulty.")
        for key in TEXT_LIMITS:
            value = item[key]
            if key in ("context", "notes") and not isinstance(value, str):
                raise ValueError(f"Backup {key} must be text.")
            if value is not None and (not isinstance(value, str) or len(value) > TEXT_LIMITS[key]):
                raise ValueError(f"Backup {key} is invalid.")
        for key in ("created_at", "modified_at"):
            if not isinstance(item[key], str) or not item[key]:
                raise ValueError("Backup timestamps are invalid.")
        for key in ("classified_at", "classification_model"):
            if item[key] is not None and not isinstance(item[key], str):
                raise ValueError("Backup classification metadata is invalid.")

    @staticmethod
    def _validate_backup_review(item):
        required = {"review_id", "entry_id", "session_id", "round_number", "remembered", "reviewed_at"}
        if not isinstance(item, dict) or set(item) != required \
                or not isinstance(item["review_id"], str) or not 1 <= len(item["review_id"]) <= 64 \
                or type(item["entry_id"]) is not int or item["entry_id"] < 1 \
                or not isinstance(item["session_id"], str) or not 1 <= len(item["session_id"]) <= 64 \
                or type(item["round_number"]) is not int or item["round_number"] < 1 \
                or type(item["remembered"]) is not int or item["remembered"] not in (0, 1) \
                or not isinstance(item["reviewed_at"], str):
            raise ValueError("Backup study review is invalid.")
        try:
            datetime.fromisoformat(item["reviewed_at"])
        except ValueError as exc:
            raise ValueError("Backup study review timestamp is invalid.") from exc

    @staticmethod
    def _validate_backup_request(request):
        if not isinstance(request, dict) or set(request) != {"request_id", "created_at", "level", "model", "entries"}:
            raise ValueError("Backup evaluation request is invalid.")
        if not isinstance(request["request_id"], str) or len(request["request_id"]) != 36 \
                or not isinstance(request["created_at"], str) or not request["created_at"] \
                or request["level"] not in ("A1", "A2", "B1", "B2", "C1", "C2") \
                or not isinstance(request["model"], str) or not request["model"] \
                or not isinstance(request["entries"], list) or not request["entries"]:
            raise ValueError("Backup evaluation request has invalid metadata.")
        seen = set()
        for entry in request["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"id", "revision", "original_text", "context"} \
                    or type(entry["id"]) is not int or entry["id"] < 1 \
                    or type(entry["revision"]) is not int or entry["revision"] < 1 \
                    or not isinstance(entry["context"], str) or len(entry["context"]) > TEXT_LIMITS["context"]:
                raise ValueError("Backup evaluation snapshot is invalid.")
            word, _ = normalize_word(entry["original_text"])
            if word != entry["original_text"] or entry["id"] in seen:
                raise ValueError("Backup evaluation snapshot has duplicate or invalid entries.")
            seen.add(entry["id"])
