"""Allowlisted, atomic per-installation UI preferences; no secrets or URLs."""
import json
import os
from pathlib import Path
import tempfile

from .models import ApplicationError

COLUMNS = ('date','mark','supplier','identity','total','method','status','reason')


class UISettings:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            if self.path.stat().st_size > 100000:
                return {}
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return self.clean(value)
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def clean(value):
        if not isinstance(value, dict):
            return {}
        widths = {k: int(v) for k,v in value.get('widths',{}).items()
                  if k in COLUMNS and isinstance(v,int) and 60 <= v <= 900} if isinstance(value.get('widths'),dict) else {}
        order = value.get('order',list(COLUMNS))
        if not isinstance(order,list) or len(order)!=len(COLUMNS) or set(order)!=set(COLUMNS):
            order = list(COLUMNS)
        paths = {}
        if isinstance(value.get('destinations'),dict):
            import uuid
            for key, path in value['destinations'].items():
                try:
                    uuid.UUID(key)
                    if isinstance(path,str) and len(path)<4096 and Path(path).is_absolute() and Path(path).is_dir():
                        paths[key] = path
                except (OSError,ValueError,TypeError):
                    pass
        return dict(widths=widths,order=order,destinations=paths)

    def save(self, value):
        data = self.clean(value)
        temporary = None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent,delete=False,mode='w',encoding='utf-8') as file:
                temporary = Path(file.name)
                json.dump(data,file,ensure_ascii=False)
                file.flush();os.fsync(file.fileno())
            os.replace(temporary,self.path)
        except OSError:
            raise ApplicationError('Δεν αποθηκεύτηκαν οι προτιμήσεις οθόνης. Τα παραστατικά διατηρούνται.') from None
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
