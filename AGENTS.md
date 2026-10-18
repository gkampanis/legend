# myInvoices Starter

This is a new incremental implementation, not recovered legacy source.
Use the existing checkout; do not create a worktree unless asked.

- Communicate with the Product Owner in Greek. Use English code and filenames.
- Preserve legacy application files and data. Never import credentials or run production AADE calls without explicit authorization.
- Keep API credentials in memory only. Do not log request headers, bodies, URLs, raw exceptions, or credentials. Do not add secrets to fixtures.
- Read `docs/ASSESSMENT.md` before changing integration assumptions. Current AADE documentation/schema and live compatibility remain unverified.
- Keep GUI, services, transport, parser, and database separate. Never perform network or database operations in Tk callbacks.
- Invoice writes and continuation state must commit in one SQLite transaction. Preserve resume behavior and client/environment isolation.
- A myDATA record, available URL, downloaded PDF, and verified PDF are distinct states. Never mark a PDF verified based only on HTTP status, filename, or `%PDF`.
- No provider URL guessing, Cloudflare bypass, destructive migrations, automatic pushes, releases, or EXE builds in this phase.
- Run offline tests: `PYTHONPATH=src python -m unittest discover -s tests -v`.
- Run repeatable smoke check: `PYTHONPATH=src python -m myinvoices --check-demo`.
- GUI tests need a display; report skips honestly. Never claim Windows validation based on Linux tests.
