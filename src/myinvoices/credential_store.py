"""Client-bound secrets: Windows DPAPI at rest, session-only elsewhere."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import uuid

from .models import ApplicationError, Credentials


class WindowsDPAPI:
    def __init__(self):
        class Blob(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
        self.Blob = Blob
        self.crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.crypt.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR,
            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        self.crypt.CryptProtectData.restype = wintypes.BOOL
        self.crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        self.crypt.CryptUnprotectData.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def _apply(self, data, protect):
        buffer = ctypes.create_string_buffer(data)
        source = self.Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = self.Blob()
        try:
            function = self.crypt.CryptProtectData if protect else self.crypt.CryptUnprotectData
            description = "myInvoicesStarter" if protect else None
            # CRYPTPROTECT_UI_FORBIDDEN; default protection scope is the current Windows user.
            if not function(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(result)):
                raise ApplicationError("Αποτυχία ασφαλούς αποθήκευσης/ανάγνωσης credentials στα Windows.")
            return ctypes.string_at(result.data, result.size)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if result.data:
                ctypes.memset(result.data, 0, result.size)
                self.kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))

    def encrypt(self, data):
        return self._apply(data, True)

    def decrypt(self, data):
        return self._apply(data, False)


class CredentialStore:
    def __init__(self, directory, backend=None):
        self.directory = Path(directory)
        self.backend = backend
        self._session = {}

    @property
    def persistent(self):
        return self.backend is not None

    def _path(self, client):
        try:
            identifier = str(uuid.UUID(client.id))
        except (ValueError, AttributeError):
            raise ApplicationError("Μη έγκυρη ταυτότητα πελάτη.") from None
        return self.directory / (identifier + ".dpapi")

    @staticmethod
    def _binding(client):
        return client.id, client.vat, client.environment

    def get(self, client):
        path = self._path(client)
        if self.backend is None:
            return self._session.get(self._binding(client))
        if not path.exists():
            return None
        try:
            payload = json.loads(self.backend.decrypt(path.read_bytes()))
            if (payload["client_id"], payload["vat"], payload["environment"]) != self._binding(client):
                raise ApplicationError("Τα αποθηκευμένα credentials δεν αντιστοιχούν σε αυτόν τον πελάτη.")
            return Credentials(payload["user_id"], payload["subscription_key"], payload.get("entity_vat"))
        except ApplicationError:
            raise
        except Exception:
            raise ApplicationError("Δεν διαβάστηκαν τα credentials. Ορίστε τα ξανά στα στοιχεία πελάτη.") from None

    def set(self, client, credentials):
        path = self._path(client)
        if credentials.entity_vat and credentials.entity_vat != client.vat:
            raise ApplicationError("Το ΑΦΜ εκπροσώπησης δεν συμφωνεί με τον πελάτη.")
        if self.backend is None:
            self._session[self._binding(client)] = credentials
            return
        payload = json.dumps({"client_id": client.id, "vat": client.vat,
            "environment": client.environment, "user_id": credentials.user_id,
            "subscription_key": credentials.subscription_key, "entity_vat": credentials.entity_vat}).encode()
        try:
            encrypted = self.backend.encrypt(payload)
            self.directory.mkdir(parents=True, exist_ok=True)
            # Only ciphertext ever enters a file, including the temporary file.
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as file:
                temporary = Path(file.name)
                try:
                    file.write(encrypted)
                    file.flush()
                    os.fsync(file.fileno())
                except Exception:
                    file.close()
                    temporary.unlink(missing_ok=True)
                    raise
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except Exception:
            raise ApplicationError("Δεν αποθηκεύτηκαν τα credentials. Ελέγξτε τα στοιχεία πελάτη και δοκιμάστε ξανά.") from None

    def clear(self):
        self._session.clear()


def default_store(directory):
    return CredentialStore(directory, WindowsDPAPI() if os.name == "nt" else None)
