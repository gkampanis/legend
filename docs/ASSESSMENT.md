# Baseline and implementation assessment — 2026-10-18

## Initial baseline

- Actual execution environment: Debian GNU/Linux 13, x86_64; Python 3.12.14; Git 2.52.0; Tk 9.0.
- Checkout: `/workspace/legend`, origin `github.com/gkampanis/legend`, unborn branch `work`, no source or commits.
- No starter, tests, database, AGENTS.md, active virtual environment or legacy extracted artifacts were present.
- No baseline tests existed; the previously mentioned 11 tests could not be independently rerun.
- The Product Owner subsequently authorized proceeding with a new implementation from the available information. This explicitly supersedes the earlier instruction to wait for a missing starter.
- This is not the user's Windows computer or VS Code workspace; Windows interpreter availability was not checked remotely.

## Official specifications

Attempted `curl -fL --max-time 30 https://www.aade.gr/mydata`; the environment returned HTTP 403. No current XSD/version was retrieved. Endpoint paths, response structure and pagination semantics are provisional and must be compared against the official current documentation before claiming live compatibility. No production or development myDATA API requests were made.

## Implemented architecture

`Tk callback → worker thread → Synchronizer → RequestDocsAPI → XML parser → Database.commit_page → GUI queue`

Database connections are short-lived and local to the thread that uses them. A page's invoice records, history event, continuation token and revision update share one `BEGIN IMMEDIATE` transaction with `synchronous=FULL`. Known recipient mismatches reject a page. An optimistic revision check rejects stale concurrent writers. Repeated continuation tokens are detected across restarts.

Continuation requests keep the initial query MARK during an unfinished cycle. The completed high-water MARK is promoted only when the final page commits. Interruptions retain the last committed token. Documents are uniquely keyed by `(client_id, mark)`, and each client belongs to one API environment. Currency values use Decimal/text, not binary floating point.

In the original v0.1, credentials were never persisted; v0.2 adds per-client Windows DPAPI storage (session-only on other systems). Audit events have fixed fields (client ID, UTC timestamp, event kind and record count), not free-form exception/request text. URLs and raw XML stay in the local database and are treated as sensitive data.

## Deliberate phase boundaries and limitations

- No original source was restored; all project modules are newly written.
- No PDF downloader or PDF validation is implemented. States are `url_available` and `no_url`, never “verified”. No provider links are guessed.
- No PDF archiving, supplier directories, Excel export, Cloudflare handling, EXE build, release or push.
- Parser supports a narrow UTF-8 RequestedDoc/invoicesDoc response and stores each invoice's raw XML. This is not full XSD validation.
- Nonempty cancellation/classification/payment sections stop the sync rather than silently discarding them. Handling those changes is a future task.
- VAT inputs receive nine-digit format checks, not checksum/registration validation. A missing recipient is unknown, not verified. Wrong credentials cannot always be identified if the API omits recipient metadata.
- No historical date filter, reset workflow, encrypted database or OS credential vault yet.
- Page limit: 1000 per run, then explicit resumable stop.
- No hardware-specific or Windows GUI/API compatibility claim is made from Linux tests.
- API timeout is a socket I/O timeout, not a global deadline. Cancellation waits for the current request or transaction.
- History currently records committed pages, not a full operational incident log.

## Validation

Actual commands and outcomes on Linux:

```text
PYTHONPATH=src python -m unittest discover -s tests -v
37 discovered: 36 passed, 1 GUI test skipped because no display was configured.

DISPLAY=:89 PYTHONPATH=src python -m unittest discover -s tests -v
37 passed, 0 skipped, 0 failed (Xvfb virtual display).

PYTHONPATH=src python -m myinvoices --check-demo
OFFLINE DEMO OK: 2 records, no duplicates, cursor 102, no PDF claimed verified.

PYTHONPATH=src python -m compileall -q src tests
Passed.

git diff --check
Passed (new files are untracked in the initially empty repository).
```

For GUI validation only, the Debian-signed repository index and `xvfb` package were downloaded into `/tmp/myinvoices-gui-tools`, unpacked there, and run as a local helper with TCP listening disabled. No system packages/settings or global Python installation were changed. Xvfb is not an application dependency.

