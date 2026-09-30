"""Repeatable offscreen performance check using temporary profiles only."""

import argparse
import gc
import json
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def memory():
    values = {}
    for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
        name, _, value = line.partition(':')
        if name in ('Rss', 'Pss', 'Private_Clean', 'Private_Dirty'):
            values[name] = int(value.split()[0]) / 1024
    return {"rss_mib": values['Rss'], "pss_mib": values['Pss'],
            "private_mib": values['Private_Clean'] + values['Private_Dirty'],
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}


def elapsed(callback, repeats=7):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def worker(root, scenario):
    from PySide6.QtWidgets import QApplication
    from profiles import ProfileStore
    from qt_app import MainWindow
    from qt_theme import STYLE
    from service import VocabularyService
    from storage import ALL_STUDY_LEVELS
    from topics import ALL_TOPICS

    app = QApplication([])
    app.setStyleSheet(STYLE)
    service = VocabularyService(ProfileStore(root=Path(root)))
    start = time.perf_counter()
    window = MainWindow(service)
    window.poll_timer.stop()
    window.show()
    app.processEvents()
    result = {"cards": window.model.rowCount(),
              "window_open_ms": (time.perf_counter() - start) * 1000,
              "library_memory": memory()}
    if scenario == 'library':
        result['library_refresh_ms'] = elapsed(window.refresh_library)
        result['study_count_ms'] = elapsed(window.refresh_study_count)
        result['word_sort_ms'] = elapsed(lambda: window.sort_library(0))
        gc.collect()
        result['final_memory'] = memory()
    elif scenario == 'study':
        def start_session():
            random.seed(12345)
            service.start_session(('easy', 'medium', 'hard'), ALL_TOPICS, ALL_STUDY_LEVELS, '20')
        result['study_20_ms'] = elapsed(start_session)
        result['selected_ids'] = [card['id'] for card in service.session.cards]
        gc.collect()
        result['final_memory'] = memory()
    else:
        ids = [card['id'] for card in window.model.rows]
        result['recent_history_ms'] = elapsed(lambda: service.db.recent_first_round_outcomes(ids))
        result['library_refresh_ms'] = elapsed(window.refresh_library)
        result['final_memory'] = memory()
    result['db_counts'] = service.db.counts()
    window.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--worker', choices=('library', 'study', 'history'))
    parser.add_argument('--root', type=Path)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.root, args.worker)))
        return
    if args.runs < 1:
        parser.error('--runs must be positive')
    from profiles import ProfileStore
    from storage import VocabularyDB
    with tempfile.TemporaryDirectory(prefix='italian-vocabulary-benchmark-') as temporary:
        store = ProfileStore(root=Path(temporary))
        profile = store.create('Benchmark', 'default')
        db = VocabularyDB(store.database_path(profile['id']))
        db.close()
        results = {"python": sys.version.split()[0], "runs": args.runs, "scenarios": {}}
        for scenario in ('library', 'study', 'history'):
            if scenario == 'history':
                db = VocabularyDB(store.database_path(profile['id']))
                # 100,000 deterministic synthetic answers across 100 cards.
                with db.conn:
                    db.conn.executemany(
                        'INSERT INTO study_reviews VALUES (?, ?, ?, ?, ?, ?)',
                        ((f'benchmark-{index}', index % 100 + 1, f'session-{index}', 1,
                          index % 2, f'2026-01-{index // 86400 + 1:02d}T{index // 3600 % 24:02d}:'
                          f'{index // 60 % 60:02d}:{index % 60:02d}+00:00')
                         for index in range(100000)))
                    db.conn.execute('UPDATE entries SET study_attempts=1000, '
                                    'remembered_count=CASE WHEN id % 2=0 THEN 1000 ELSE 0 END, '
                                    'again_count=CASE WHEN id % 2=0 THEN 0 ELSE 1000 END WHERE id<=100')
                db.close()
            samples = []
            for _ in range(args.runs):
                completed = subprocess.run(
                    [sys.executable, __file__, '--worker', scenario, '--root', temporary],
                    check=True, capture_output=True, text=True)
                samples.append(json.loads(completed.stdout))
            results['scenarios'][scenario] = samples
    content = json.dumps(results, indent=2) + '\n'
    if args.output:
        args.output.write_text(content, encoding='utf-8')
    print(content)


if __name__ == '__main__':
    main()
