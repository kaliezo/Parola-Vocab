import json
import os
import queue
import random
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import VocabularyApp
from classifier import (ClassificationCancelled, ClassifierError, _stop_process,
                        explain_failure, load_json_strict,
                        run_codex, validate_response)
from profiles import ProfileStore
from storage import STUDY_LEVELS, VocabularyDB
from starter import load_starter_catalog, starter_counts
from study import (StudySession, choose_cards, parse_session_size, recall_estimate,
                   recall_label, selection_weight)
from topics import ALL_TOPICS, TOPICS, topics_for_text


def result_for(entry, *, status="ok", difficulty="easy"):
    return {
        "id": entry["id"], "revision": entry["revision"],
        "original_text": entry["original_text"], "status": status,
        "difficulty": difficulty if status == "ok" else None,
        "definition_it": "Una definizione breve." if status == "ok" else None,
        "gloss_en": "a gloss" if status == "ok" else None,
        "example_it": "Questo e un esempio." if status == "ok" else None,
        "difficulty_reason": "Uso frequente." if status == "ok" else None,
        "review_note": None if status == "ok" else "Add a full context sentence.",
    }


def response_for(request, *, difficulty="easy"):
    return {"schema_version": 1, "request_id": request["request_id"],
            "results": [result_for(entry, difficulty=difficulty) for entry in request["entries"]]}