The suite covers synthetic metadata, official URL field handling, no-link cases, single/multiple pages, duplicates, client/environment isolation, malformed XML, request headers/timeouts/HTTP failures, interruption/resume, stale writers, repeated tokens, cancellation, transactional failure injection, cursor invisibility before commit, secret-free errors/logging, and a GUI demo flow. No real customer credentials or documents are used. Windows commands, `run_demo.bat` and real AADE operations remain unexecuted.

## Next milestone

Validate against official current XSD and controlled development responses. Confirm received-invoice semantics, endpoint paths, continuation parameter names, and cursor invariants. Resolve other response sections before supporting them. Implement PDF retrieval/validation only as a subsequent focused task.

## Follow-up v0.2 — 2026-10-18

The official AADE ERP API 2.0.2 September 2026 PDF was retrieved directly and read. The old 403 limitation above is historical; see AADE_RESEARCH.md for the exact source URL, hash, sections, findings and outstanding schema/live limitations. Headers, RequestDocs endpoint paths, entityVatNumber representation semantics and the downloadingInvoiceUrl PDF contract have now been checked against that primary source.

The GUI now configures credentials in each client's form, supports editing/name changes without resetting state, and automatically loads that client's credentials for sync. Windows uses current-user DPAPI ciphertext files; Linux uses session-only memory. SQLite schema remains at version 1; no migration or replacement of existing data was required. run_live.bat distinguishes real-mode launch from demo.

Validation with `DISPLAY=:89 PYTHONPATH=src python -m unittest discover -s tests -v`: 46 discovered, 45 passed, 1 native Windows DPAPI test skipped. GUI tests include selected-client credential editing and demo isolation. No native Windows, production API or development API testing has been performed. Encryption storage-contract tests use an opaque fake backend, not an implementation of encryption.

## Follow-up v0.2.1 — startup and rejection diagnosis

The Product Owner reported disabled fields and the old generic connection rejection. The screenshots proved demo mode was open; the reported rejection message only narrowed the prior HTTP result to 401 or 403. No specific live cause has been proven.

Normal editable client configuration is now the default entry point; START_MYINVOICES.bat and compatibility run_demo.bat open it. Explicit demo remains under demo_only.bat / --demo. New real clients default to production. Before a first page commit, an unused client's environment can be corrected transactionally without changing its VAT or losing data. Production API calls still require explicit GUI confirmation.

Errors now distinguish HTTP 401/403, selected environment, TLS/network, credential loading, XML parsing and database stages. Diagnostics deliberately omit exception text, response bodies, headers, URLs and locals. A separate explicitly invoked probe reads secure bindings, sends a single today's-date RequestDocs query, and prints no invoice data. With absent bindings it makes no request. No live probe with credentials has yet run.

Validation: 53 discovered tests, 52 passed with Xvfb, 1 native Windows DPAPI test skipped. Includes default startup, explicit demo, environment changes before/after sync, safe diagnostics, date query encoding and no-secret/no-body probe output. Offline smoke and compilation passed. Native Windows launchers remain unexecuted on this Linux machine.

## Follow-up v0.2.2 — local diagnostic alternative

The cloud checks did not expose usable secret bindings, while the Product Owner reported entering values in Network Secrets. This discrepancy does not prove the user failed to save anything. No cloud auth request could be made with those inaccessible bindings. The specific cause of the Windows connection rejection remains unconfirmed.

CHECK_AADE.bat now runs a local, explicitly initiated check using the existing per-client credentials on the user's own PC and their saved API environment. It performs one today's-date RequestDocs request, checks the XML envelope, and does not save invoice data or advance synchronization state. It does not require cloud secrets or ask for keys in the terminal. It keeps the console open for the fixed safe result/status message.

Validation: full offline suite discovered 58 tests: 55 passed, native Windows DPAPI and 2 GUI tests skipped without a display. The 5 new local-check tests all passed, covering saved credentials/environment, unchanged checkpoints, safe results, missing credentials/database, HTTP status propagation and cancellation before a request. Compileall and diff checks passed. This does not constitute native Windows or live AADE validation.

## Follow-up v0.3.0 — copyable diagnostics, UI and mixed records

The Product Owner's exact production message was “unexpected RequestDocs response”. That identifies the envelope root check, not the old mixed-section rejection. No specific live root has been obtained or accepted as valid. Diagnostic errors now identify known technical root names as XML_ROOT, use UNKNOWN for arbitrary names, and still reject unexpected roots without advancing progress. No XML body or credentials enter the error log/clipboard.

