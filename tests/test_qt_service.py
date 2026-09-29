"""Focused service and offscreen Qt checks with temporary profile data."""

import os
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from profiles import ProfileStore
from qt_app import MainWindow
from service import VocabularyService
from topics import ALL_TOPICS
from storage import ALL_STUDY_LEVELS


def response_for(request):
    return {"schema_version": 1, "request_id": request["request_id"], "results": [
        {"id": item["id"], "revision": item["revision"],
         "original_text": item["original_text"], "status": "ok", "difficulty": "easy",
         "definition_it": "Una definizione.", "gloss_en": "a meaning",
         "example_it": "Questo e un esempio.", "difficulty_reason": "Uso comune.",
         "review_note": None} for item in request["entries"]]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProfileStore(root=Path(self.temp.name))
        self.store.create("Temporary", "empty")
        self.service = VocabularyService(self.store)

    def tearDown(self):
        if self.service.db:
            self.service.close()
        self.temp.cleanup()

    def test_add_filter_edit_study_backup_and_profile(self):
        first, created = self.service.add(" si ")
        self.assertTrue(created)
        accented, created = self.service.add("sì")
        self.assertTrue(created)
        self.assertNotEqual(first, accented)
        self.assertEqual(self.service.add("SI"), (first, False))
        with self.assertRaisesRegex(ValueError, "120"):
            self.service.add("x" * 121)
        row = self.service.db.get(first)
        values = {key: row[key] or "" for key in
                  ("context", "notes", "definition_it", "gloss_en", "example_it", "difficulty_reason")}
        values.update(original_text="si", difficulty="easy", definition_it="Una parola.",
                      gloss_en="yes", example_it="Sì, capisco.")
        updated = self.service.update(first, values, row["revision"])
        self.assertEqual(updated["notes"], "")
        self.assertEqual(len(self.service.library("si", ["easy"], ["Personal"])), 1)
        self.assertEqual(len(self.service.library("", ["hard"], ["Personal"])), 0)
        total, incomplete = self.service.start_session(["easy"], ALL_TOPICS, ALL_STUDY_LEVELS, "All")
        self.assertEqual((total, incomplete), (1, 0))
        with self.assertRaisesRegex(ValueError, "Reveal"):
            self.service.answer(True)
        self.assertEqual(self.service.reveal(), 1)
        self.assertEqual(self.service.reveal(), 2)
        self.service.answer(False)
        self.assertEqual(self.service.db.get(first)["again_count"], 1)
        self.assertTrue(self.service.next_round())
        backup = Path(self.temp.name) / "backup.json"
        self.assertEqual(self.service.export_backup(backup), 2)
        second = self.service.create_profile("Second", "empty")
        self.assertEqual(self.service.db.counts()["unknown"], 0)
        self.service.import_backup(backup)
        self.assertEqual(self.service.db.counts()["unknown"], 1)
        self.service.open_profile(self.store.list_profiles()[0]["id"])
        self.assertEqual(self.service.db.get(first)["again_count"], 1)
        self.assertNotEqual(second["id"], self.service.profile["id"])

    def test_evaluation_worker_saves_on_owner_thread(self):
        ids = [self.service.add(f"word {i}")[0] for i in range(21)]
        with patch("service.run_codex", side_effect=lambda request, **kwargs: response_for(request)):
            self.assertTrue(self.service.evaluate())
            deadline = time.monotonic() + 5
            while self.service.running and time.monotonic() < deadline:
                self.service.poll_events()
                time.sleep(0.01)
            self.service.poll_events()
        self.assertEqual(self.service.evaluation["state"], "completed")
        self.assertEqual(self.service.evaluation["done"], 2)
        self.assertEqual(self.service.evaluation["applied"], 21)
        self.assertTrue(all(self.service.db.get(entry_id)["difficulty"] == "easy" for entry_id in ids))
        changed, pending = self.service.save_settings({**self.service.settings(), "level": "B2"})
        self.assertEqual((changed, pending), (True, 21))
        self.assertEqual(len(self.service.evaluation_snapshot(recheck_only=True)), 21)

    def test_cancellation_blocks_profile_switch_until_worker_exits(self):
        self.service.add("prova")
        started = threading.Event()
        def slow(_request, **kwargs):
            started.set()
            kwargs["cancel_event"].wait(3)
            from classifier import ClassificationCancelled
            raise ClassificationCancelled("Cancelled")
        with patch("service.run_codex", side_effect=slow):
            self.service.evaluate()
            self.assertTrue(started.wait(2))
            with self.assertRaisesRegex(RuntimeError, "cancel"):
                self.service.create_profile("Other", "empty")
            self.service.cancel_evaluation()
            deadline = time.monotonic() + 5
            while self.service.running and time.monotonic() < deadline:
                self.service.poll_events()
                time.sleep(0.01)
            self.service.poll_events()
        self.assertEqual(self.service.evaluation["state"], "cancelled")
        self.assertEqual(self.service.db.get(1)["difficulty"], "unknown")

    def test_prompt_export_copy_and_validated_import_with_space_in_path(self):
        entry_id, _ = self.service.add("città")
        folder = Path(self.temp.name) / "folder with spaces"
        folder.mkdir()
        prompt, request_id, count = self.service.copy_prompt()
        self.assertEqual(count, 1)
        self.assertIn("città", prompt)
        self.assertEqual(self.service.db.get_request(request_id)["entries"][0]["id"], entry_id)
        target, entries, batches = self.service.export_prompt(folder)
        self.assertEqual((entries, batches), (1, 1))
        self.assertTrue((target / "prompt-01.txt").is_file())
        request = json.loads((target / "request-01.json").read_text(encoding="utf-8"))
        response_path = folder / "AI response.json"
        response_path.write_text(json.dumps(response_for(request)), encoding="utf-8")
        self.assertEqual(self.service.import_ai(response_path)["applied"], 1)
        self.assertEqual(self.service.db.get(entry_id)["difficulty"], "easy")

    def test_partial_evaluation_failure_keeps_saved_batch(self):
        for index in range(21):
            self.service.add(f"parola {index}")
        def fake(request, **_kwargs):
            if len(request["entries"]) == 1:
                time.sleep(0.15)
                raise RuntimeError("Codex unavailable")
            return response_for(request)
        with patch("service.run_codex", side_effect=fake):
            self.service.evaluate()
            deadline = time.monotonic() + 5
            while self.service.running and time.monotonic() < deadline:
                self.service.poll_events()
                time.sleep(0.01)
            self.service.poll_events()
        self.assertEqual(self.service.evaluation["state"], "partial failure")
        self.assertEqual(self.service.evaluation["applied"], 20)
        self.assertEqual(len(self.service.evaluation_snapshot()), 1)


class QtSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = ProfileStore(root=Path(self.temp.name))
        store.create("Temporary", "empty")
        self.service = VocabularyService(store)
        self.window = MainWindow(self.service)

    def tearDown(self):
        self.window.close()
        self.temp.cleanup()

    def test_library_add_select_save_and_reopen(self):
        self.window.quick_add.setText("si")
        self.window.add_word()
        self.window.quick_add.setText("sì")
        self.window.add_word()
        self.assertEqual(self.window.model.rowCount(), 2)
        self.window.table.selectRow(0)
        self.assertIsNotNone(self.window.selected_id)
        self.window.detail_fields["notes"].setPlainText("Remember the accent.")
        self.assertTrue(self.window.is_dirty())
        self.assertTrue(self.window.save_detail())
        self.assertEqual(self.service.db.get(self.window.selected_id)["notes"], "Remember the accent.")
        self.window.search.setText("missing")
        self.window.refresh_library()
        self.assertEqual(self.window.model.rowCount(), 0)
        self.window.reset_filters()
        self.assertEqual(self.window.model.rowCount(), 2)

    def test_study_stages_and_single_rating(self):
        entry_id, _ = self.service.add("casa")
        row = self.service.db.get(entry_id)
        values = {key: row[key] or "" for key in
                  ("context", "notes", "definition_it", "gloss_en", "example_it", "difficulty_reason")}
        values.update(original_text="casa", difficulty="easy", definition_it="Abitazione.",
                      gloss_en="house", example_it="La casa e grande.")
        self.service.update(entry_id, values, row["revision"])
        self.window.refresh_library()
        self.window.tabs.setCurrentIndex(1)
        self.window.start_study()
        self.assertFalse(self.window.example_section.isVisible())
        self.assertFalse(self.service.session.revealed)
        self.window.answer(True)
        self.assertEqual(self.service.db.get(entry_id)["study_attempts"], 0)
        self.window.reveal()
        self.assertFalse(self.service.session.revealed)
        self.window.reveal()
        self.assertTrue(self.service.session.revealed)
        self.window.answer(True)
        self.window.answer(True)
        self.assertEqual(self.service.db.get(entry_id)["study_attempts"], 1)
        self.assertEqual(self.window.study_pages.currentIndex(), 2)

    def test_learner_level_change_starts_recheck(self):
        entry_id, _ = self.service.add("andare")
        with patch("service.run_codex", side_effect=lambda request, **kwargs: response_for(request)):
            self.service.evaluate()
            deadline = time.monotonic() + 5
            while self.service.running and time.monotonic() < deadline:
                self.service.poll_events()
                time.sleep(0.01)
            self.service.poll_events()
            self.window.level_field.setCurrentText("B2")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.app.processEvents()
                self.service.poll_events()
                if self.service.db.pending_recheck_count() == 0 and self.service.db.get_setting("level", "B1") == "B2":
                    break
                time.sleep(0.01)
        self.assertEqual(self.service.db.pending_recheck_count(), 0)
        self.assertEqual(self.service.db.get(entry_id)["difficulty"], "easy")

    def test_incoming_result_refreshes_clean_detail_and_preserves_draft(self):
        entry_id, _ = self.service.add("prova")
        self.window.refresh_library()
        self.window.table.selectRow(0)
        request = self.service.db.create_request(self.service.db.unknown_snapshot(),
                                                 self.service.settings()["level"], "gpt-6-sol")
        self.service.db.apply_results(response_for(request), request)
        self.window.refresh_library()
        self.assertEqual(self.window.detail_difficulty.currentData(), "easy")
        self.window.detail_fields["notes"].setPlainText("My draft")
        self.service.db.save_evaluation_settings({**self.service.settings(), "level": "B2"})
        recheck = self.service.db.create_request(self.service.db.pending_snapshot(recheck_only=True), "B2", "gpt-6-sol")
        self.service.db.apply_results(response_for(recheck), recheck)
        self.window.refresh_library()
        self.assertEqual(self.window.detail_fields["notes"].toPlainText(), "My draft")
        self.assertTrue(self.window.is_dirty())
        self.window.detail_fields["notes"].setPlainText("")
