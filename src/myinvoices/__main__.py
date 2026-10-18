import argparse
import os
from pathlib import Path
import tempfile

from .database import Database
from .demo import DEMO_VAT, DemoAPI
from .sync import Synchronizer


def data_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "myInvoicesStarter"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "myInvoicesStarter"


def prepare_demo(database):
    return next((c for c in database.clients() if c.vat == DEMO_VAT and c.environment == "development"), None) or database.add_client("Συνθετικός πελάτης DEMO", DEMO_VAT)


def main(argv=None):
    parser = argparse.ArgumentParser(description="myInvoices Starter — normal client configuration by default")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Explicit offline demonstration with synthetic data")
    mode.add_argument("--live", action="store_true", help="Enable experimental real AADE requests after GUI confirmation")
    parser.add_argument("--db", type=Path, help="Separate local database path; never point at legacy data")
    parser.add_argument("--check-demo", action="store_true", help="Run repeatable offline smoke check without GUI")
    args = parser.parse_args(argv)
    if args.check_demo:
        if args.live:
            parser.error("--check-demo cannot be used with --live")
        with tempfile.TemporaryDirectory(prefix="myinvoices-demo-") as directory:
            database = Database(args.db or Path(directory) / "demo.sqlite3")
            client = prepare_demo(database)
            Synchronizer(database, DemoAPI()).run(client.id)
            Synchronizer(database, DemoAPI()).run(client.id)
            rows = database.invoices(client.id)
            assert len(rows) == 2
            assert {r["status"] for r in rows} == {"url_available", "no_url"}
            assert database.checkpoint(client.id).cursor == 102
            print("OFFLINE DEMO OK: 2 records, no duplicates, cursor 102, no PDF claimed verified.")
        return 0
    demo = args.demo
    database = Database(args.db or data_directory() / ("demo.sqlite3" if demo else "myinvoices.sqlite3"))
    if demo:
        prepare_demo(database)
    import tkinter as tk
    from .gui import App
    root = tk.Tk()
    App(root, database, demo=demo)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