class ProfileTests(unittest.TestCase):
    def test_default_and_empty_profiles_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(directory)
            default = store.create("Default Tester", "default")
            default_db = VocabularyDB(store.database_path(default["id"]))
            try:
                ready, incomplete = default_db.study_pool(("easy", "medium", "hard"))
                self.assertEqual((len(ready), incomplete), (7695, 0))
                for level, expected in (("A1", 542), ("A2", 1038),
                                        ("B1", 2058), ("B2", 4057)):
                    selected, missing = default_db.study_pool(("easy", "medium", "hard"),
                                                               level=level)
                    self.assertEqual((len(selected), missing), (expected, 0))
                    self.assertTrue(all(row["starter_level"] == level for row in selected))
                self.assertEqual(default_db.get_setting("level", ""), "A2")
                self.assertEqual(default_db.get_setting("effort", ""), "xhigh")
                self.assertEqual([row["original_text"] for row in ready
                                  if len(row["original_text"]) == 1], ["O"])
                self.assertEqual(default_db.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 0)
                self.assertEqual(default_db.conn.execute("SELECT COUNT(*) FROM study_reviews").fetchone()[0], 0)
            finally:
                default_db.close()
            empty = store.create("Local Test", "empty")
            empty_db = VocabularyDB(store.database_path(empty["id"]))
            try:
                self.assertEqual(empty_db.counts()["unknown"], 0)
                self.assertEqual(empty_db.study_pool(("easy", "medium", "hard"))[0], [])
                empty_db.add("casa")
            finally:
                empty_db.close()
            default_db = VocabularyDB(store.database_path(default["id"]))
            try:
                self.assertEqual(default_db.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 7695)
            finally:
                default_db.close()
            self.assertEqual(store.active_profile()["name"], "Local Test")
            store.activate(default["id"])
            self.assertEqual(ProfileStore(directory).active_profile()["id"], default["id"])
            with self.assertRaises(ValueError):
                store.create("local test", "empty")

    def test_legacy_database_is_copied_without_removing_original(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "vocabulary.sqlite3"
            old_db = VocabularyDB(legacy)
            word_id, _ = old_db.add("casa")
            old_db.record_study(word_id, True)
            old_db.close()
            store = ProfileStore(directory)
            migrated = store.migrate_legacy_if_needed()
            self.assertEqual(migrated["name"], "Existing Library")
            self.assertTrue(legacy.is_file())
            new_db = VocabularyDB(store.database_path(migrated["id"]))
            try:
                self.assertEqual(new_db.get(word_id)["original_text"], "casa")
                self.assertEqual(new_db.get(word_id)["study_attempts"], 1)
            finally:
                new_db.close()
            self.assertIsNone(store.migrate_legacy_if_needed())
            self.assertEqual(len(store.list_profiles()), 1)


class TopicTests(unittest.TestCase):
    def test_ambiguous_meanings_and_expressions(self):
        cases = (
            ("arancia", "orange", "Food and drink", "Colors and shapes"),
            ("arancione", "orange", "Colors and shapes", "Food and drink"),
            ("mouse", "computer mouse", "Technology and media", "Animals"),
            ("treno", "train", "Travel and transport", "Sports and leisure"),
        )
        for word, gloss, expected, excluded in cases:
            with self.subTest(word=word):
                assigned = topics_for_text(word, gloss, "")
                self.assertIn(expected, assigned)
                self.assertNotIn(excluded, assigned)
        self.assertEqual(topics_for_text("a casa", "at home", "", "phrase"),
                         ("Expressions",))

    def test_every_bundled_card_has_a_known_topic(self):
        path = Path(__file__).resolve().parents[1] / "default_cards.jsonl"
        cards = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(cards), 7695)
        found = set()
        for card in cards:
            assigned = topics_for_text(card["word"], card.get("gloss_en"),
                                       card.get("definition_it"), card.get("kind", "word"))
            self.assertTrue(assigned, card["word"])
            self.assertTrue(set(assigned) <= set(TOPICS), card["word"])
            found.update(assigned)
        self.assertEqual(found, set(TOPICS))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.sqlite3"
        self.db = VocabularyDB(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_add_persistence_duplicates_and_accents(self):
        word_id, created = self.db.add("  A   MALAPENA  ")
        self.assertTrue(created)
        self.assertEqual(self.db.get(word_id)["difficulty"], "unknown")
        self.assertEqual(self.db.add("a malapena"), (word_id, False))
        si_id, _ = self.db.add("si")
        accented_id, _ = self.db.add("sì")
        self.assertNotEqual(si_id, accented_id)
        composed_id, _ = self.db.add("perché")
        self.assertEqual(self.db.add("perche\u0301"), (composed_id, False))
        with self.assertRaises(ValueError):
            self.db.add("   ")
        with self.assertRaises(ValueError):
            self.db.add("a" * 121)
        self.db.close()
        self.db = VocabularyDB(self.path)
        self.assertEqual(self.db.get(word_id)["original_text"], "A MALAPENA")
        self.assertEqual(self.db.counts()["unknown"], 4)

    def test_classification_review_counts_and_study_selections(self):
        for word in ("casa", "cavarsela", "a malapena", "perché"):
            self.db.add(word)
        request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        response = response_for(request)
        response["results"][1]["difficulty"] = "medium"
        response["results"][2]["difficulty"] = "hard"
        response["results"][3] = result_for(request["entries"][3], status="needs_review")
        counts = self.db.apply_results(response, request)
        self.assertEqual(counts, {"applied": 3, "needs_review": 1, "skipped_stale": 0})
        self.assertEqual(self.db.counts(), {"unknown": 1, "easy": 1, "medium": 1, "hard": 1})
        self.assertIn("context", self.db.get(request["entries"][3]["id"])["review_note"])
        for mask in range(1, 8):
            selected = [name for bit, name in enumerate(("easy", "medium", "hard")) if mask & (1 << bit)]
            ready, incomplete = self.db.study_pool(selected)
            self.assertEqual(len(ready), len(selected))
            self.assertEqual(incomplete, 0)
        self.assertEqual(self.db.study_pool([]), ([], 0))
        self.assertEqual(self.db.study_pool(["unknown"]), ([], 0))
        session = StudySession(self.db.study_pool(["easy", "medium", "hard"])[0], "all")
        self.assertEqual(len({card["id"] for card in session.cards}), 3)
        while session.current:
            session.reveal()
            session.reveal()
            entry_id = session.answer(False)
            self.db.record_study(entry_id, False)
        self.assertEqual(self.db.counts(), {"unknown": 1, "easy": 1, "medium": 1, "hard": 1})
        self.assertTrue(session.next_round())
        self.assertEqual(len(session.cards), 3)

    def test_incomplete_and_unknown_excluded(self):
        ready_id, _ = self.db.add("casa")
        incomplete_id, _ = self.db.add("pane")
        request = self.db.create_request([self.db.unknown_snapshot()[0]], "B1", "gpt-6-sol")
        self.db.apply_results(response_for(request), request)
        row = self.db.get(incomplete_id)
        self.db.update(incomplete_id, original_text=row["original_text"], context="", notes="",
                       difficulty="easy", definition_it="", gloss_en="", example_it="",
                       difficulty_reason="")
        pool, incomplete = self.db.study_pool(["easy"])
        self.assertEqual([entry["id"] for entry in pool], [ready_id])
        self.assertEqual(incomplete, 1)

    def test_study_pool_filters_topic_and_difficulty(self):
        self.db.import_starter([
            {"text": "rosso", "key": "rosso", "cefr_level": "A1", "kind": "word",
             "source": "editorial"},
            {"text": "banana", "key": "banana", "cefr_level": "B2", "kind": "word",
             "source": "editorial"},
        ])
        for word, gloss, difficulty in (("rosso", "red", "easy"),
                                        ("banana", "banana", "medium"),
                                        ("arancione", "orange", "hard")):
            entry_id, _ = self.db.add(word)
            self.db.update(entry_id, original_text=word, context="", notes="",
                           difficulty=difficulty, definition_it="Una definizione.",
                           gloss_en=gloss, example_it="Un esempio.", difficulty_reason="")
        colors, incomplete = self.db.study_pool(("easy", "medium", "hard"),
                                                 topic="Colors and shapes")
        self.assertEqual([row["original_text"] for row in colors], ["arancione", "rosso"])
        self.assertEqual(incomplete, 0)
        food, _ = self.db.study_pool(("easy", "medium", "hard"), topic="Food and drink")
        self.assertEqual([row["original_text"] for row in food], ["banana"])
        self.assertEqual(len(self.db.study_pool(("easy", "medium", "hard"), ALL_TOPICS)[0]), 3)
        self.assertEqual([row["original_text"] for row in
                          self.db.study_pool(("easy",), "Colors and shapes")[0]], ["rosso"])
        self.assertEqual([row["original_text"] for row in
                          self.db.study_pool(("easy", "medium", "hard"),
                                             topic="Colors and shapes", level="A1")[0]], ["rosso"])
        self.assertEqual([row["original_text"] for row in
                          self.db.study_pool(("easy", "medium", "hard"),
                                             topic="Food and drink", level="B2")[0]], ["banana"])
        self.assertEqual([row["original_text"] for row in
                          self.db.study_pool(("easy", "medium", "hard"),
                                             topic="Colors and shapes", level="Personal")[0]], ["arancione"])
        self.assertEqual(self.db.study_pool(("easy", "medium", "hard"), level="A2"), ([], 0))
        with self.assertRaises(ValueError):
            self.db.study_pool(("easy",), topic="Not a topic")
        with self.assertRaises(ValueError):
            self.db.study_pool(("easy",), level="C1")

    def test_study_pool_counts_match_card_selection(self):
        self.db.import_starter([
            {"text": "rosso", "key": "rosso", "cefr_level": "A1", "kind": "word",
             "source": "editorial"},
        ])
        for word, difficulty, ready in (("rosso", "easy", True),
                                        ("blu", "medium", False),
                                        ("verde", "hard", True)):
            entry_id, _ = self.db.add(word)
            self.db.update(entry_id, original_text=word, context="", notes="",
                           difficulty=difficulty, definition_it="Una definizione." if ready else "",
                           gloss_en="a color", example_it="Un esempio.", difficulty_reason="")
        for difficulties in ((), ("easy",), ("easy", "medium", "hard")):
            for level in STUDY_LEVELS:
                for topic in (ALL_TOPICS, "Colors and shapes"):
                    with self.subTest(difficulties=difficulties, level=level, topic=topic):
                        ready, incomplete = self.db.study_pool(difficulties, topic, level)
                        self.assertEqual(self.db.study_pool_counts(difficulties, topic, level),
                                         (len(ready), incomplete))
                        compact, compact_incomplete = self.db.study_pool(
                            difficulties, topic, level, compact=True)
                        self.assertEqual([card["id"] for card in compact],
                                         [card["id"] for card in ready])
                        self.assertEqual(compact_incomplete, incomplete)
                        for card in compact:
                            self.assertEqual(card["gloss_en"], "a color")

    def test_library_view_keeps_filters_and_ready_status_without_full_content(self):
        ready_id, _ = self.db.add("casa")
        self.db.update(ready_id, original_text="casa", context="", notes="",
                       difficulty="easy", definition_it="Una casa.", gloss_en="house",
                       example_it="La mia casa.", difficulty_reason="")
        other_id, _ = self.db.add("pane")
        self.db.update(other_id, original_text="pane", context="", notes="bakery note",
                       difficulty="unknown", definition_it="", gloss_en="", example_it="",
                       difficulty_reason="")
        full = self.db.list_entries()
        compact = self.db.list_entries(library_view=True)
        self.assertEqual([row["id"] for row in compact], [row["id"] for row in full])
        self.assertEqual({row["id"]: row["content_ready"] for row in compact},
                         {ready_id: 1, other_id: 0})
        self.assertNotIn("definition_it", compact[0])
        self.assertEqual([row["id"] for row in
                          self.db.list_entries(search="bakery", library_view=True)], [other_id])
        self.assertEqual([row["id"] for row in
                          self.db.list_entries(difficulties=["easy"], library_view=True)], [ready_id])

    def test_reviewed_entry_is_skipped_until_context_changes(self):
        reviewed_id, _ = self.db.add("piano")
        ready_id, _ = self.db.add("casa")
        request = self.db.create_request(self.db.pending_snapshot(), "B1", "gpt-6-sol")
        response = response_for(request)
        response["results"][0] = result_for(request["entries"][0], status="needs_review")
        self.db.apply_results(response, request)
        self.assertEqual(self.db.review_count(), 1)
        self.assertEqual(self.db.pending_snapshot(), [])
        self.assertEqual([row["id"] for row in self.db.review_snapshot()], [reviewed_id])
        self.assertEqual([row["id"] for row in self.db.list_entries(review_only=True)], [reviewed_id])
        self.assertEqual(self.db.get(ready_id)["difficulty"], "easy")

        self.db.update(reviewed_id, original_text="piano", context="", notes="my note",
                       difficulty="unknown", definition_it="", gloss_en="", example_it="",
                       difficulty_reason="")
        self.assertEqual(self.db.pending_snapshot(), [])
        self.db.update(reviewed_id, original_text="piano", context="Vado al primo piano.",
                       notes="my note", difficulty="unknown", definition_it="", gloss_en="",
                       example_it="", difficulty_reason="")
        self.assertEqual(self.db.review_count(), 0)
        self.assertEqual([row["id"] for row in self.db.pending_snapshot()], [reviewed_id])

    def test_invalid_batch_is_atomic_and_late_results_skip(self):
        first, _ = self.db.add("casa")
        second, _ = self.db.add("pane")
        request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        valid = response_for(request)
        for invalid in (
            {**valid, "results": valid["results"][:1]},
            {**valid, "results": [valid["results"][0], valid["results"][0]]},
            {**valid, "results": [{**valid["results"][0], "difficulty": None}, valid["results"][1]]},
        ):
            with self.assertRaises(ValueError):
                self.db.apply_results(invalid, request)
            self.assertEqual(self.db.counts()["unknown"], 2)
        with self.assertRaises(ValueError):
            load_json_strict("{broken")
        self.db.update(first, original_text="casa nuova", context="", notes="personal",
                       difficulty="unknown", definition_it="", gloss_en="", example_it="",
                       difficulty_reason="")
        self.db.delete(second)
        applied = self.db.apply_results(valid, request)
        self.assertEqual(applied, {"applied": 0, "needs_review": 0, "skipped_stale": 2})
        self.assertEqual(self.db.get(first)["notes"], "personal")
        self.assertIsNone(self.db.get(second))

    def test_context_edit_resets_ai_but_notes_and_history_survive(self):
        entry_id, _ = self.db.add("casa")
        request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        self.db.apply_results(response_for(request), request)
        self.db.record_study(entry_id, True)
        edited = self.db.update(entry_id, original_text="casa", context="La casa e bella.",
                                notes="I saw this in a story.", difficulty="easy",
                                definition_it="A house", gloss_en="house",
                                example_it="La casa e bella.", difficulty_reason="Common")
        self.assertEqual(edited["difficulty"], "unknown")
        self.assertIsNone(edited["definition_it"])
        self.assertEqual(edited["remembered_count"], 1)
        self.assertEqual(edited["notes"], "I saw this in a story.")
        with self.assertRaisesRegex(ValueError, "changed since it was opened"):
            self.db.update(entry_id, original_text="casa", context="", notes="stale",
                           difficulty="unknown", definition_it="", gloss_en="", example_it="",
                           difficulty_reason="", expected_revision=1)

    def test_level_change_rechecks_ai_results_and_preserves_content_until_success(self):
        casa, _ = self.db.add("casa")
        unclear, _ = self.db.add("frammento")
        manual, _ = self.db.add("pane")
        initial = self.db.create_request(self.db.unknown_snapshot()[:2], "B1", "gpt-6-sol")
        response = response_for(initial)
        response["results"][1] = result_for(initial["entries"][1], status="needs_review")
        self.db.apply_results(response, initial)
        row = self.db.get(manual)
        self.db.update(manual, original_text=row["original_text"], context="", notes="",
                       difficulty="easy", definition_it="Definizione.", gloss_en="bread",
                       example_it="Mangio il pane.", difficulty_reason="Common.")
        self.db.record_study(casa, True)
        self.assertEqual(self.db.save_evaluation_settings({"level": "B2"}), (True, 2))
        self.assertEqual(self.db.pending_recheck_ids(), {casa, unclear})
        self.assertEqual(self.db.get(casa)["difficulty"], "easy")
        self.assertEqual(self.db.get(casa)["definition_it"], "Una definizione breve.")
        self.assertEqual(self.db.pending_snapshot(recheck_only=True)[0]["id"], casa)
        self.db.close()
        self.db = VocabularyDB(self.path)
        self.assertEqual(self.db.pending_recheck_count(), 2)
        recheck = self.db.create_request(self.db.pending_snapshot(recheck_only=True), "B2", "gpt-6-sol")
        revised = response_for(recheck, difficulty="hard")
        revised["results"][1] = result_for(recheck["entries"][1], status="needs_review")
        self.assertEqual(self.db.apply_results(revised, recheck),
                         {"applied": 1, "needs_review": 1, "skipped_stale": 0})
        self.assertEqual(self.db.get(casa)["difficulty"], "hard")
        self.assertEqual(self.db.get(casa)["remembered_count"], 1)
        self.assertEqual(self.db.get(unclear)["difficulty"], "unknown")
        self.assertIsNone(self.db.get(unclear)["definition_it"])
        self.assertEqual(self.db.get(manual)["difficulty"], "easy")
        self.assertEqual(self.db.pending_recheck_count(), 0)

    def test_old_level_and_manual_edits_cannot_overwrite_rechecks(self):
        first, _ = self.db.add("casa")
        second, _ = self.db.add("pane")
        original = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        self.db.apply_results(response_for(original), original)
        old_snapshot = [{key: self.db.get(entry_id)[key]
                         for key in ("id", "revision", "original_text", "context")}
                        for entry_id in (first, second)]
        old_level_request = self.db.create_request(old_snapshot, "B1", "gpt-6-sol")
        self.db.save_evaluation_settings({"level": "B2"})
        self.assertEqual(self.db.apply_results(response_for(old_level_request), old_level_request),
                         {"applied": 0, "needs_review": 0, "skipped_stale": 2})
        current = self.db.create_request(self.db.pending_snapshot(recheck_only=True), "B2", "gpt-6-sol")
        row = self.db.get(first)
        self.db.update(first, original_text="casa", context="", notes="my note",
                       difficulty="hard", definition_it=row["definition_it"],
                       gloss_en=row["gloss_en"], example_it=row["example_it"],
                       difficulty_reason=row["difficulty_reason"])
        self.db.delete(second)
        self.assertEqual(self.db.apply_results(response_for(current), current),
                         {"applied": 0, "needs_review": 0, "skipped_stale": 2})
        self.assertEqual(self.db.get(first)["difficulty"], "hard")
        self.assertEqual(self.db.get(first)["notes"], "my note")
        self.assertIsNone(self.db.get(second))
        self.assertEqual(self.db.pending_recheck_count(), 0)

    def test_recheck_backup_and_legacy_import(self):
        entry_id, _ = self.db.add("casa")
        original = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        self.db.apply_results(response_for(original), original)
        self.db.save_evaluation_settings({"level": "B2", "model": "gpt-6-sol"})
        backup = Path(self.temp.name) / "recheck.json"
        self.db.export_backup(backup)
        other = VocabularyDB(Path(self.temp.name) / "restored.sqlite3")
        try:
            self.assertEqual(other.import_backup(backup)["added"], 1)
            self.assertEqual(other.get_setting("level", "B1"), "B2")
            self.assertEqual(other.pending_recheck_ids(), {entry_id})
            payload = json.loads(backup.read_text(encoding="utf-8"))
            payload["pending_reclassification"] = [999]
            backup.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "queue"):
                other.import_backup(backup)
            self.assertEqual(other.pending_recheck_ids(), {entry_id})
            payload["format_version"] = 1
            payload.pop("pending_reclassification")
            payload.pop("settings")
            payload.pop("study_reviews")
            payload.pop("starter_catalog")
            backup.write_text(json.dumps(payload), encoding="utf-8")
            legacy = VocabularyDB(Path(self.temp.name) / "legacy.sqlite3")
            try:
                self.assertEqual(legacy.import_backup(backup)["added"], 1)
            finally:
                legacy.close()
        finally:
            other.close()
        self.db.export_backup(backup)
        merged = VocabularyDB(Path(self.temp.name) / "merged.sqlite3")
        try:
            merged.set_settings({"level": "C1"})
            self.assertEqual(merged.import_backup(backup)["added"], 1)
            self.assertEqual(merged.get_setting("level", "B1"), "C1")
            self.assertEqual(merged.pending_recheck_ids(), {entry_id})
        finally:
            merged.close()

    def test_notes_keep_recheck_but_manual_learning_edit_clears_it(self):
        entry_id, _ = self.db.add("casa")
        request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        self.db.apply_results(response_for(request), request)
        self.db.save_evaluation_settings({"level": "B2"})
        row = self.db.get(entry_id)
        self.db.update(entry_id, original_text="casa", context="", notes="Remember this one",
                       difficulty=row["difficulty"], definition_it=row["definition_it"],
                       gloss_en=row["gloss_en"], example_it=row["example_it"],
                       difficulty_reason=row["difficulty_reason"])
        self.assertEqual(self.db.pending_recheck_ids(), {entry_id})
        row = self.db.get(entry_id)
        self.db.update(entry_id, original_text="casa", context="", notes=row["notes"],
                       difficulty=row["difficulty"], definition_it="My own definition",
                       gloss_en=row["gloss_en"], example_it=row["example_it"],
                       difficulty_reason=row["difficulty_reason"])
        self.assertEqual(self.db.pending_recheck_count(), 0)
        self.assertIsNone(self.db.get(entry_id)["classification_model"])

    def test_backup_merge_and_validation(self):
        entry_id, _ = self.db.add("sì")
        self.db.record_study(entry_id, False)
        request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        backup = Path(self.temp.name) / "backup.json"
        self.assertEqual(self.db.export_backup(backup), 1)
        other = VocabularyDB(Path(self.temp.name) / "other.sqlite3")
        try:
            self.assertEqual(other.import_backup(backup), {"added": 1, "skipped": 0, "conflicting": 0})
            self.assertEqual(other.get(1)["again_count"], 1)
            self.assertEqual(other.get_request(request["request_id"])["entries"], request["entries"])
            self.assertEqual(other.import_backup(backup), {"added": 0, "skipped": 1, "conflicting": 0})
            other.update(1, original_text="sì", context="", notes="my note", difficulty="unknown",
                         definition_it="", gloss_en="", example_it="", difficulty_reason="")
            self.assertEqual(other.import_backup(backup), {"added": 0, "skipped": 0, "conflicting": 1})
            self.assertEqual(other.get(1)["notes"], "my note")
            payload = json.loads(backup.read_text(encoding="utf-8"))
            payload["entries"].append(payload["entries"][0])
            backup.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                other.import_backup(backup)
            self.assertEqual(other.counts()["unknown"], 1)
        finally:
            other.close()

    def test_review_history_is_atomic_persistent_and_uses_first_round(self):
        entry_id, _ = self.db.add("casa")
        self.db.record_study(entry_id, False, session_id="first", round_number=1)
        self.db.record_study(entry_id, True, session_id="first", round_number=2)
        self.db.record_study(entry_id, True, session_id="second", round_number=1)
        self.assertEqual(self.db.recent_first_round_outcomes([entry_id]), {entry_id: [1, 0]})
        self.assertEqual(self.db.get(entry_id)["study_attempts"], 3)
        self.assertEqual(self.db.get(entry_id)["remembered_count"], 2)
        self.db.close()
        self.db = VocabularyDB(self.path)
        self.assertEqual(self.db.recent_first_round_outcomes([entry_id]), {entry_id: [1, 0]})
        with self.assertRaisesRegex(ValueError, "deleted"):
            self.db.record_study(999, True)
        self.assertEqual(self.db.get(entry_id)["study_attempts"], 3)
        self.db.delete(entry_id)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM study_reviews").fetchone()[0], 0)

    def test_existing_database_gains_review_table_without_losing_counts(self):
        entry_id, _ = self.db.add("casa")
        self.db.record_study(entry_id, False)
        with self.db.conn:
            self.db.conn.execute("DROP TABLE study_reviews")
        self.db.close()
        self.db = VocabularyDB(self.path)
        self.assertEqual(self.db.get(entry_id)["again_count"], 1)
        self.assertEqual(self.db.recent_first_round_outcomes([entry_id]), {})
        self.db.record_study(entry_id, True)
        self.assertEqual(self.db.get(entry_id)["study_attempts"], 2)
        self.assertEqual(self.db.recent_first_round_outcomes([entry_id]), {entry_id: [1]})

    def test_recent_history_uses_only_five_latest_first_round_answers(self):
        entry_id, _ = self.db.add("casa")
        for index in range(7):
            self.db.record_study(entry_id, bool(index % 2), session_id=f"session-{index}")
        self.db.record_study(entry_id, True, session_id="repeat", round_number=2)
        self.assertEqual(self.db.recent_first_round_outcomes([entry_id]),
                         {entry_id: [0, 1, 0, 1, 0]})

    def test_review_backup_round_trip_and_rejects_invalid_history(self):
        entry_id, _ = self.db.add("casa")
        self.db.record_study(entry_id, False, session_id="first", round_number=1)
        self.db.record_study(entry_id, True, session_id="first", round_number=2)
        backup = Path(self.temp.name) / "study-backup.json"
        self.db.export_backup(backup)
        payload = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(payload["format_version"], 4)
        self.assertEqual(len(payload["study_reviews"]), 2)
        restored = VocabularyDB(Path(self.temp.name) / "restored-study.sqlite3")
        try:
            self.assertEqual(restored.import_backup(backup)["added"], 1)
            self.assertEqual(restored.recent_first_round_outcomes([entry_id]), {entry_id: [0]})
            self.assertEqual(restored.get(entry_id)["study_attempts"], 2)
            self.assertEqual(restored.import_backup(backup)["skipped"], 1)
            self.assertEqual(restored.conn.execute("SELECT COUNT(*) FROM study_reviews").fetchone()[0], 2)
            for change in ("duplicate", "unknown_word", "bad_counts"):
                bad = json.loads(json.dumps(payload))
                if change == "duplicate":
                    bad["study_reviews"].append(dict(bad["study_reviews"][0]))
                elif change == "unknown_word":
                    bad["study_reviews"][0]["entry_id"] = 999
                else:
                    bad["entries"][0]["study_attempts"] = 0
                    bad["entries"][0]["remembered_count"] = 0
                    bad["entries"][0]["again_count"] = 0
                backup.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError, msg=change):
                    restored.import_backup(backup)
                self.assertEqual(restored.conn.execute("SELECT COUNT(*) FROM study_reviews").fetchone()[0], 2)
            old = json.loads(json.dumps(payload))
            old["format_version"] = 2
            old.pop("study_reviews")
            old.pop("starter_catalog")
            backup.write_text(json.dumps(old), encoding="utf-8")
            legacy = VocabularyDB(Path(self.temp.name) / "version-two.sqlite3")
            try:
                self.assertEqual(legacy.import_backup(backup)["added"], 1)
                self.assertEqual(legacy.get(entry_id)["study_attempts"], 2)
                self.assertEqual(legacy.recent_first_round_outcomes([entry_id]), {})
            finally:
                legacy.close()
        finally:
            restored.close()

    def test_starter_collection_full_merge_filters_and_backup(self):
        collection = load_starter_catalog()
        self.assertEqual(len(collection), 7695)
        self.assertEqual(starter_counts(collection),
                         {"A1": 542, "A2": 1038, "B1": 2058, "B2": 4057})
        self.assertEqual(len({item["key"] for item in collection}), len(collection))
        phrase = next(item for item in collection if item["kind"] == "proverb")
        self.assertTrue(any(item["kind"] == "idiom" for item in collection))
        existing, _ = self.db.add("casa")
        self.db.record_study(existing, False)
        self.db.update(existing, original_text="casa", context="", notes="my own note",
                       difficulty="unknown", definition_it="", gloss_en="", example_it="",
                       difficulty_reason="")
        result = self.db.import_starter(collection)
        self.assertEqual(result["added"] + result["skipped"], len(collection))
        self.assertEqual(self.db.counts()["unknown"], len(collection))
        self.assertEqual(self.db.get(existing)["notes"], "my own note")
        self.assertEqual(self.db.get(existing)["again_count"], 1)
        self.assertEqual(self.db.import_starter(collection),
                         {"added": 0, "skipped": len(collection)})
        proverb = next(row for row in self.db.list_entries(search=phrase["text"])
                       if row["normalized_key"] == phrase["key"])
        self.assertEqual(proverb["starter_kind"], "proverb")
        self.assertEqual(proverb["difficulty"], "unknown")
        self.assertEqual(len(self.db.list_entries(starter_levels=["Personal"])), 1)
        for level, count in self.db.starter_counts().items():
            self.assertEqual(len(self.db.list_entries(starter_levels=[level])), count)
        self.assertEqual(self.db.list_entries(starter_levels=[]), [])
        self.assertEqual(self.db.study_pool(["easy", "medium", "hard"]), ([], 0))
        request = self.db.create_request([{
            key: proverb[key] for key in ("id", "revision", "original_text", "context")
        }], "B1", "gpt-6-sol")
        self.assertEqual(self.db.apply_results(response_for(request, difficulty="hard"), request)["applied"], 1)
        ready, incomplete = self.db.study_pool(["hard"])
        self.assertEqual([entry["id"] for entry in ready], [proverb["id"]])
        self.assertEqual(incomplete, 0)
        self.assertEqual(self.db.get(proverb["id"])["starter_level"], phrase["cefr_level"])
        backup = Path(self.temp.name) / "starter-backup.json"
        self.db.export_backup(backup)
        restored = VocabularyDB(Path(self.temp.name) / "starter-restored.sqlite3")
        try:
            self.assertEqual(restored.import_backup(backup)["added"], len(collection))
            self.assertEqual(restored.starter_counts(), self.db.starter_counts())
            self.assertEqual(restored.get(existing)["notes"], "my own note")
            self.assertEqual(restored.import_backup(backup)["skipped"], len(collection))
            bad = json.loads(backup.read_text(encoding="utf-8"))
            bad["starter_catalog"].append(dict(bad["starter_catalog"][0]))
            backup.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "starter metadata"):
                restored.import_backup(backup)
            self.assertEqual(restored.starter_counts(), self.db.starter_counts())
        finally:
            restored.close()

    def test_starter_import_validation_is_atomic(self):
        collection = load_starter_catalog()[:3]
        invalid = collection + [dict(collection[0])]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.db.import_starter(invalid)
        self.assertEqual(sum(self.db.counts().values()), 0)
        self.assertEqual(self.db.import_starter(collection), {"added": 3, "skipped": 0})
        self.db.close()
        self.db = VocabularyDB(self.path)
        self.assertEqual(self.db.counts()["unknown"], 3)
        self.assertEqual(self.db.starter_counts()["A1"], 3)


