"""Sequential Stage A runner. No desktop mutations, API calls or PDF downloads."""
from collections import Counter
from dataclasses import dataclass,field
import threading
from .domain import State,validate_invoice
from ..models import ApplicationError


class Control:
    def __init__(self):self.condition=threading.Condition();self.paused=False;self.stopped=False
    def pause(self):
        with self.condition:self.paused=True;self.condition.notify_all()
    def resume(self):
        with self.condition:self.paused=False;self.condition.notify_all()
    def stop(self):
        with self.condition:self.stopped=True;self.condition.notify_all()
    def ready(self,on_pause=lambda:None):
        with self.condition:
            reported=False
            while self.paused and not self.stopped:
                if not reported:on_pause();reported=True
                self.condition.wait(timeout=.1)
            return not self.stopped


@dataclass(frozen=True)
class Progress:
    total:int
    processed:int
    counts:dict=field(default_factory=dict)
    mark:int|None=None
    supplier:str=''
    number:str=''
    paused:bool=False


class Preparer:
    def __init__(self,source,store):self.source,self.store=source,store
    def run(self,scope,marks,control,progress=lambda event:None):
        # Duplicate MARK selections collapse deterministically, preserving order.
        client=self.source.client
        if (scope.client_id,scope.client_vat,scope.environment)!=(client.id,client.vat,client.environment):
            raise ApplicationError('Το πλαίσιο Entersoft δεν συμφωνεί με τον πελάτη της πηγής.')
        marks=tuple(dict.fromkeys(int(mark) for mark in marks));counts=Counter();processed=0
        self.store.recover_interrupted(scope)
        for mark in marks:
            if not control.ready(lambda:progress(Progress(len(marks),processed,dict(counts),paused=True))):break
            row={'mark':mark}
            try:
                row=self.source.invoice(mark)
                progress(Progress(len(marks),processed,dict(counts),mark,row.get('issuer_vat') or '',row.get('number') or ''))
                mapping=self.store.mapping(scope,row.get('issuer_vat'),mark)
                preparation=validate_invoice(row,scope,mapping)
                # Pause/stop before committing this preparation, not an EBS Save.
                if not control.ready(lambda:progress(Progress(len(marks),processed,dict(counts),mark,paused=True))):break
                state,_=self.store.prepare(scope,preparation)
            except Exception:
                self.store.fail(scope,row)
                state=State.FAILED.value
            counts[state]+=1;processed+=1
            progress(Progress(len(marks),processed,dict(counts),mark,row.get('issuer_vat') or '',row.get('number') or ''))
        return dict(total=len(marks),processed=processed,remaining=len(marks)-processed,counts=dict(counts),stopped=control.stopped,ebs_writes=0)
