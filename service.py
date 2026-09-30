"""UI-independent coordination for the Italian Vocabulary desktop client.

All VocabularyDB calls occur on the owning (UI) thread. Codex workers only use
immutable request snapshots and pass results through a queue for main-thread writes.
"""

import json
import queue
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from classifier import (ClassificationCancelled, SCHEMA_PATH, build_prompt,
                        load_json_strict, run_codex, validate_response)
from profiles import ProfileStore
from starter import load_starter_catalog
from storage import VocabularyDB
from study import StudySession, parse_session_size

BATCH_SIZE = 20
MAX_JOBS = 5


class VocabularyService:
    def __init__(self, store=None):
        self.store = store or ProfileStore()
        self.store.migrate_legacy_if_needed()
        self.profile = None
        self.db = None
        self.session = None
        self.events = queue.Queue()
        self.worker = None
        self.cancel_event = None
        self.evaluation = {"state": "idle", "done": 0, "batches": 0,
                           "applied": 0, "needs_review": 0, "stale": 0, "total": 0}
        self.queued_recheck = None
        self.closing = False
        active = self.store.active_profile()
        if active:
            self.open_profile(active["id"])

    def profiles(self):
        return self.store.list_profiles()

    def create_profile(self, name, source):
        if self.running:
            raise RuntimeError("Finish or cancel evaluation before changing profiles.")
        profile = self.store.create(name, source)
        self.open_profile(profile["id"])
        return profile

    def open_profile(self, profile_id):
        if self.running or self.worker and self.worker.is_alive():
            raise RuntimeError("Finish or cancel evaluation before changing profiles.")
        path = self.store.database_path(profile_id)
        db = VocabularyDB(path)
        try:
            self.store.activate(profile_id)
        except Exception:
            db.close()
            raise
        if self.db:
            self.db.close()
        self.db = db
        self.profile = next(p for p in self.store.list_profiles() if p["id"] == profile_id)
        self.session = None
        self.evaluation = {"state": "idle", "done": 0, "batches": 0,
                           "applied": 0, "needs_review": 0, "stale": 0, "total": 0}

    @property
    def running(self):
        return self.evaluation["state"] in ("running", "cancelling")

    def settings(self):
        return {key: self.db.get_setting(key, default) for key, default in
                (("level", "B1"), ("model", "gpt-6-sol"),
                 ("effort", "high"), ("timeout", "900"))}

    def save_settings(self, values, *, auto_recheck=True):
        model = values["model"].strip()
        effort = values["effort"]
        level = values["level"]
        try:
            timeout = int(values["timeout"])
        except (TypeError, ValueError):
            timeout = 0
        if not model or len(model) > 100 or any(ch.isspace() for ch in model):
            raise ValueError("Enter a valid model name without spaces.")
        if effort not in ("low", "medium", "high", "xhigh", "max") or level not in (
                "A1", "A2", "B1", "B2", "C1", "C2") or not 30 <= timeout <= 3600:
            raise ValueError("Choose valid effort and level, and a timeout of 30 to 3600 seconds.")
        changed, pending = self.db.save_evaluation_settings(
            {"model": model, "effort": effort, "level": level, "timeout": str(timeout)})
        if changed and pending and auto_recheck:
            self.queued_recheck = not self.running
            if self.running:
                self.queued_recheck = False  # Include Unknown after an interrupted run.
                self.cancel_evaluation(clear_queue=False)
        return changed, pending

    def library(self, search="", difficulties=None, levels=None, review_only=False):
        return self.db.list_entries(search, difficulties, levels, review_only,
                                    library_view=True)

    def add(self, word):
        return self.db.add(word)

    def update(self, entry_id, values, revision):
        return self.db.update(entry_id, expected_revision=revision, **values)

    def delete(self, entry_id):
        return self.db.delete(entry_id)

    def study_pool(self, difficulties, topic, level):
        return self.db.study_pool(difficulties, topic, level)

    def study_pool_counts(self, difficulties, topic, level):
        return self.db.study_pool_counts(difficulties, topic, level)

    def start_session(self, difficulties, topic, level, size, custom=""):
        count = parse_session_size(size, custom)
        ready, incomplete = self.db.study_pool(difficulties, topic, level, compact=True)
        if not ready:
            raise ValueError("No study-ready cards match. Adjust filters or prepare words in Library.")
        recent = self.db.recent_first_round_outcomes([row["id"] for row in ready])
        self.session = StudySession(ready, count, recent_outcomes=recent)
        return len(self.session.cards), incomplete

    def reveal(self):
        return self.session.reveal() if self.session else 0

    def answer(self, remembered):
        session = self.session
        if not session or not session.revealed or session.current is None:
            raise ValueError("Reveal the full answer before rating this card.")
        entry_id = session.current["id"]
        self.db.record_study(entry_id, remembered, session_id=session.session_id,
                             round_number=session.round_number)
        session.answer(remembered)
        return entry_id

    def next_round(self):
        return self.session.next_round() if self.session else False

    def end_session(self):
        self.session = None

    def _requests(self, snapshot):
        settings = self.settings()
        return [self.db.create_request(snapshot[i:i+BATCH_SIZE], settings["level"], settings["model"])
                for i in range(0, len(snapshot), BATCH_SIZE)]

    def evaluation_snapshot(self, *, retry_reviewed=False, recheck_only=False):
        return self.db.review_snapshot() if retry_reviewed else self.db.pending_snapshot(
            recheck_only=recheck_only)

    def evaluate(self, snapshot=None, *, recheck_only=False):
        if self.running or self.worker and self.worker.is_alive():
            raise RuntimeError("An evaluation is already running.")
        if snapshot is None:
            snapshot = self.evaluation_snapshot(recheck_only=recheck_only)
        if not snapshot:
            return False
        requests = self._requests(snapshot)
        settings = self.settings()
        self.queued_recheck = None
        self.cancel_event = threading.Event()
        self.evaluation = {"state": "running", "done": 0, "batches": len(requests),
                           "applied": 0, "needs_review": 0, "stale": 0,
                           "total": sum(len(r["entries"]) for r in requests)}
        self.worker = threading.Thread(target=self._evaluation_worker,
                                       args=(requests, settings, self.cancel_event), daemon=True)
        try:
            self.worker.start()
        except RuntimeError:
            self.evaluation["state"] = "failed"
            raise
        return True

    def _evaluation_worker(self, requests, settings, cancel):
        failure = None
        try:
            with ThreadPoolExecutor(max_workers=min(MAX_JOBS, len(requests))) as pool:
                futures = {pool.submit(self._run_batch, i, request, settings, cancel): i
                           for i, request in enumerate(requests, 1)}
                for future in as_completed(futures):
                    if cancel.is_set():
                        break
                    try:
                        index, request, response = future.result()
                    except ClassificationCancelled:
                        cancel.set()
                        break
                    except Exception as exc:
                        failure = (futures[future], str(exc))
                        cancel.set()
                        break
                    ack = threading.Event()
                    self.events.put(("batch", index, request, response, ack))
                    while not ack.wait(0.1):
                        if cancel.is_set() and self.closing:
                            break
                    if cancel.is_set():
                        break
        except Exception as exc:
            failure = (0, str(exc))
            cancel.set()
        self.events.put(("failed", *failure) if failure else
                        ("cancelled",) if cancel.is_set() else ("completed",))

    def _run_batch(self, index, request, settings, cancel):
        if cancel.is_set():
            raise ClassificationCancelled("Evaluation cancelled.")
        self.events.put(("running", index))
        response = run_codex(request, model=settings["model"], effort=settings["effort"],
                             timeout=int(settings["timeout"]), cancel_event=cancel)
        validate_response(response, request)
        return index, request, response

    def poll_events(self):
        result = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "batch":
                _, index, request, response, ack = event
                try:
                    if not self.cancel_event.is_set():
                        counts = self.db.apply_results(response, request)
                        self.evaluation["done"] += 1
                        for src, dest in (("applied", "applied"), ("needs_review", "needs_review"),
                                          ("skipped_stale", "stale")):
                            self.evaluation[dest] += counts[src]
                        result.append(("batch", index, counts))
                except (ValueError, OSError, sqlite3.Error) as exc:
                    self.cancel_event.set()
                    self.evaluation["state"] = "failed"
                    result.append(("rejected", index, str(exc)))
                finally:
                    ack.set()
            elif kind in ("failed", "cancelled", "completed"):
                if self.evaluation["state"] != "failed":
                    self.evaluation["state"] = "partial failure" if kind == "failed" and self.evaluation["done"] else kind
                result.append(event)
                if self.queued_recheck is not None and not self.closing:
                    recheck_only = self.queued_recheck
                    self.queued_recheck = None
                    # The caller schedules this after the worker has exited.
                    result.append(("queued_recheck", recheck_only))
            else:
                result.append(event)
        return result

    def cancel_evaluation(self, *, clear_queue=True):
        if clear_queue:
            self.queued_recheck = None
        if self.running and self.cancel_event:
            self.evaluation["state"] = "cancelling"
            self.cancel_event.set()

    def export_prompt(self, directory):
        snapshot = self.evaluation_snapshot()
        if not snapshot:
            raise ValueError("There are no pending entries to export.")
        requests = self._requests(snapshot)
        target = Path(directory) / f"italian-vocabulary-{requests[0]['request_id']}"
        target.mkdir(mode=0o700)
        for index, request in enumerate(requests, 1):
            (target / f"prompt-{index:02d}.txt").write_text(build_prompt(request), encoding="utf-8")
            (target / f"request-{index:02d}.json").write_text(
                json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.copyfile(SCHEMA_PATH, target / "result_schema.json")
        return target, len(snapshot), len(requests)

    def copy_prompt(self):
        snapshot = self.evaluation_snapshot()[:BATCH_SIZE]
        if not snapshot:
            raise ValueError("There are no pending entries to copy.")
        request = self._requests(snapshot)[0]
        return build_prompt(request), request["request_id"], len(snapshot)

    def import_ai(self, path):
        response = load_json_strict(Path(path).read_text(encoding="utf-8"))
        if not isinstance(response, dict) or not isinstance(response.get("request_id"), str):
            raise ValueError("JSON has no request ID.")
        request = self.db.get_request(response["request_id"])
        if request is None:
            raise ValueError("Request ID is not in this database. Import the matching backup first.")
        return self.db.apply_results(response, request)

    def import_starter(self):
        return self.db.import_starter(load_starter_catalog())

    def export_backup(self, path):
        return self.db.export_backup(path)

    def import_backup(self, path):
        return self.db.import_backup(path)

    def close(self):
        self.closing = True
        self.cancel_evaluation()
        if self.worker and self.worker.is_alive():
            return False
        if self.db:
            self.db.close()
            self.db = None
        return True
