"""Explicit per-client archive maintenance. No permanent deletion fallback."""
from pathlib import Path
import stat

from .local_files import client_archive_folder
from .models import ApplicationError


def removable_client_folder(root, client):
    if len(client.vat)!=9 or not client.vat.isascii() or not client.vat.isdigit():
        raise ApplicationError('Μη έγκυρο ΑΦΜ πελάτη για διαγραφή φακέλου.')
    root=Path(root).absolute()
    folder=client_archive_folder(root,client)
    try:
        if folder.parent.resolve()!=root.resolve() or folder.resolve()==root.resolve():
            raise ValueError()
        if folder.is_symlink():
            raise ValueError()
        if folder.exists():
            info=folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
                raise ValueError()  # A junction/reparse point may target another folder.
    except (OSError,ValueError,RuntimeError):
        raise ApplicationError('Ο φάκελος πελάτη δεν είναι κανονικός φάκελος. Δεν έγινε διαγραφή.') from None
    return folder


def trash_client_archive(database, client, root, trash=None):
    """Only the current client's folder, then reset that client's download state."""
    if database.client(client.id)!=client:
        raise ApplicationError('Ο πελάτης έχει αλλάξει. Ανανεώστε τη λίστα.')
    folder=removable_client_folder(root,client)
    try:
        moved=folder.exists()
        if moved:
            if trash is None:
                try:
                    from .recycle_bin import recycle_folder
                    import send2trash  # noqa: F401
                except ImportError:
                    raise ApplicationError('Τρέξτε INSTALL_BROWSER.bat για τη μεταφορά φακέλου στον Κάδο.') from None
                trash=recycle_folder
            trash(str(folder))
            if folder.exists():
                raise OSError()
    except ApplicationError:
        raise
    except ImportError:
        raise ApplicationError('Τρέξτε INSTALL_BROWSER.bat για την υποστήριξη Κάδου ανακύκλωσης.') from None
    except Exception:
        raise ApplicationError('Δεν μεταφέρθηκε ο φάκελος στον Κάδο. Οι λήψεις δεν μηδενίστηκαν.') from None
    count=database.reset_downloads(client.id)
    return folder,moved,count
