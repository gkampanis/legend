"""Read-only Windows UIA inspection. No focus, input, invoke or Save methods."""
from contextlib import contextmanager
from dataclasses import dataclass,asdict
import os
import sys
import time
from ..models import ApplicationError

LABELS={'Πιστωτής','Α.Φ.Μ.','ΑΦΜ','Εναλ. παραστατικό','Σχετικά παρ/κά','Ημ. έκδοσης','Αποθήκευση',
        'ΤΕΔ','Δαπάνη','Λογ/σμός Γ.Λ.','Καθαρή αξία','Αξία ΦΠΑ','ΣΥΝΟΛΟ','Ακύρωση'}


@dataclass(frozen=True)
class Window:
    handle:int
    process_id:int
    title:str


@contextmanager
def _desktop():
    if sys.platform!='win32':raise ApplicationError('Η ανίχνευση πραγματικού Entersoft απαιτεί Windows. Η τοπική προετοιμασία παραμένει διαθέσιμη.')
    try:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            from pywinauto import Desktop
            yield Desktop(backend='uia')
        finally:pythoncom.CoUninitialize()
    except ImportError:
        raise ApplicationError('Εγκαταστήστε την προαιρετική υποστήριξη από INSTALL_ENTERSOFT.bat στα Windows.') from None
    except ApplicationError:raise
    except Exception:
        raise ApplicationError('Δεν ολοκληρώθηκε η read-only ανίχνευση UI Automation. Ελέγξτε δικαιώματα/διαθεσιμότητα EBS.') from None


def windows(process_id=None):
    with _desktop() as desktop:
        results=[]
        for wrapper in desktop.windows(visible_only=True,**({'process':process_id} if process_id else {})):
            info=wrapper.element_info;title=info.name or ''
            if info.process_id!=os.getpid() and (process_id==info.process_id if process_id else any(word in title.casefold() for word in ('entersoft','ebs erp'))):
                results.append(Window(int(wrapper.handle),int(info.process_id),title))
        return results


def inspect(window,*,limit=300,seconds=5):
    with _desktop() as desktop:
        wrapper=desktop.window(handle=window.handle).wrapper_object()
        info=wrapper.element_info
        if info.process_id!=window.process_id or info.process_id==os.getpid() or info.name!=window.title:
            raise ApplicationError('Το επιλεγμένο παράθυρο άλλαξε. Κάντε νέα ανίχνευση.')
        result=[];queue=[(wrapper,0)];deadline=time.monotonic()+seconds
        while queue and len(result)<limit and time.monotonic()<deadline:
            node,depth=queue.pop(0);item=node.element_info
            if getattr(item,'is_password',False):continue
            # Never read ValuePattern/text contents or copy arbitrary customer text.
            name=(item.name or '').strip().rstrip(':').strip()
            result.append(dict(control_type=item.control_type,automation_id=item.automation_id or '',
                               class_name=item.class_name or '',label=name if name in LABELS else '',depth=depth))
            if depth<7:queue.extend((child,depth+1) for child in node.children())
        return dict(stage='read_only',handle=window.handle,process_id=window.process_id,
                    controls=result,truncated=bool(queue),fields_verified=False,save_verified=False)
