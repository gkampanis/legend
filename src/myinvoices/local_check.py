"""Use this PC's saved client credentials; no cloud secrets are required."""

import argparse
from datetime import date
from pathlib import Path

from .__main__ import data_directory
from .api import Environment, RequestDocsAPI
from .credential_store import default_store
from .database import Database
from .models import ApplicationError
from .xml_parser import read_envelope


def check_client(database, store, client_id, api_factory=RequestDocsAPI):
    client = database.client(client_id)
    credentials = store.get(client)
    if credentials is None:
        raise ApplicationError("Δεν βρέθηκαν αποθηκευμένα credentials για αυτόν τον πελάτη. Ορίστε τα τοπικά από Στοιχεία πελάτη.")
    api = api_factory(credentials, Environment[client.environment.upper()])
    today = date.today()
    body = api.fetch(0, date_from=today, date_to=today)
    read_envelope(body)
    return ("ΕΠΙΤΥΧΙΑ: HTTP 200 και έγκυρος φάκελος RequestedDoc. "
            "Η ταυτοποίηση έγινε δεκτή. Δεν αποθηκεύτηκαν παραστατικά ή πρόοδος συγχρονισμού.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local read-only AADE connection check with saved client credentials")
    parser.add_argument("--db", type=Path,
                        default=data_directory() / "myinvoices.sqlite3")
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print("Δεν βρέθηκε η τοπική βάση κανονικής λειτουργίας. Ανοίξτε START_MYINVOICES.bat και αποθηκεύστε τον πελάτη και τα API στοιχεία του.")
        return 2
    try:
        database = Database(args.db)
        clients = database.clients()
        if not clients:
            print("Δεν υπάρχουν πελάτες στην κανονική λειτουργία. Ανοίξτε START_MYINVOICES.bat και προσθέστε πελάτη.")
            return 2
        print("ΤΟΠΙΚΟΣ ΕΛΕΓΧΟΣ ΑΑΔΕ — δεν απαιτεί μυστικά cloud ούτε νέα εισαγωγή κλειδιών.")
        print("Θα σταλεί ένα αναγνωστικό RequestDocs για σήμερα. Δεν εμφανίζονται κλειδιά ή παραστατικά.")
        for index, client in enumerate(clients, 1):
            label = "ΠΑΡΑΓΩΓΗ" if client.environment == "production" else "ΔΟΚΙΜΑΣΤΙΚΟ"
            print(f"{index}. {client.name} — {label}")
        choice = input("Αριθμός πελάτη [1] — Enter για συνέχεια, Ctrl+C για ακύρωση: ").strip() or "1"
        if not choice.isascii() or not choice.isdigit() or not 1 <= int(choice) <= len(clients):
            raise ApplicationError("Μη έγκυρος αριθμός πελάτη. Δεν έγινε αίτημα.")
        store = default_store(args.db.parent / "credentials")
        print(check_client(database, store, clients[int(choice) - 1].id))
        return 0
    except ApplicationError as error:
        print(f"ΑΠΟΤΥΧΙΑ: {error}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Ακυρώθηκε ο έλεγχος.")
        return 2
    except Exception:
        print("ΤΟΠΙΚΟ ΣΦΑΛΜΑ: δεν ολοκληρώθηκε ο έλεγχος εφαρμογής/βάσης. Δεν εμφανίζονται ευαίσθητες λεπτομέρειες.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
