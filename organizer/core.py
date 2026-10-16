from __future__ import annotations

import json
import os
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TYPES = {
    'Excel': '.xls .xlsx .xlsm .xlsb .xlt .xltx .csv .ods',
    'Word': '.doc .docx .docm .dot .dotx .odt .rtf',
    'PDF': '.pdf',
    'PowerPoint': '.ppt .pptx .pptm .pps .ppsx .odp',
    'Εικόνες': '.jpg .jpeg .png .gif .bmp .tif .tiff .webp .heic .svg .raw .ico',
    'Βίντεο': '.mp4 .mkv .avi .mov .wmv .webm .m4v .mpeg .mpg',
    'Ήχος': '.mp3 .wav .flac .aac .ogg .m4a .wma .opus',
    'Συμπιεσμένα': '.zip .rar .7z .tar .gz .bz2 .xz .tgz',
}
EXTENSIONS = {ext: kind for kind, extensions in TYPES.items() for ext in extensions.split()}
SYSTEM = {'windows', 'program files', 'program files (x86)', 'programdata',
          '$recycle.bin', 'system volume information', 'recovery', 'config.msi',
          'appdata', '$windows.~bt', '$windows.~ws'}


def linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def safe_path(path: Path) -> bool:
    """Reject symlinks/junctions in every existing path component."""
    for part in (path, *path.parents):
        try:
            if linked(part):
                return False
        except FileNotFoundError:
            continue
    return True


@dataclass(frozen=True)
class Entry:
    source: Path
    target: Path
    size: int
    modified_ns: int


@dataclass
class Plan:
    destination: Path
    entries: list[Entry]
    warnings: list[str]
    cancelled: bool = False


def unique(path: Path, reserved: set[str]) -> Path:
    candidate, number = path, 1
    while str(candidate).casefold() in reserved or candidate.exists():
        candidate = path.with_name(f'{path.stem} ({number}){path.suffix}')
        number += 1
    reserved.add(str(candidate).casefold())
    return candidate


def scan(roots, destination, cancel=None, progress=lambda count: None) -> Plan:
    cancel = cancel or threading.Event()
    destination = Path(os.path.abspath(destination))
    if not safe_path(destination):
        raise ValueError('Ο προορισμός περιέχει συμβολικό σύνδεσμο ή junction.')
    plan = Plan(destination, [], [])
    reserved, seen = set(), set()
    for root in roots:
        root = Path(os.path.abspath(root))
        if not safe_path(root) or any(p.name.casefold() in SYSTEM for p in (root, *root.parents)):
            plan.warnings.append(f'Εξαιρέθηκε: {root}')
            continue
        stack = [root]
        while stack:
            if cancel.is_set():
                plan.cancelled = True
                return plan
            directory = stack.pop()
            if inside(directory, destination) or str(directory).casefold() in seen:
                continue
            seen.add(str(directory).casefold())
            try:
                with os.scandir(directory) as children:
                    for child in children:
                        if cancel.is_set():
                            plan.cancelled = True
                            return plan
                        path = Path(child.path)
                        try:
                            if linked(path):
                                continue
                            if child.is_dir(follow_symlinks=False):
                                if path.name.casefold() not in SYSTEM:
                                    stack.append(path)
                            elif child.is_file(follow_symlinks=False):
                                info = child.stat(follow_symlinks=False)
                                year = str(datetime.fromtimestamp(info.st_mtime).year)
                                category = EXTENSIONS.get(path.suffix.lower(), 'Λοιπά')
                                target = unique(destination / category / year / path.name, reserved)
                                plan.entries.append(Entry(path, target, info.st_size, info.st_mtime_ns))
                                if len(plan.entries) % 100 == 0:
                                    progress(len(plan.entries))
                        except (OSError, ValueError, OverflowError) as exc:
                            plan.warnings.append(f'{path}: {exc}')
            except OSError as exc:
                plan.warnings.append(f'{directory}: {exc}')
    progress(len(plan.entries))
    return plan


def execute(plan: Plan, mode: str, log_path: Path, cancel=None, progress=lambda *args: None):
    if mode not in ('copy', 'move') or plan.cancelled:
        raise ValueError('Μη έγκυρο σχέδιο ή λειτουργία.')
    cancel = cancel or threading.Event()
    results = {'completed': 0, 'failed': 0, 'cancelled': False}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as log:
        for index, entry in enumerate(plan.entries):
            if cancel.is_set():
                results['cancelled'] = True
                break
            temporary = None
            claimed = False
            published = False
            record = {'time': datetime.now().isoformat(), 'mode': mode,
                      'source': str(entry.source), 'target': str(entry.target)}
            try:
                if not inside(entry.target, plan.destination) or not safe_path(entry.source) or not safe_path(entry.target):
                    raise ValueError('Μη ασφαλής διαδρομή.')
                info = entry.source.stat()
                if info.st_size != entry.size or info.st_mtime_ns != entry.modified_ns:
                    raise ValueError('Το αρχείο άλλαξε μετά την προεπισκόπηση. Απαιτείται νέα σάρωση.')
                entry.target.parent.mkdir(parents=True, exist_ok=True)
                temporary = entry.target.parent / f'.organizer-{uuid.uuid4().hex}.tmp'
                with entry.source.open('rb') as source, temporary.open('xb') as output:
                    while chunk := source.read(1024 * 1024):
                        if cancel.is_set():
                            raise InterruptedError('Ακύρωση')
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                after = entry.source.stat()
                if after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns:
                    raise ValueError('Το αρχείο άλλαξε κατά την αντιγραφή.')
                os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
                # Exclusive creation prevents overwriting a file created after preview.
                with entry.target.open('xb'):
                    claimed = True
                os.replace(temporary, entry.target)
                published = True
                if mode == 'move':
                    entry.source.unlink()
                results['completed'] += 1
                record['status'] = 'ok'
            except InterruptedError as exc:
                results['cancelled'] = True
                record.update(status='cancelled', error=str(exc))
            except (OSError, ValueError) as exc:
                results['failed'] += 1
                record.update(status='error', error=str(exc), copy_retained=published)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
                if claimed and not published:
                    entry.target.unlink(missing_ok=True)
            log.write(json.dumps(record, ensure_ascii=False) + '\n')
            log.flush()
            progress(index + 1, len(plan.entries), record)
            if results['cancelled']:
                break
    return results
