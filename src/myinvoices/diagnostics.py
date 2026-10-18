"""Fixed metadata only: no exception messages, locals, source lines or request data."""

from datetime import datetime, timezone
import json
from pathlib import Path
import traceback


def record_failure(path, error, stage):
    from .xml_parser import DIAGNOSTIC_ROOTS
    allowed_stages = {"local", "credentials", "request", "xml", "database", "gui"}
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
