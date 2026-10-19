import hashlib
from pathlib import Path
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import test_pdf_downloads as helpers
from myinvoices.archive_actions import removable_client_folder, trash_client_archive
from myinvoices.database import Database
from myinvoices.demo import xml_invoice, xml_page
from myinvoices.models import ApplicationError
from myinvoices.pdf_downloads import PDFDownloader
from myinvoices.xml_parser import parse_response


class ArchiveActionTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)
        self.desktop=self.root/'Desktop'
        PDFDownloader(self.db,helpers.Opener(self.pdf)).run(self.client,self.desktop,threading.Event())
        self.folder=self.desktop/'000000000_ΔΟΚΙΜΑΣΤΙΚΟ'

    def test_reset_is_client_scoped_retains_files_metadata_and_retries_all(self):
        other=self.db.add_client('Other','222222222')
        self.db.commit_page(self.db.checkpoint(other.id),parse_response(xml_page([xml_invoice(101,recipient=other.vat)])))
        other_row=self.db.invoice(other.id,101)
        self.db.record_pdf(other.id,101,'challenge',hashlib.sha256(other_row['download_url'].encode()).hexdigest())
        before=self.db.checkpoint(self.client.id)
        files={p:p.read_bytes() for p in self.folder.rglob('*.pdf')}
        self.assertEqual(self.db.reset_downloads(self.client.id),2)
        self.assertEqual(self.db.pdf_stats(self.client.id),dict(total=2,saved=0,failed=0,pending=0))
        self.assertTrue(all(r['pdf_state'] is None for r in self.db.download_candidates(self.client.id)))
        self.assertEqual(self.db.checkpoint(self.client.id),before)
        self.assertEqual(self.db.client(self.client.id),self.client)
        self.assertEqual(len(self.db.pending_candidates(other.id)),1)
        self.assertEqual({p:p.read_bytes() for p in files},files)
        reopened=Database(self.db.path)
        self.assertEqual(reopened.count(self.client.id,bucket='success'),0)
        result=PDFDownloader(reopened,helpers.Opener(self.pdf)).run(self.client,self.desktop,threading.Event())
        self.assertEqual((result['saved'],result['skipped']),(2,0))
        self.assertEqual(len(list(self.folder.rglob('*.pdf'))),4)
        self.assertEqual({p:p.read_bytes() for p in files},files)

    def test_reset_invalidates_historical_sources_and_known_challenges(self):
        original=self.db.invoice(self.client.id,101)['download_url']
        changed='https://provider.example/changed'
        self.db.record_pdf(self.client.id,101,'challenge',hashlib.sha256(changed.encode()).hexdigest())
        with self.db.connection() as conn:
            conn.execute('UPDATE invoices SET download_url=? WHERE mark=101',(changed,))
        self.db.reset_downloads(self.client.id)
        for link in (changed,original):
            with self.db.connection() as conn:
                conn.execute('UPDATE invoices SET download_url=? WHERE mark=101',(link,))
            self.assertIsNone(self.db.saved_pdf(self.client.id,101,hashlib.sha256(link.encode()).hexdigest()))
            self.assertIsNone(next(r for r in self.db.invoices(self.client.id) if r['mark']==101)['pdf_state'])
        self.assertEqual(self.db.pending_candidates(self.client.id),[])

    def test_reset_rolls_back_all_barriers_on_event_failure(self):
        with self.db.connection() as conn:
            count=conn.execute('SELECT count(*) FROM pdf_attempts').fetchone()[0]
            conn.execute("""CREATE TRIGGER fail_reset BEFORE INSERT ON events
              WHEN NEW.kind='pdf_downloads_reset' BEGIN SELECT RAISE(ABORT,'synthetic'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.reset_downloads(self.client.id)
        self.assertEqual(self.db.count(self.client.id,bucket='success'),2)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM pdf_attempts').fetchone()[0],count)

    def test_trash_only_selected_folder_then_reset(self):
        other=self.desktop/'222222222'
        other.mkdir();(other/'keep.txt').write_text('keep')
        recycle=self.root/'Recycle'
        def trash(path):
            self.assertEqual(Path(path),self.folder)
            shutil.move(path,recycle)
        folder,moved,count=trash_client_archive(self.db,self.client,self.desktop,trash=trash)
        self.assertEqual((folder,moved,count),(self.folder,True,2))
        self.assertFalse(folder.exists())
        self.assertEqual(len(list(recycle.rglob('*.pdf'))),2)
        self.assertEqual((other/'keep.txt').read_text(),'keep')
        self.assertEqual(self.db.count(self.client.id,bucket='success'),0)
        self.assertEqual(self.db.client(self.client.id),self.client)

    def test_trash_failure_keeps_state_and_has_no_permanent_fallback(self):
        with patch.object(self.db,'reset_downloads') as reset:
            with self.assertRaises(ApplicationError) as caught:
                trash_client_archive(self.db,self.client,self.desktop,trash=Mock(side_effect=OSError('private token')))
            reset.assert_not_called()
        self.assertNotIn('private',str(caught.exception))
        self.assertEqual(self.db.count(self.client.id,bucket='success'),2)
        self.assertEqual(len(list(self.folder.rglob('*.pdf'))),2)

    def test_symlink_target_is_rejected_without_touching_other_folder(self):
        alias=self.root/'alias-root';alias.mkdir()
        (alias/self.folder.name).symlink_to(self.folder,target_is_directory=True)
        trash=Mock()
        with self.assertRaises(ApplicationError):
            trash_client_archive(self.db,self.client,alias,trash=trash)
        trash.assert_not_called()
        self.assertTrue(self.folder.is_dir())

    def test_absent_folder_only_resets_selected_client(self):
        root=self.root/'Empty'
        trash=Mock()
        result=trash_client_archive(self.db,self.client,root,trash=trash)
        self.assertFalse(result[1])
        trash.assert_not_called()
        self.assertEqual(self.db.count(self.client.id,bucket='success'),0)
