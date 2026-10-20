"""Fixed metadata only: no exception messages, locals, source lines or request data."""

from datetime import datetime, timezone
import json
from pathlib import Path
import traceback


def record_failure(path, error, stage):
    from .xml_parser import DIAGNOSTIC_ROOTS
    allowed_stages = {"local", "credentials", "request", "xml", "database", "gui", "pdf", "pdf_import", "backup", "export"}
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "stage": stage if stage in allowed_stages else "local",
        "exception_type": type(error).__name__ if type(error).__module__ in
                          {"builtins", "sqlite3", "_tkinter", "myinvoices.models", "myinvoices.api"} else "Error",
        "http_status": getattr(error, "status", None) if isinstance(getattr(error, "status", None), int) else None,
        "xml_root": getattr(error, "root_kind", None) if getattr(error, "root_kind", None) in DIAGNOSTIC_ROOTS | {"UNKNOWN"} else None,
        "aade_codes": [c for c in getattr(error, "service_codes", ())
                       if isinstance(c, str) and 0 < len(c) <= 6 and c.isascii() and c.isdigit()],
        "frames": [{"file": Path(f.filename).name, "function": f.name, "line": f.lineno}
                   for f in traceback.extract_tb(error.__traceback__)
                   if Path(f.filename).parent.name == "myinvoices"],
    }
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True


def export_report(directory,destination):
    """Explicit support export: fixed metadata, no invoice/customer/path data."""
    import platform
    import sys
    from . import __version__
    records=[]
    try:
        with (Path(directory)/'diagnostics.jsonl').open('rb') as file:
            file.seek(0,2);size=file.tell();file.seek(max(0,size-256*1024))
            for line in file.read().decode('utf-8',errors='replace').splitlines():
                try:
                    source=json.loads(line)
                    # Never copy arbitrary fields or exception text from an edited log.
                    stage=source.get('stage')
                    if stage not in {'local','credentials','request','xml','database','gui','pdf','pdf_import','backup','export'}:continue
                    record={'stage':stage}
                    status=source.get('http_status')
                    if isinstance(status,int) and 100<=status<=599:record['http_status']=status
                    records.append(record)
                except (ValueError,AttributeError):continue
    except OSError:pass
    report=dict(application_version=__version__,python=f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
                operating_system=platform.system(),diagnostics=records[-100:])
    from .models import ApplicationError
    try:
        with Path(destination).open('x',encoding='utf-8') as file:json.dump(report,file,ensure_ascii=False,indent=2)
    except OSError:
        raise ApplicationError('Δεν γράφτηκε η τεχνική αναφορά. Επιλέξτε νέο όνομα αρχείου.') from None
    return len(report['diagnostics'])
