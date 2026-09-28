"""Validated, offline A1-B2 starter collection bundled with the app."""

from collections import Counter
from pathlib import Path

from storage import normalize_word

LEVELS = ("A1", "A2", "B1", "B2")
KINDS = ("word", "phrase", "idiom", "proverb")
SOURCES = ("community-cefr", "editorial")
DATA_DIR = Path(__file__).resolve().parent


def load_starter_catalog(data_dir=DATA_DIR):
    """Read both TSV files and reject any malformed or duplicate entry."""
    collection = []
    seen = set()
    files = (("starter_words.tsv", "word", "community-cefr"),
             ("starter_expressions.tsv", None, "editorial"))
    for filename, fixed_kind, source in files:
        path = Path(data_dir) / filename
        for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw or raw.startswith("#"):
                continue
            fields = raw.split("\t")
            if len(fields) != (2 if fixed_kind else 3):
                raise ValueError(f"{filename}:{line_no}: invalid column count.")
            word, key = normalize_word(fields[0])
            level = fields[1]
            kind = fixed_kind or fields[2]
            if word != fields[0] or level not in LEVELS or kind not in KINDS:
                raise ValueError(f"{filename}:{line_no}: invalid word, level, or type.")
            if key in seen:
                raise ValueError(f"{filename}:{line_no}: duplicate entry {word!r}.")
            seen.add(key)
            collection.append({"text": word, "key": key, "cefr_level": level,
                               "kind": kind, "source": source})
    if not all(any(item["cefr_level"] == level for item in collection) for level in LEVELS):
        raise ValueError("Starter collection must contain entries at every A1-B2 level.")
    return collection


def starter_counts(collection):
    return dict(Counter(item["cefr_level"] for item in collection))