def study_entry(entry_id, remembered, again):
    return {"id": entry_id, "study_attempts": remembered + again,
            "remembered_count": remembered, "again_count": again}


class AdaptiveStudyTests(unittest.TestCase):
    def test_custom_session_size_accepts_positive_counts_and_rejects_invalid_input(self):
        self.assertEqual(parse_session_size("Custom", " 37 "), 37)
        self.assertEqual(parse_session_size("10"), 10)
        self.assertEqual(parse_session_size("20"), 20)
        self.assertEqual(parse_session_size("All"), "all")
        pool = [study_entry(index, 0, 0) for index in range(1, 6)]
        self.assertEqual(len(StudySession(pool, parse_session_size("Custom", "99")).cards), 5)
        for invalid in ("", "0", "-3", "2.5", "five"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_session_size("Custom", invalid)

    def test_smoothed_recall_and_recent_lapses(self):
        new = study_entry(1, 0, 0)
        once = study_entry(2, 1, 0)
        strong = study_entry(3, 20, 0)
        struggling = study_entry(4, 0, 20)
        self.assertEqual(recall_estimate(new), 0.5)
        self.assertAlmostEqual(recall_estimate(once), 0.6)
        self.assertLess(recall_estimate(struggling), recall_estimate(new))
        self.assertLess(recall_estimate(new), recall_estimate(strong))
        self.assertLess(recall_estimate(strong, [0]), recall_estimate(strong))
        self.assertGreater(selection_weight(struggling), selection_weight(strong))
        self.assertGreaterEqual(selection_weight(strong), 1)
        self.assertLessEqual(selection_weight(struggling), 4)
        self.assertEqual(recall_label(new), "New")
        self.assertEqual(recall_label(struggling), "Needs practice")
        self.assertEqual(recall_label(strong), "Strong")

    def test_weighted_sessions_favor_struggling_without_excluding_strong(self):
        strong = study_entry(1, 20, 0)
        struggling = study_entry(2, 0, 20)
        first_cards = [choose_cards([strong, struggling], 1, rng=random.Random(seed))[0]["id"]
                       for seed in range(600)]
        self.assertGreater(first_cards.count(2), first_cards.count(1) * 2)
        self.assertGreater(first_cards.count(1), 0)
        pool = [study_entry(index, 0, 0) for index in range(1, 31)]
        session = StudySession(pool, 10, rng=random.Random(17))
        self.assertEqual(len(session.cards), 10)
        self.assertEqual(len({card["id"] for card in session.cards}), 10)
        all_session = StudySession(pool, "all", rng=random.Random(17))
        self.assertEqual(len(all_session.cards), 30)
        self.assertEqual(len({card["id"] for card in all_session.cards}), 30)

    def test_recent_first_round_failures_change_selection(self):
        first = study_entry(1, 20, 0)
        second = study_entry(2, 20, 0)
        baseline = sum(choose_cards([first, second], 1, rng=random.Random(seed))[0]["id"] == 1
                       for seed in range(600))
        after_lapses = sum(choose_cards([first, second], 1, rng=random.Random(seed),
                                       recent_outcomes={1: [0, 0, 0]})[0]["id"] == 1
                            for seed in range(600))
        self.assertGreater(after_lapses, baseline + 50)

    def test_again_round_keeps_session_id_and_is_separate_from_first_round(self):
        session = StudySession([study_entry(1, 0, 0), study_entry(2, 0, 0)],
                               "all", rng=random.Random(4))
        session_id = session.session_id
        session.reveal()
        session.reveal()
        again_id = session.answer(False)
        session.reveal()
        session.reveal()
        session.answer(True)
        self.assertTrue(session.next_round())
        self.assertEqual(session.session_id, session_id)
        self.assertEqual(session.round_number, 2)
        self.assertEqual([card["id"] for card in session.cards], [again_id])


FAKE_CODEX = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if os.environ.get("OPENAI_API_KEY"):
    print("API key leaked", file=sys.stderr)
    sys.exit(2)
if mode == "slow":
    time.sleep(20)
    sys.exit(0)
if mode == "error":
    print("model_not_found: unavailable", file=sys.stderr)
    sys.exit(1)
prompt = sys.stdin.read()
request = json.loads(prompt.split("Input JSON:\\n", 1)[1])
results = []
for item in request["entries"]:
    results.append({"id":item["id"], "revision":item["revision"],
        "original_text":item["original_text"], "status":"ok", "difficulty":"easy",
        "definition_it":"Definizione.", "gloss_en":"gloss", "example_it":"Esempio.",
        "difficulty_reason":"Common.", "review_note":None})
target = Path(sys.argv[sys.argv.index("--output-last-message")+1])
target.write_text(json.dumps({"schema_version":1,"request_id":request["request_id"],
    "results":results}), encoding="utf-8")
'''


class ClassifierSubprocessTests(unittest.TestCase):
    def test_windows_cancel_falls_back_if_taskkill_is_missing(self):
        stopped = []
        proc = SimpleNamespace(pid=42, poll=lambda: None,
                               terminate=lambda: stopped.append("terminated"),
                               communicate=lambda timeout=None: ("", ""))
        with patch("classifier.os.name", "nt"), \
                patch("classifier.subprocess.run", side_effect=FileNotFoundError):
            _stop_process(proc)
        self.assertEqual(stopped, ["terminated"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.executable = Path(self.temp.name) / "fake-codex"
        self.executable.write_text(FAKE_CODEX, encoding="utf-8")
        self.executable.chmod(self.executable.stat().st_mode | stat.S_IXUSR)
        self.db = VocabularyDB(Path(self.temp.name) / "database.sqlite3")
        self.db.add("casa")
        self.request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_fake_codex_and_error_preserve_data(self):
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "ok", "OPENAI_API_KEY": "must-not-pass"}):
            response = run_codex(self.request, executable=str(self.executable), timeout=30)
        self.assertEqual(len(validate_response(response, self.request)), 1)
        self.assertEqual(self.db.counts()["unknown"], 1)
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "error"}):
            with self.assertRaisesRegex(ClassifierError, "model is unavailable"):
                run_codex(self.request, executable=str(self.executable), timeout=30)
        self.assertEqual(self.db.counts()["unknown"], 1)

    def test_cancellation_preserves_data(self):
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        try:
            with patch.dict(os.environ, {"FAKE_CODEX_MODE": "slow"}):
                with self.assertRaises(ClassificationCancelled):
                    run_codex(self.request, executable=str(self.executable),
                              timeout=30, cancel_event=cancel)
        finally:
            timer.cancel()
        self.assertEqual(self.db.counts()["unknown"], 1)

    def test_recheck_cli_failure_and_cancellation_keep_prior_result(self):
        entry_id = self.request["entries"][0]["id"]
        self.db.apply_results(response_for(self.request), self.request)
        self.db.save_evaluation_settings({"level": "B2"})
        recheck = self.db.create_request(self.db.pending_snapshot(recheck_only=True), "B2", "gpt-6-sol")
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "error"}):
            with self.assertRaises(ClassifierError):
                run_codex(recheck, executable=str(self.executable), timeout=30)
        self.assertEqual(self.db.get(entry_id)["difficulty"], "easy")
        self.assertEqual(self.db.pending_recheck_count(), 1)
        cancel = threading.Event()
        timer = threading.Timer(0.3, cancel.set)
        timer.start()
        try:
            with patch.dict(os.environ, {"FAKE_CODEX_MODE": "slow"}):
                with self.assertRaises(ClassificationCancelled):
                    run_codex(recheck, executable=str(self.executable), timeout=30, cancel_event=cancel)
        finally:
            timer.cancel()
        self.assertEqual(self.db.get(entry_id)["definition_it"], "Una definizione breve.")
        self.assertEqual(self.db.pending_recheck_count(), 1)
        with patch.dict(os.environ, {"FAKE_CODEX_MODE": "ok"}):
            response = run_codex(recheck, executable=str(self.executable), timeout=30)
        self.assertEqual(self.db.apply_results(response, recheck)["applied"], 1)
        self.assertEqual(self.db.pending_recheck_count(), 0)


class SettingsWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = VocabularyDB(Path(self.temp.name) / "settings.sqlite3")
        self.db.add("casa")
        request = self.db.create_request(self.db.unknown_snapshot(), "B1", "gpt-6-sol")
        self.db.apply_results(response_for(request), request)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_level_change_schedules_recheck_without_opening_gui(self):
        scheduled = []
        messages = []
        variable = lambda value: SimpleNamespace(get=lambda: value)
        fake = SimpleNamespace(
            db=self.db, model_var=variable("gpt-6-sol"), effort_var=variable("high"),
            level_var=variable("B2"), timeout_var=variable("900"), worker=None,
            _evaluation_in_progress=False,
            _queued_recheck=False, _queued_include_unknown=False,
            _refresh_recheck_status=lambda: None, _refresh_library=lambda: None,
            _message=messages.append, after=lambda delay, callback: scheduled.append((delay, callback)),
            _start_queued_recheck=lambda: None,
        )
        self.assertTrue(VocabularyApp._save_settings(fake))
        self.assertEqual(self.db.pending_recheck_count(), 1)
        self.assertTrue(fake._queued_recheck)
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][0], 0)
        self.assertIn("Re-evaluating 1", messages[-1])

    def test_level_change_cancels_active_old_level_job(self):
        variable = lambda value: SimpleNamespace(get=lambda: value)
        cancel = threading.Event()
        fake = SimpleNamespace(
            db=self.db, model_var=variable("gpt-6-sol"), effort_var=variable("high"),
            level_var=variable("C1"), timeout_var=variable("900"),
            worker=SimpleNamespace(is_alive=lambda: True), cancel_event=cancel,
            _evaluation_in_progress=True,
            _queued_recheck=False, _queued_include_unknown=False,
            _refresh_recheck_status=lambda: None, _refresh_library=lambda: None,
            _message=lambda text: None,
        )
        self.assertTrue(VocabularyApp._save_settings(fake))
        self.assertTrue(cancel.is_set())
        self.assertTrue(fake._queued_recheck)
        self.assertTrue(fake._queued_include_unknown)


class ParallelEvaluationTests(unittest.TestCase):
    def test_rate_limit_error_does_not_claim_account_allowance_is_exhausted(self):
        self.assertIn("temporary traffic throttling", explain_failure("429 slow_down"))
        self.assertIn("account's Codex usage limit", explain_failure("usage limit reached"))

    def test_retry_preview_names_entries_and_cancel_uses_no_request(self):
        fake = SimpleNamespace(
            _evaluation_in_progress=False, worker=None,
            _save_settings=lambda **kwargs: True,
            db=SimpleNamespace(review_snapshot=lambda: [
                {"original_text": "D"}, {"original_text": "the"},
            ]),
            _requests_for_pending=lambda **kwargs: self.fail("Cancelled retry created a request"),
        )
        with patch("app.simpledialog.askinteger", return_value=None) as dialog:
            VocabularyApp._evaluate(fake, retry_reviewed=True)
        prompt = dialog.call_args.args[1]
        self.assertIn("D, the", prompt)
        self.assertIn("allowance", prompt)
        self.assertIn("need edits", prompt)

    def test_automatic_and_manual_requests_use_twenty_entry_batches(self):
        variable = lambda value: SimpleNamespace(get=lambda: value)
        fake = SimpleNamespace(
            db=SimpleNamespace(create_request=lambda entries, level, model: {"entries": entries}),
            level_var=variable("B1"), model_var=variable("gpt-6-sol"),
        )
        expected = {
            1: [1], 2: [2], 10: [10], 20: [20], 21: [20, 1],
            25: [20, 5], 50: [20, 20, 10],
            100: [20] * 5, 101: [20] * 5 + [1],
        }
        for count, expected_sizes in expected.items():
            snapshot = [{"id": index} for index in range(count)]
            requests = VocabularyApp._requests_for_pending(fake, snapshot=snapshot)
            sizes = [len(request["entries"]) for request in requests]
            self.assertEqual(sizes, expected_sizes)
            self.assertEqual([entry["id"] for request in requests
                              for entry in request["entries"]], list(range(count)))

    def test_copy_prompt_tracks_first_twenty_entries(self):
        snapshot = [{"id": index} for index in range(22)]
        captured = []

        def create_request(entries, level, model):
            captured.append(entries)
            return {"request_id": "request", "entries": entries}

        fake = SimpleNamespace(
            _save_settings=lambda **kwargs: True,
            db=SimpleNamespace(
                pending_snapshot=lambda: snapshot,
                create_request=create_request,
            ),
            level_var=SimpleNamespace(get=lambda: "B1"),
            model_var=SimpleNamespace(get=lambda: "gpt-6-sol"),
            clipboard_clear=lambda: None,
            clipboard_append=lambda text: None,
            _message=lambda text: None,
        )
        with patch("app.build_prompt", return_value="prompt"):
            VocabularyApp._copy_prompt(fake)
        self.assertEqual(captured, [snapshot[:20]])

    def test_worker_runs_at_most_five_jobs_and_returns_all_results(self):
        requests = []
        for index in range(10):
            entry = {"id": index + 1, "revision": 0, "original_text": f"word{index}"}
            requests.append({"schema_version": 1, "request_id": str(index),
                             "entries": [entry]})
        events = queue.Queue()
        fake = SimpleNamespace(events=events)
        cancel = threading.Event()
        five_started = threading.Event()
        lock = threading.Lock()
        active = 0
        maximum = 0

        def fake_run(request, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                if active == 5:
                    five_started.set()
            try:
                if not five_started.wait(2):
                    raise RuntimeError("Five jobs did not start concurrently")
                time.sleep(0.02)
                return response_for(request)
            finally:
                with lock:
                    active -= 1

        worker = threading.Thread(target=VocabularyApp._evaluation_worker,
                                  args=(fake, requests, "gpt-6-sol", "high", 30, cancel))
        with patch("app.run_codex", side_effect=fake_run):
            worker.start()
            batches = []
            terminal = None
            while terminal is None:
                event = events.get(timeout=5)
                if event[0] == "batch":
                    batches.append(event[1])
                    event[4].set()
                elif event[0] in ("done", "error", "cancelled"):
                    terminal = event
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(terminal, ("done",))
        self.assertEqual(maximum, 5)
        self.assertEqual(sorted(batches), list(range(1, 11)))

    def test_cancellation_stops_all_running_jobs(self):
        requests = [{"schema_version": 1, "request_id": str(index),
                     "entries": [{"id": index, "revision": 0,
                                  "original_text": f"word{index}"}]}
                    for index in range(6)]
        events = queue.Queue()
        fake = SimpleNamespace(events=events)
        cancel = threading.Event()

        def fake_run(request, **kwargs):
            if not kwargs["cancel_event"].wait(2):
                raise RuntimeError("Cancellation did not reach a running job")
            raise ClassificationCancelled("Evaluation cancelled")

        worker = threading.Thread(target=VocabularyApp._evaluation_worker,
                                  args=(fake, requests, "gpt-6-sol", "high", 30, cancel))
        with patch("app.run_codex", side_effect=fake_run):
            worker.start()
            started = [events.get(timeout=5) for _ in range(5)]
            self.assertTrue(all(event[0] == "running" for event in started))
            cancel.set()
            terminal = events.get(timeout=5)
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(terminal[0], "cancelled")
        self.assertTrue(events.empty())


class StudyWorkflowTests(unittest.TestCase):
    def test_library_tree_reuses_unchanged_rows_and_preserves_order(self):
        class Tree:
            def __init__(self):
                self.order = []
                self.values = {}
                self.changes = []

            def delete(self, *items):
                for item in items:
                    self.order.remove(item)
                    del self.values[item]
                self.changes.append("delete")

            def insert(self, _parent, index, *, iid, values):
                self.order.insert(index, iid)
                self.values[iid] = values
                self.changes.append("insert")

            def item(self, item, *, values):
                self.values[item] = values
                self.changes.append("update")

            def move(self, item, _parent, index):
                self.order.remove(item)
                self.order.insert(index, item)
                self.changes.append("move")

        tree = Tree()
        fake = SimpleNamespace(tree=tree, _tree_rows={}, _tree_order=[])
        first = [("1", ("a",)), ("2", ("b",)), ("3", ("c",))]
        VocabularyApp._render_library_rows(fake, first)
        tree.changes.clear()
        VocabularyApp._render_library_rows(fake, first)
        self.assertEqual(tree.changes, [])
        VocabularyApp._render_library_rows(fake, [("1", ("a",)), ("2", ("changed",)),
                                                  ("3", ("c",))])
        self.assertEqual(tree.changes, ["update"])
        tree.changes.clear()
        VocabularyApp._render_library_rows(fake, [("2", ("changed",)), ("3", ("c",))])
        self.assertEqual(tree.order, ["2", "3"])
        VocabularyApp._render_library_rows(fake, [("1", ("a",)), ("2", ("changed",)),
                                                  ("3", ("c",))])
        self.assertEqual(tree.order, ["1", "2", "3"])
        VocabularyApp._render_library_rows(fake, [("3", ("c",)), ("2", ("changed",)),
                                                  ("4", ("d",))])
        self.assertEqual(tree.order, ["3", "2", "4"])

    def test_study_result_updates_only_its_visible_library_row(self):
        with tempfile.TemporaryDirectory() as directory:
            db = VocabularyDB(Path(directory) / "study-row.sqlite3")
            try:
                entry_id, _ = db.add("casa")
                key = str(entry_id)
                previous = ("casa", "-", "Easy", "Ready", "0/0 - New")
                updated_items = []
                fake = SimpleNamespace(
                    db=db, _tree_rows={key: previous},
                    tree=SimpleNamespace(item=lambda item, **kw: updated_items.append((item, kw))),
                    _selected_id=lambda: None,
                )
                db.record_study(entry_id, True)
                VocabularyApp._refresh_study_result(fake, entry_id)
                self.assertEqual(fake._tree_rows[key][-1], "1/1 - Building")
                self.assertEqual(updated_items, [(key, {"values": fake._tree_rows[key]})])
                self.assertEqual(fake._tree_rows[key][:-1], previous[:-1])
            finally:
                db.close()

    def test_study_count_refreshes_when_study_tab_opens(self):
        refreshed = []
        fake = SimpleNamespace(tabs=SimpleNamespace(select=lambda: "study"),
                               study_tab="study", _refresh_study_count=lambda: refreshed.append(True))
        VocabularyApp._on_tab_changed(fake)
        self.assertEqual(refreshed, [True])
        fake.tabs.select = lambda: "library"
        VocabularyApp._on_tab_changed(fake)
        self.assertEqual(refreshed, [True])

    def test_reveal_shows_example_before_answer(self):
        card = {**study_entry(1, 0, 0), "original_text": "discutere",
                "example_it": "Discutiamo dopo cena.", "gloss_en": "to discuss",
                "definition_it": "Parlare con altri di un argomento."}
        session = StudySession([card], "all")
        shown = []
        button_changes = {"reveal": [], "remembered": [], "again": []}
        fake = SimpleNamespace(
            session=session, card_answer=SimpleNamespace(set=shown.append),
            reveal_button=SimpleNamespace(configure=lambda **kw: button_changes["reveal"].append(kw)),
            remembered_button=SimpleNamespace(configure=lambda **kw: button_changes["remembered"].append(kw)),
            again_button=SimpleNamespace(configure=lambda **kw: button_changes["again"].append(kw)),
        )
        VocabularyApp._reveal(fake)
        self.assertIn(card["example_it"], shown[-1])
        self.assertNotIn(card["gloss_en"], shown[-1])
        self.assertNotIn(card["definition_it"], shown[-1])
        self.assertFalse(session.revealed)
        with self.assertRaisesRegex(ValueError, "Reveal"):
            session.answer(True)
        VocabularyApp._reveal(fake)
        self.assertIn(card["gloss_en"], shown[-1])
        self.assertIn(card["definition_it"], shown[-1])
        self.assertTrue(session.revealed)
        self.assertEqual(button_changes["remembered"][-1], {"state": "normal"})
        self.assertEqual(button_changes["again"][-1], {"state": "normal"})
        session.answer(True)
        self.assertFalse(session.revealed)

    def test_app_answer_records_session_round_without_opening_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            db = VocabularyDB(Path(directory) / "study.sqlite3")
            try:
                entry_id, _ = db.add("casa")
                session = StudySession([study_entry(entry_id, 0, 0)], "all")
                refreshed = []
                fake = SimpleNamespace(db=db, session=session, _show_card=lambda: None,
                                       _refresh_study_result=refreshed.append)
                session.reveal()
                session.reveal()
                VocabularyApp._answer(fake, False)
                self.assertEqual(db.recent_first_round_outcomes([entry_id]), {entry_id: [0]})
                self.assertEqual(db.get(entry_id)["again_count"], 1)
                self.assertTrue(session.next_round())
                session.reveal()
                session.reveal()
                VocabularyApp._answer(fake, True)
                self.assertEqual(db.recent_first_round_outcomes([entry_id]), {entry_id: [0]})
                self.assertEqual(db.get(entry_id)["remembered_count"], 1)
                self.assertEqual(refreshed, [entry_id, entry_id])
                rows = db.conn.execute("SELECT DISTINCT session_id FROM study_reviews").fetchall()
                self.assertEqual([row[0] for row in rows], [session.session_id])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
