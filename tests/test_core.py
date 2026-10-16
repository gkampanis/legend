import os
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from organizer.core import scan, execute


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'source'
        self.src.mkdir()
        self.dst = self.src / 'organized'

    def file(self, name, content=b'example'):
        p = self.src / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        stamp = datetime(2024, 6, 1).timestamp()
        os.utime(p, (stamp, stamp))
        return p

    def test_preview_year_categories_and_overlapping_roots(self):
        p = self.file('nested/report.XLSX')
        self.file('a.pdf')
        self.file('unknown.xyz')
        plan = scan([self.src, self.src / 'nested'], self.dst)
        self.assertEqual(len(plan.entries), 3)
        self.assertEqual(next(e.target.relative_to(self.dst).parts[:2] for e in plan.entries if e.source == p), ('Excel', '2024'))
        self.assertFalse(self.dst.exists())
        self.assertTrue(p.exists())

    def test_exclusions(self):
        self.file('Windows/hidden.pdf')
        self.file('organized/hidden.pdf')
        self.file('ok.pdf')
        try:
            (self.src / 'link').symlink_to(self.src, target_is_directory=True)
        except OSError:
            pass
        self.assertEqual(len(scan([self.src], self.dst).entries), 1)

    def test_copy_duplicates_and_history(self):
        self.file('a/item.pdf', b'first')
        self.file('b/item.pdf', b'second')
        plan = scan([self.src], self.dst)
        self.assertEqual(len({e.target for e in plan.entries}), 2)
        result = execute(plan, 'copy', self.root / 'history.jsonl')
        self.assertEqual(result['completed'], 2)
        for e in plan.entries:
            self.assertEqual(e.source.read_bytes(), e.target.read_bytes())
        self.assertEqual(len((self.root / 'history.jsonl').read_text().splitlines()), 2)

    def test_changed_source_and_new_collision(self):
        p = self.file('a.pdf')
        plan = scan([self.src], self.dst)
        p.write_bytes(b'changed')
        self.assertEqual(execute(plan, 'move', self.root / 'log')['failed'], 1)
        plan = scan([self.src], self.dst)
        target = plan.entries[0].target
        target.parent.mkdir(parents=True)
        target.write_bytes(b'preserve')
        self.assertEqual(execute(plan, 'move', self.root / 'log')['failed'], 1)
        self.assertEqual(target.read_bytes(), b'preserve')
        self.assertTrue(p.exists())

    def test_move_and_cancel(self):
        p = self.file('a.docx')
        plan = scan([self.src], self.dst)
        event = threading.Event()
        event.set()
        self.assertTrue(scan([self.src], self.dst, event).cancelled)
        self.assertTrue(execute(plan, 'move', self.root / 'log', event)['cancelled'])
        self.assertTrue(p.exists())
        self.assertEqual(execute(plan, 'move', self.root / 'log')['completed'], 1)
        self.assertFalse(p.exists())
        self.assertTrue(plan.entries[0].target.exists())

    def test_cancel_during_copy_cleans_partial_file(self):
        source = self.file('large.pdf', b'x' * (3 * 1024 * 1024))
        plan = scan([self.src], self.dst)
        class CancelDuringCopy:
            calls = 0
            def is_set(self):
                self.calls += 1
                return self.calls >= 3
        result = execute(plan, 'move', self.root / 'log', CancelDuringCopy())
        self.assertTrue(result['cancelled'])
        self.assertTrue(source.exists())
        self.assertFalse(plan.entries[0].target.exists())
        self.assertEqual(list(self.dst.rglob('*.tmp')), [])

    def test_thousands_of_files(self):
        for i in range(2000):
            self.file(f'folder{i % 10}/file{i}.pdf', b'')
        progress = []
        plan = scan([self.src], self.dst, progress=progress.append)
        self.assertEqual(len(plan.entries), 2000)
        self.assertEqual(progress[-1], 2000)
        self.assertFalse(self.dst.exists())


if __name__ == '__main__':
    unittest.main()