The independent known limitation of mixed sections has also been removed: supported cancellation/classification/payment records are durably preserved with invoice references, optional event marks and raw XML. Checkpoints use actual event marks when present. An absent optional event mark is not guessed; canonical XML hashing supplies idempotent identity. Full accounting interpretation of classification/payment content remains outside this change. Unknown or invalid sections still fail closed.

SQLite schema 2 is additive. A consistent schema-1 backup is written and quick-checked before migration. Regression tests verify original client, invoice and checkpoint preservation, backup version, repeatability, and rollback of invoice/change/checkpoint writes together.

UI changes: larger fonts/rows, accent-insensitive client search, client scrollbar, double-click editing, API connection check button, changes tab, canceled-invoice indicator, persistent scrollable error text with clipboard buttons and Ctrl+A/C, and cleanup of pending Tk timer callbacks when closing. The clipboard test checks the exact full error survives a later status message, plus search and async API-error propagation.

Validation on Linux/Xvfb: 67 discovered, 66 passed, 1 native Windows DPAPI test skipped. During UI testing an accent-normalization failure was diagnosed and fixed, and tests rerun. Compileall, offline smoke check and diff checks passed. Native Windows and real AADE responses remain unverified. The official PDF was read in the earlier research; this turn's re-download returned 403, so field structure was additionally checked against the downstream XSD copy in firebed/aade-mydata, not represented as a new primary-source verification.

## Uploaded aade-mydata 5.13.0 reference review

The user supplied aade-mydata-5.13.0.zip (280,112 bytes; SHA-256 43c47a06846dcfdf7e1c308ff38667c4398a4e12c21aa7e8c988bbfe9a0bc94d). Its 264 archive entries were checked for traversal/symlinks and extracted outside the project. No PHP code, Composer script or bundled instruction was executed.

Inspected composer.json, LICENSE.md, RequestDocs/MyDataGetRequest, MyDataRequest/GuzzleGateway, XMLReader/RequestedDocReader, RequestedDocXmlFactory and RequestedDoc model. The package is PHP 8.1+ / Guzzle / DOM, MIT-licensed; it is a reference rather than a Python runtime dependency. No source code was copied. Headers/endpoints/continuation/entityVatNumber match the documented contract. The Python request now also explicitly sends Content-Type text/xml and accepts both application/xml and text/xml. This is compatibility alignment, not proof of the live error's cause.

The supplied generic XML reader reads child nodes without asserting RequestedDoc as the root and can skip unknown complex types. We deliberately retain explicit response-root/error checks to avoid reporting an unexpected/error document as an empty successful sync. Schema copies in the archive corroborate mixed-section names and optional classification/payment event MARKs. They are downstream copies, not a newly retrieved authoritative AADE publication.

## Follow-up v0.3.1 — upstream error handling and observed string wrapper

Upstream main was compared against tag v5.13.0. Commit 1244dd9296606f63f1390d88c2faeaf8049b5d09 adds explicit ResponseDoc error handling for success/error HTTP responses. Our HTTP adapter now safely extracts bounded numeric service codes from HTTP-error XML as well as preserving the HTTP status. Invalid/HTML/DTD/oversized error bodies do not mask the transport error or expose body text.

The Product Owner supplied a production diagnostic XML_ROOT=string. A bounded string-envelope decoder now parses escaped XML and validates the inner root; it rejects empty/plaintext strings, malformed/DTD content and more than two nesting levels without checkpoint advancement. Tests cover direct/wrapped equivalence, wrapper limits, safe failure, wrapped ResponseDoc codes and a two-page synchronization end to end. No production payload or credentials were used. The actual inner payload remains unavailable; live resolution is not claimed.

There is no extra authorization/preflight endpoint call. Own-client credentials omit entityVatNumber; only explicit representative mode adds it. UI/help/error messages now state this clearly, and the direct HTTP test asserts one request.

Validation: 75 tests discovered; 71 passed, 4 skipped without a display (3 GUI, 1 native Windows DPAPI). Compileall, smoke demo and diff checks passed. Prior v0.3 GUI validation with Xvfb remains recorded separately; no new native Windows or live AADE verification is claimed.
