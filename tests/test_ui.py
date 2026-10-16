"""UI checks use real Tk widgets; headless machines skip only the GUI class."""
import os
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from organizer.core import Entry, Plan
from organizer.gui import Application
from organizer.ui import HELP, format_count, format_size, help_icon, tooltip_position


class PresentationTests(unittest.TestCase):
    def test_greek_number_and_size_formatting(self):
        self.assertEqual(format_count(12000), '12.000')
        self.assertEqual(format_size(0), '0 B')
        self.assertEqual(format_size(1024), '1,0 KB')
        self.assertEqual(format_size(1536 * 1024), '1,5 MB')
        self.assertEqual(format_size(1024 ** 4), '1,0 TB')

    def test_tooltip_placement_stays_on_screen_and_avoids_anchor(self):
        self.assertEqual(tooltip_position((50, 50, 20, 20), (200, 60), (800, 600)), (50, 78))
        x, y = tooltip_position((780, 570, 20, 20), (200, 60), (800, 600))
        self.assertLessEqual(x + 200, 800)
        self.assertLess(y + 60, 570)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_help_for_every_primary_action(self):
        for key in ('scan', 'copy', 'move', 'preview', 'destination', 'cancel', 'history', 'execute'):
            self.assertIn(key, HELP)
            self.assertGreater(len(HELP[key]), 20)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.destroy()
        except tk.TclError as exc:
            if os.name == 'nt' or os.environ.get('FILE_ORGANIZER_REQUIRE_GUI_TESTS') == '1':
                raise
            raise unittest.SkipTest(f'Απαιτείται οθόνη για τις δοκιμές Tk: {exc}')

    def setUp(self):
        self.app = Application()
        self.errors = []
        self.app.report_callback_exception = lambda *args: self.errors.append(args)
        self.app.update()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app.logs = self.root / 'logs'

    def tearDown(self):
        self.app.busy = False
        self.app.close()
        self.assertEqual(self.errors, [])

    def plan(self, count=1, **kwargs):
        return Plan(self.root / 'destination', [Entry(self.root / f'{i}.pdf', self.root / 'destination' / f'{i}.pdf', 1024, 0)
                                               for i in range(count)], kwargs.get('warnings', []), kwargs.get('cancelled', False))

    def pump_until_finished(self):
        deadline = time.monotonic() + 5
        while self.app.busy and time.monotonic() < deadline:
            self.app.update()
            time.sleep(.01)
        self.app.update()
        self.assertFalse(self.app.busy, 'Background task did not finish')

    def test_initial_and_missing_inputs(self):
        self.assertEqual(self.app.mode.get(), 'copy')
        self.assertTrue(self.app.run_button.instate(['disabled']))
        self.assertTrue(self.app.cancel_button.instate(['disabled']))
        with patch('organizer.gui.messagebox.showinfo') as dialog:
            self.app.begin_scan()
        dialog.assert_called_once()
        self.assertIn('Προσθέστε', self.app.status.get())
        self.assertEqual(self.app.badge.cget('style'), 'warning.Badge.TLabel')

    def test_preview_pagination_metrics_and_full_paths(self):
        self.app._accept_plan(self.plan(251))
        self.assertEqual(self.app.file_count.get(), '251')
        self.assertEqual(self.app.total_size.get(), '251,0 KB')
        self.assertEqual(len(self.app.table.get_children()), 250)
        self.assertTrue(self.app.previous_button.instate(['disabled']))
        self.assertTrue(self.app.next_button.instate(['!disabled']))
        self.app.show_page(1)
        self.assertEqual(len(self.app.table.get_children()), 1)
        self.assertTrue(self.app.next_button.instate(['disabled']))
        self.app.table.selection_set('250')
        self.app.update()
        self.assertEqual(self.app.selected_source.get(), str(self.root / '250.pdf'))
        self.assertTrue(self.app.run_button.instate(['!disabled']))
        self.app.invalidate()
        self.assertEqual(self.app.total_size.get(), '—')
        self.assertTrue(self.app.run_button.instate(['disabled']))

    def test_empty_cancelled_and_warning_states(self):
        self.app._accept_plan(self.plan(0))
        self.assertIn('Δεν βρέθηκαν', self.app.empty_message.cget('text'))
        self.assertTrue(self.app.run_button.instate(['disabled']))
        self.app._accept_plan(self.plan(cancelled=True))
        self.assertIn('ακυρώθηκε', self.app.status.get())
        self.assertTrue(self.app.run_button.instate(['disabled']))
        self.app._accept_plan(self.plan(warnings=['Δοκιμαστική προειδοποίηση']))
        self.assertEqual(self.app.warning_count.get(), '1')
        self.assertEqual(self.app.badge.cget('style'), 'warning.Badge.TLabel')
        self.assertIn('Δοκιμαστική', self.app.history.get('1.0', 'end'))

    def test_background_scan_preview_confirmation_and_copy(self):
        source = self.root / 'source'
        source.mkdir()
        original = source / 'δοκιμή.pdf'
        original.write_bytes(b'example')
        destination = self.root / 'destination'
        self.app.roots.insert('end', str(source))
        self.app.destination.set(str(destination))
        self.app.begin_scan()
        self.assertTrue(self.app.busy)
        self.assertTrue(self.app.run_button.instate(['disabled']))
        self.pump_until_finished()
        self.assertFalse(destination.exists())  # Preview must never modify files.
        target = self.app.plan.entries[0].target
        with patch('organizer.gui.messagebox.askyesno', return_value=False):
            self.app.begin_execution()
        self.assertFalse(destination.exists())
        with patch('organizer.gui.messagebox.askyesno', return_value=True):
            self.app.begin_execution()
        self.pump_until_finished()
        self.assertEqual(target.read_bytes(), b'example')
        self.assertTrue(original.exists())
        self.assertTrue(self.app.run_button.instate(['disabled']))
        self.assertEqual(self.app.badge.cget('style'), 'success.Badge.TLabel')
        self.assertIn('Επιτυχία', self.app.history.get('1.0', 'end'))
        self.assertEqual(len(list(self.app.logs.glob('*.jsonl'))), 1)

    def test_cancel_and_error_reset_controls(self):
        self.app._accept_plan(self.plan())
        self.app.busy = True
        self.app.update_controls()
        self.app.request_cancel()
        self.assertTrue(self.app.cancel.is_set())
        self.assertTrue(self.app.cancel_button.instate(['disabled']))
        self.app.events.put(('error', 'Δοκιμαστικό σφάλμα'))
        with patch('organizer.gui.messagebox.showerror'):
            self.pump_until_finished()
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.plan)
        self.assertTrue(self.app.run_button.instate(['disabled']))
        self.assertEqual(self.app.badge.cget('style'), 'error.Badge.TLabel')

    def test_history_has_distinct_tags_and_bounded_size(self):
        for i in range(600):
            self.app.append_history(f'Σφάλμα {i}', 'error')
        self.assertIn('Σφάλμα 599', self.app.history.get('1.0', 'end'))
        self.assertLess(int(self.app.history.index('end-1c').split('.')[0]), 501)
        self.assertTrue(self.app.history.tag_ranges('error'))
        self.app._accept_result({'completed': 0, 'failed': 1, 'cancelled': False})
        self.assertEqual(self.app.badge.cget('style'), 'error.Badge.TLabel')

    def test_tooltip_hover_focus_escape_and_destroy(self):
        badge = help_icon(self.app.settings, 'scan')
        badge.pack()
        self.app.update()
        tooltip = badge.tooltip
        tooltip.delay = 0
        badge.event_generate('<Enter>')
        self.app.update()
        self.assertIsNotNone(tooltip.window)
        badge.event_generate('<Leave>')
        self.app.update()
        self.assertIsNone(tooltip.window)
        badge.focus_force()
        self.app.update()
        self.assertIsNotNone(tooltip.window)
        badge.event_generate('<Escape>')
        self.app.update()
        self.assertIsNone(tooltip.window)
        tooltip.schedule()
        badge.destroy()
        self.app.update()
        self.assertIsNone(tooltip.pending)
        self.assertIsNone(tooltip.window)

    def test_responsive_layout_and_keyboard_reveal(self):
        self.app.geometry('800x620')
        self.app.update()
        self.assertTrue(self.app.compact)
        self.assertEqual(int(self.app.workspace.grid_info()['row']), 1)
        self.assertGreater(self.app.content.winfo_reqheight(), self.app.canvas.winfo_height())
        self.app.run_button.focus_force()
        self.app.update()
        self.assertGreater(self.app.canvas.yview()[0], 0)
        self.assertLessEqual(self.app.canvas.winfo_rooty() + self.app.canvas.winfo_height(), self.app.bar.winfo_rooty())
        self.app.geometry('1360x960')
        self.app.update()
        self.assertFalse(self.app.compact)
        self.assertEqual(int(self.app.workspace.grid_info()['column']), 1)

    def test_larger_font_scale_layout(self):
        from tkinter import font
        current = float(self.app.tk.call('tk', 'scaling'))
        self.app.tk.call('tk', 'scaling', current * 1.5)
        # A new instance picks up the new display scale, as it does at startup on Windows.
        large = Application()
        try:
            large.geometry('1200x820')
            large.update()
            self.assertTrue(large.compact)
            self.assertGreater(large.scale, self.app.scale)
            self.assertGreater(font.nametofont('TkDefaultFont', root=large).metrics('linespace'), 10)
            self.assertTrue(large.canvas.bbox('all'))
        finally:
            large.close()
            self.app.tk.call('tk', 'scaling', current)


if __name__ == '__main__':
    unittest.main()
