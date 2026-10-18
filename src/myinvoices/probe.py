"""Explicit read-only production auth probe. Never writes or prints invoice data."""

from datetime import date
import os

from .api import Environment, RequestDocsAPI
from .models import ApplicationError, Credentials
from .xml_parser import read_envelope


def main():
    user = os.environ.get("MYINVOICES_AADE_USER_ID")
    key = os.environ.get("MYINVOICES_AADE_SUBSCRIPTION_KEY")
    if not user or not key:
        print("Δεν έγινε σύνδεση: λείπουν τα δύο ασφαλή πεδία MYINVOICES_AADE_USER_ID και MYINVOICES_AADE_SUBSCRIPTION_KEY.")
        return 2
    try:
        api = RequestDocsAPI(Credentials(user.strip(), key.strip()), Environment.PRODUCTION)
        today = date.today()
        body = api.fetch(0, date_from=today, date_to=today)
        read_envelope(body)
    except ApplicationError as error:
        print(f"Αποτυχία ελέγχου σύνδεσης: {error}")
        return 1
    print("Επιτυχία: HTTP 200 και RequestedDoc από την παραγωγική ΑΑΔΕ. Δεν αποθηκεύτηκαν ούτε εμφανίστηκαν παραστατικά.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
