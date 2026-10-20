import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from myinvoices.backup import create_backup,validate_backup
from myinvoices.models import ApplicationError
import test_pdf_downloads as helpers


class BackupTests(unittest.TestCase):
    def setUp(self):
        helpers.PDFTests.setUp(self)
        self.destination=self.root/'backups';self.destination.mkdir()

    def run_download(self,data):return helpers.PDFTests.run_download(self,data)

    def create(self,pdfs=False,cancel=None):
        return create_backup(self.db,self.destination,include_pdfs=pdfs,cancel=cancel or threading.Event())

    def validate(self,path):return validate_backup(path,cancel=threading.Event())

    def test_consistent_metadata_backup_and_temp_restore_preserve_live_database(self):
        before=self.db.path.read_bytes()
        path,manifest=self.create()
        self.assertFalse(manifest['includes_pdfs']);self.assertFalse(manifest['credentials_included'])
        self.assertEqual(manifest['counts']['invoices'],2)
        result=self.validate(path)
        self.assertTrue(result['restored_to_temporary']);self.assertEqual(result['pdf_count'],0)
        self.assertEqual(self.db.path.read_bytes(),before)
        self.assertEqual(len(list(path.iterdir())),2)

    def test_optional_pdf_copies_have_hashes_original_paths_and_no_credentials(self):
        self.run_download(self.pdf)
        (self.root/'credentials').mkdir();(self.root/'credentials'/'synthetic.dpapi').write_text('SYNTHETIC-SECRET')
        path,manifest=self.create(True)
        result=self.validate(path);self.assertEqual(result['pdf_count'],2)
        self.assertEqual(manifest['issues'],[])
        for entry in manifest['files']:
            self.assertEqual(hashlib.sha256((path/entry['path']).read_bytes()).hexdigest(),entry['sha256'])
        self.assertNotIn('SYNTHETIC-SECRET',''.join(p.read_text(errors='ignore') for p in path.rglob('*') if p.is_file()))
        for entry in manifest['files']:
            if entry['kind']=='pdf':self.assertTrue(Path(entry['original_path']).is_file())

    def test_missing_or_changed_pdf_produces_explicit_partial_backup(self):
        self.run_download(self.pdf)
        next((self.root/'output').rglob('*.pdf')).write_bytes(b'changed')
        path,manifest=self.create(True)
        self.assertEqual(manifest['status'],'partial_pdf_copy');self.assertEqual(len(manifest['issues']),1)
        self.assertEqual(self.validate(path)['pdf_count'],1)

    def test_corrupt_database_rejected_without_touching_live_data(self):
        path,_=self.create();before=self.db.path.read_bytes()
        (path/'metadata.sqlite3').write_bytes(b'corrupt')
        with self.assertRaises(ApplicationError):self.validate(path)
        self.assertEqual(self.db.path.read_bytes(),before)

    def test_schema_and_count_mismatch_rejected_even_with_recomputed_hash(self):
        path,manifest=self.create()
        manifest['counts']['invoices']+=1
        (path/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ApplicationError):self.validate(path)
        manifest['schema']=999
        (path/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ApplicationError):self.validate(path)

    def test_interrupted_creation_removes_only_own_partial_directory(self):
        existing=self.destination/'keep';existing.mkdir();(existing/'file').write_text('keep')
        cancel=threading.Event();cancel.set()
        with self.assertRaises(ApplicationError):self.create(cancel=cancel)
        self.assertEqual(list(self.destination.iterdir()),[existing])
        self.assertEqual((existing/'file').read_text(),'keep')

    def test_interrupted_manifest_and_path_traversal_are_rejected(self):
        path,manifest=self.create()
        (path/'manifest.json').write_text('{unfinished')
        with self.assertRaises(ApplicationError):self.validate(path)
        manifest['files'][0]['path']='../outside'
        (path/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ApplicationError):self.validate(path)

    def test_symlinked_file_is_never_followed(self):
        path,_=self.create();source=path/'metadata.sqlite3';copy=path/'outside.sqlite3'
        source.rename(copy)
        try:source.symlink_to(copy)
        except OSError:self.skipTest('Symlink unavailable')
        with self.assertRaises(ApplicationError):self.validate(path)

    def test_failure_during_copy_does_not_publish_incomplete_snapshot(self):
        self.run_download(self.pdf)
        from myinvoices import backup
        original=backup.validate_backup
        with patch.object(backup,'validate_backup',side_effect=ApplicationError('synthetic failure')):
            with self.assertRaises(ApplicationError):self.create(True)
        self.assertEqual(list(self.destination.iterdir()),[])
        self.assertEqual(self.db.count(self.client.id),2)

    def test_second_backup_does_not_overwrite_first(self):
        first,_=self.create();data=(first/'metadata.sqlite3').read_bytes()
        second,_=self.create();self.assertNotEqual(first,second)
        self.assertEqual((first/'metadata.sqlite3').read_bytes(),data)
