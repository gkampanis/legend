"""Read-only RequestDocs adapter. Credentials never follow redirects."""

from enum import Enum
from datetime import date
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import ApplicationError, Credentials
from .xml_parser import MAX_RESPONSE_BYTES


class Environment(Enum):
    DEVELOPMENT = "https://mydataapidev.aade.gr"
    PRODUCTION = "https://mydatapi.aade.gr/myDATA"


class APIError(ApplicationError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RequestDocsAPI:
    def __init__(self, credentials: Credentials, environment=Environment.DEVELOPMENT,
                 timeout=30, opener=None):
        if not isinstance(environment, Environment):
            raise ApplicationError("Μη υποστηριζόμενο περιβάλλον ΑΑΔΕ.")
        if not 0 < timeout <= 120:
            raise ApplicationError("Μη έγκυρο χρονικό όριο σύνδεσης.")
        self.credentials = credentials
        self.environment = environment
        self.timeout = timeout
        self.opener = opener or build_opener(NoRedirect())

    def fetch(self, mark: int, continuation=None, *, date_from=None, date_to=None) -> bytes:
        if mark < 0:
            raise ApplicationError("Μη έγκυρο σημείο συγχρονισμού.")
        query = {"mark": str(mark)}
        for name, value in (("dateFrom", date_from), ("dateTo", date_to)):
            if value is not None:
                if not isinstance(value, date):
                    raise ApplicationError("Μη έγκυρη ημερομηνία αιτήματος.")
                query[name] = value.strftime("%d/%m/%Y")
        if date_from and date_to and date_from > date_to:
            raise ApplicationError("Μη έγκυρη περίοδος αιτήματος.")
        if self.credentials.entity_vat:
            query["entityVatNumber"] = self.credentials.entity_vat
        if continuation is not None:
            if len(continuation) != 2 or not all(continuation):
                raise ApplicationError("Μη έγκυρο continuation token.")
            query.update(zip(("nextPartitionKey", "nextRowKey"), continuation))
        request = Request(
            self.environment.value + "/RequestDocs?" + urlencode(query),
            headers={"aade-user-id": self.credentials.user_id,
                     "Ocp-Apim-Subscription-Key": self.credentials.subscription_key,
                     "Accept": "application/xml"}, method="GET",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = response.status
                if status != 200:
                    raise APIError("Αποτυχία HTTP από την ΑΑΔΕ.", status)
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise APIError("Υπερβολικά μεγάλη απόκριση ΑΑΔΕ.")
                return body
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 401:
                message = "HTTP 401: απορρίφθηκε η ταυτοποίηση. Ελέγξτε αν τα κλειδιά είναι παραγωγής ή δοκιμαστικού και αν επιλέχθηκε το αντίστοιχο περιβάλλον."
            elif status == 403:
                message = "HTTP 403: απαγορεύτηκε η πρόσβαση. Ελέγξτε δικαιώματα εκπροσώπησης/ΑΦΜ, περιβάλλον και περιορισμούς δικτύου. Αυτό δεν αποδεικνύει λανθασμένα credentials."
            elif status == 429:
                message = "Όριο αιτημάτων ΑΑΔΕ. Δοκιμάστε αργότερα· η πρόοδος διατηρείται."
            elif 300 <= status < 400:
                message = "Η ανακατεύθυνση αποκλείστηκε για προστασία των credentials."
            else:
                message = "Σφάλμα HTTP από την ΑΑΔΕ. Η πρόοδος διατηρείται."
            label = "παραγωγή" if self.environment is Environment.PRODUCTION else "δοκιμαστικό"
            raise APIError(f"{message} Περιβάλλον: {label}.", status) from None
        except (TimeoutError, socket.timeout):
            raise APIError("Έληξε το χρονικό όριο σύνδεσης. Δοκιμάστε ξανά.") from None
        except URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                raise APIError("Αποτυχία TLS/πιστοποιητικού. Ελέγξτε την ημερομηνία Windows και τα πιστοποιητικά του δικτύου, χωρίς απενεργοποίηση επαλήθευσης TLS.") from None
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise APIError("Έληξε το χρονικό όριο σύνδεσης. Δοκιμάστε ξανά.") from None
            raise APIError("Αποτυχία δικτύου/DNS ή proxy κατά τη σύνδεση στην ΑΑΔΕ.") from None
        except (OSError, ValueError):
            raise APIError("Αποτυχία ασφαλούς σύνδεσης με την ΑΑΔΕ.") from None
