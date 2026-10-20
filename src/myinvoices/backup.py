"""Local, consistent metadata backups and optional copies of recorded PDFs.

Restore preview validates a temporary copy. This module never replaces a live
application database or restores credentials/browser profiles.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import uuid

from . import __version__
from .models import ApplicationError

TABLES=('clients','invoices','pdf_attempts','checkpoints','period_checkpoints','document_changes','events')
MAX_FILE=1024*1024*1024


def digest(path):
    with Path(path).open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def check_database(path):
    try:
        with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as conn:
            if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError()
            schema=conn.execute('PRAGMA user_version').fetchone()[0]
            if schema!=4:raise ValueError()
            found={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(TABLES)<=found:raise ValueError()
            if conn.execute('PRAGMA foreign_key_check').fetchone():raise ValueError()
            return schema,{table:conn.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in TABLES}
    except (sqlite3.Error,OSError,ValueError):
        raise ApplicationError('Το αντίγραφο δεν περιέχει έγκυρη βάση schema 4.') from None


def _copy(source,destination,cancel,expected=None,max_bytes=MAX_FILE):
    size=0;sha=hashlib.sha256()
    with Path(source).open('rb') as src,Path(destination).open('xb') as out:
        for chunk in iter(lambda:src.read(1024*1024),b''):
            if cancel.is_set():raise ApplicationError('Το backup διακόπηκε. Τα τρέχοντα δεδομένα διατηρούνται.')
            size+=len(chunk)
            if size>max_bytes:raise ApplicationError('Το αρχείο υπερβαίνει το όριο του backup.')
            sha.update(chunk);out.write(chunk)
        out.flush();os.fsync(out.fileno())
    checksum=sha.hexdigest()
    if expected and (checksum!=expected[0] or size!=expected[1]):
        raise ApplicationError('Το καταγεγραμμένο PDF λείπει ή έχει αλλάξει.')
    return checksum,size


def create_backup(database,destination,*,include_pdfs=False,cancel,progress=lambda text:None):
    """Publish a new directory only after database/files/manifest validate."""
    parent=Path(destination).resolve()
    if not parent.is_dir():raise ApplicationError('Επιλέξτε υπάρχοντα φάκελο backup.')
    workspace=Path(tempfile.mkdtemp(prefix='.myinvoices-backup-partial-',dir=parent))
    published=None
    try:
        dbfile=workspace/'metadata.sqlite3'
        progress('Συνεπές αντίγραφο βάσης SQLite')
        with database.connection() as source,sqlite3.connect(dbfile) as target:
            def copying(status,remaining,total):
                if cancel.is_set():raise ApplicationError('Το backup διακόπηκε. Η βάση διατηρείται.')
            source.backup(target,pages=256,progress=copying)
        if cancel.is_set():raise ApplicationError('Το backup διακόπηκε.')
        schema,counts=check_database(dbfile)
        entries=[dict(path='metadata.sqlite3',sha256=digest(dbfile),size=dbfile.stat().st_size,kind='database')]
        issues=[];requested=0
        if include_pdfs:
            with sqlite3.connect(dbfile) as conn:
                sources=conn.execute("SELECT DISTINCT path,sha256,byte_count FROM pdf_attempts WHERE state IN ('saved_unverified','printed_unverified','imported_unverified') AND path IS NOT NULL").fetchall()
            requested=len(sources);(workspace/'pdfs').mkdir()
            for index,(path,checksum,size) in enumerate(sources):
                if cancel.is_set():raise ApplicationError('Το backup διακόπηκε.')
                progress(f'Αντιγραφή καταγεγραμμένων PDF {index+1} / {len(sources)}')
                relative=f'pdfs/{index+1:08d}.pdf';output=workspace/relative
                try:
                    source=Path(path)
                    if source.is_symlink() or not source.is_file() or getattr(source.stat(),'st_file_attributes',0)&0x400:raise OSError()
                    sha,nbytes=_copy(source,output,cancel,(checksum,size),40*1024*1024)
                    entries.append(dict(path=relative,sha256=sha,size=nbytes,kind='pdf',original_path=str(source)))
                except (OSError,ApplicationError):
                    output.unlink(missing_ok=True)
                    if cancel.is_set():raise ApplicationError('Το backup διακόπηκε.') from None
                    issues.append(dict(original_path=str(path),reason='missing_changed_or_unreadable'))
        manifest=dict(format=1,application_version=__version__,schema=schema,created_at=datetime.now(timezone.utc).isoformat(),
            counts=counts,includes_pdfs=include_pdfs,requested_pdfs=requested,credentials_included=False,browser_profiles_included=False,
            status='partial_pdf_copy' if issues else 'complete',files=entries,issues=issues)
        (workspace/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        validate_backup(workspace,cancel=cancel)
        # UUID destination: never target an existing backup or archive folder.
        published=parent/('myInvoices-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:10])
        if published.exists():raise ApplicationError('Το όνομα backup υπάρχει ήδη.')
        workspace.rename(published)
        return published,manifest
    except (OSError,sqlite3.Error):
        raise ApplicationError('Δεν ολοκληρώθηκε το backup. Ελέγξτε χώρο και δικαιώματα. Η αρχική βάση διατηρείται.') from None
    finally:
        # Only this invocation's unpublished temporary directory is removed.
        if workspace.exists():shutil.rmtree(workspace)


def validate_backup(directory,*,cancel,progress=lambda text:None):
    """Reject unsafe paths/corruption, then restore metadata to a temporary DB."""
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():raise ApplicationError('Μη έγκυρος φάκελος backup.')
    try:
        path=root/'manifest.json'
        if path.is_symlink() or path.stat().st_size>10*1024*1024:raise ValueError()
        manifest=json.loads(path.read_text(encoding='utf-8'))
        if manifest.get('format')!=1 or manifest.get('schema')!=4 or manifest.get('credentials_included') is not False or manifest.get('browser_profiles_included') is not False:raise ValueError()
        if not isinstance(manifest.get('created_at'),str) or not isinstance(manifest.get('application_version'),str) or not isinstance(manifest.get('issues'),list) or not isinstance(manifest.get('includes_pdfs'),bool) or manifest.get('status') not in ('complete','partial_pdf_copy'):
            raise ValueError()
        entries=manifest['files']
        if not isinstance(entries,list) or not entries or len(entries)>100000:raise ValueError()
        names=set();total=0;metadata=0
        for entry in entries:
            if cancel.is_set():raise ApplicationError('Ο έλεγχος backup διακόπηκε.')
            name=entry['path'];relative=PurePosixPath(name)
            if not isinstance(name,str) or relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or not relative.parts:raise ValueError()
            if name in names:raise ValueError()
            names.add(name);source=root.joinpath(*relative.parts)
            if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1,len(relative.parts)+1)):raise ValueError()
            size=entry['size']
            if not isinstance(size,int) or not 0<=size<=MAX_FILE or source.stat().st_size!=size:raise ValueError()
            if getattr(source.stat(),'st_file_attributes',0)&0x400:raise ValueError()
            if digest(source)!=entry['sha256']:raise ValueError()
            if entry['kind']=='database':
                if name!='metadata.sqlite3':raise ValueError()
                metadata+=1
            elif entry['kind']=='pdf':
                if not name.startswith('pdfs/') or size>40*1024*1024:raise ValueError()
            else:raise ValueError()
            total+=size
        if metadata!=1:raise ValueError()
        progress('Δοκιμαστική επαναφορά βάσης σε προσωρινό φάκελο')
        with tempfile.TemporaryDirectory(prefix='myinvoices-restore-check-') as temporary:
            restored=Path(temporary)/'restored.sqlite3'
            _copy(root/'metadata.sqlite3',restored,cancel)
            schema,counts=check_database(restored)
            if schema!=manifest['schema'] or counts!=manifest['counts']:raise ValueError()
        return dict(manifest=manifest,total_bytes=total,pdf_count=sum(e['kind']=='pdf' for e in entries),
                    metadata_valid=True,restored_to_temporary=True)
    except (OSError,ValueError,KeyError,TypeError,AttributeError,sqlite3.Error):
        raise ApplicationError('Το backup είναι ελλιπές, αλλοιωμένο ή μη υποστηριζόμενο. Τα τρέχοντα δεδομένα δεν άλλαξαν.') from None
