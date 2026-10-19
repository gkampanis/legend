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

## Follow-up v0.4.0 — direct provider PDF retrieval

The user confirmed actual production metadata synchronization (144 invoices and one change in their screenshot). This supersedes earlier uncertainty about local API synchronization, but does not establish live provider-download compatibility.

Implemented a separate credential-free PDF downloader using the exact official downloadingInvoiceUrl. It accepts public HTTPS/443 destinations and validates redirect destinations, rejects private DNS results, bounds redirects/response bytes, and does not solve challenges. HTML/challenge/HTTP/invalid-PDF outcomes remain explicit per-invoice pending states. PDF structure is checked with pinned pypdf 6.10.0; saved files remain saved_unverified because document-content identity is not independently compared. OCR is planned for electronic archiving/search, not a download gate.

Files use client VAT/environment, review folder, date, supplier and MARK with a unique suffix. Exclusive creation avoids overwriting files. Staging files are removed on failure/cancellation; completed files remain. Size/SHA-256/source hash determine repeat-download skipping; missing or changed files are downloaded again. Cancellation waits for the current blocking network read (30s socket timeout); the 90s streaming deadline is checked between reads, not a hard process deadline. Encrypted PDFs and more than 500 pages are deferred. DNS checks do not pin the TLS connection address; this is not a fully isolated arbitrary-URL fetch sandbox.

SQLite schema 3 adds attempt history only, backing up schema 1/2 before upgrade. The GUI has a background download action, folder chooser per batch, progress/cancellation and per-invoice states. INSTALL.bat installs the one free PDF dependency in the project venv. No OCR, browser automation, EXE or releases added.

Validation on Linux/Xvfb: 85 tests discovered, 84 passed, one native Windows DPAPI test skipped. Added PDF tests use generated synthetic PDFs/fake network responses and cover repeat download/hash changes, missing files, challenge/HTML/fake PDF, size bound, cancellation, cleanup on database error, migration/backup, client/cancellation exclusion, private URL/redirect refusal and HTTP failure stream cleanup. Existing GUI, synchronization and parser tests passed. Offline demo, compileall and diff checks passed. No native Windows execution or live provider PDF download has been performed.

## Follow-up v0.5.0 — date-scoped synchronization and browser PDF fallback

The user reported 0 saved / 15 pending real downloads. That summary does not identify the cause; no provider URLs or response payloads were available for a live reproduction. Pending summaries now count distinct failure states and preserve safe numeric HTTP statuses. HTTP-error bodies are bounded and inspected only for challenge markers, then closed/discarded. DNS lookup errors are distinguished from forbidden URL destinations; incomplete HTTP reads remain safe network failures.

Implemented inclusive issue-date filtering for local listing/count/pagination and download selection, plus actual RequestDocs dateFrom/dateTo synchronization. Date-scoped checkpoints and continuation history are durable and independent of the general scope. Invoice/change writes, cursor and token history remain atomic for each page. Schema 4 is additive with a quick-checked backup for schema 1/2/3 before migration. A date/client invoice index was added. Empty but completed period syncs also prevent changing the client's environment.

Normal-browser fallback opens only the selected invoice's validated official HTTPS URL. The user completes the provider's flow and downloads the PDF. A separate import action confirms client/MARK/supplier, checks the PDF and copies it into the review archive without moving the source. Duplicate imports compare content hash; imported PDFs count as saved for later repeat-download skipping. Cancellation and DB failures clean newly created files/staging files. This does not automatically solve CAPTCHA, capture browser downloads or transfer browser cookies. The user-supplied turnstile_solver repository was inspected read-only; findings and exact commit are in AADE_RESEARCH.md. It was neither executed nor integrated.

Validation: Linux/Xvfb full suite discovered 97 tests, 96 passed, one native Windows DPAPI test skipped. New tests cover inclusive boundaries/invalid dates, scoped resume after reopening DB, full-vs-period token isolation, cursor preservation, atomic rollback, repeated tokens, schema-3 backup/data preservation, source-preserving/deduplicated browser imports, HTML rejection, cleanup on DB failure, selected official URL opening (mocked), safe distinct HTTP/challenge counts, date-limited downloads, and the GUI date/browser/import/download flow. Offline demo, compilation and diff checks passed. Actual default-browser launch, Windows operation, provider pages and production date-filtered responses have not been exercised here. The original 15 downloads are not claimed resolved automatically.

## Follow-up v0.6.0 — explicit document resolution and browser capture

The real failure is now narrowed to all 15 URLs returning HTML. The user supplied a screenshot of an active IMPACT PDF viewer. This supports implementing document resolution and browser execution, not asserting that Turnstile caused the failures or inventing a JSON API. Direct provider research remained blocked by the current restricted cloud network; a domain addition was saved to the draft without claiming runtime application/publication. Details are in AADE_RESEARCH.md.

The direct downloader now resolves one explicit embedded/document reference from bounded HTML/JSON and standard PDF.js viewer URLs. Each followed URL uses the original public-HTTPS checks and credential-free transport. Cycles, multiple candidates, invalid descriptors, challenge pages and limits fail pending; streams are closed. No arbitrary links or guessed provider paths are crawled.

Added optional browser_downloads service with pinned Playwright 1.62.0, INSTALL_BROWSER.bat and a client/period batch button independent of document-row selection. It launches visible installed Edge/Chrome on Windows (system Chromium or installed Playwright Chromium on Linux), keeps a temporary profile for the batch and automatically archives a visible Blob/PDF.js document, a correlated PDF response or a completed download. CAPTCHA remains user-completed in that window. Existing manual browser/import workflow remains available. Work runs in the GUI worker; safe MARK notices/progress reach Tk through the queue. Archived bytes use the same structural checks, no-overwrite publication, hash deduplication and transactional attempt recording. No schema change or credential-storage change.

Browser limits differ from direct HTTP: final PDF bytes are checked against 40 MB/500 pages, but browser network transfers/downloads may complete before this size check. Browser response-body access can buffer the file. The nominal 90s per-document deadline is checked around awaited operations with individual limits, not a hard process deadline. Startup/browser availability, site-specific engines and actual provider compatibility remain unverified on native Windows. Normal TLS checks remain enabled; no personal browser profile, AADE auth headers, challenge solver or endpoint guesses were added.

Validation on Linux/Xvfb: 108 tests discovered, 107 passed, one native Windows DPAPI test skipped. Six optional integration tests launch actual system Chromium against synthetic pages intercepted by Playwright: JavaScript JSON→Blob viewer capture, canvas/viewer-correlated PDF response, Blob-triggered native download with automatic archive, repeated-run deduplication, ambiguity, ordinary HTML timeout and cancellation/cleanup. These checks make no external provider requests. An initial intercepted HTTP-attachment fixture was canceled by Chromium; the successful native-download fixture uses a real Blob download instead. Live HTTP attachments/provider behavior are not claimed validated. The canceled-download outcome is reported separately from timeout.

Additional direct resolver tests cover explicit embed/JSON/PDF.js references, base64, malformed/deep JSON, unknown/ordinary links, hidden/multiple candidates and HTML→JSON→PDF stream closure. The existing GUI workflow now also invokes the batch-browser button with no document row selected and verifies service dispatch. Smoke demo, compileall and diff checks passed. Windows installer scripts and actual Edge/Chrome launch on Windows remain unexecuted.

## Follow-up v0.7.0 — simple archive and automatic browser continuation

The user confirmed one real saved PDF and two pending HTML sources, and requested removal of the review wording and deep folders. The HTML states are not evidence that PDF structure checks rejected those two files. No additional provider access was performed in this change.

New files use destination/client-VAT/supplier-VAT/MARK.pdf; development client folders have a test suffix to preserve production separation without an extra folder level. Existing conflicting filenames receive a unique suffix without overwrite. Existing files/recorded paths are not moved or deleted. Visible saved states now say PDF saved (or imported), and completion messages omit review disclaimers and repeated generic instructions. Internal saved_unverified/imported_unverified states remain compatible with the existing DB and do not claim independent content verification.

The primary download action now uses a service that first attempts direct retrieval and lazily opens a shared browser session only for HTML/challenge/HTTP-403/descriptor failures. Final attempt state/counts are recorded once per document; direct successes are preserved when another document requires the browser. Missing browser support is reported pending with INSTALL_BROWSER.bat guidance, and initialization is not retried for every row. HTTP 429 and malformed PDFs are not immediately retried through the browser. The separate browser-only action remains available. Parser, credential isolation, no-overwrite, cancellation and hash checks remain in place.

Validation: full Linux/Xvfb suite discovered 112 tests, 111 passed, one native Windows DPAPI test skipped. New orchestration tests cover a mixed batch (one direct PDF plus two browser continuations), one shared session with cleanup, direct-only success without browser startup, missing browser without repeated initialization, and malformed PDF without an unrelated retry. The mixed-batch test was strengthened after the full run and the four targeted orchestration tests passed again. Existing six real-Chromium integration tests and four GUI tests passed in the full run. Smoke demo, compileall and diff checks passed. No new native Windows or live provider validation is claimed.

## Follow-up v0.8.0 — two download passes and three-step dashboard

The user reported that visible automated browsers opened every unresolved invoice and that clicking Cloudflare sometimes failed. A click is not evidence that a challenge passed. The user requested a background first pass, a separate second pass for pending items, three main buttons and the preceding current period by default. “Previous period” was interpreted and communicated as the previous full calendar month.

The primary download action now uses direct retrieval followed only when needed by a headless dynamic-document loader. Known challenges for the same source are skipped without a new request. Active challenge/HTTP-403 pages are recorded pending without asking the user to interact; HTTP 429 is not retried by browser. Headless mode does not launch a visible invoice window. The background browser timeout is 15s, so unsupported/slow dynamic pages can also remain pending rather than block the batch.

The third action uses a separate service selecting only source-matched, non-canceled, previously pending invoices within the selected client/date range. Saved/unattempted invoices and stale-source attempts are excluded. It opens the actual OS default browser, not an automated profile, so the human uses their normal provider session. The user completes any challenge and explicitly downloads the PDF. The service monitors a user-selected browser-download folder for one new/changed, stable and structurally valid PDF, requires a GUI-thread confirmation of the MARK, and copies it into the archive. Old files, partial downloads, rejected confirmations and multiple simultaneous new PDFs are not silently assigned. Original Downloads files remain. This is a human-assisted download flow, not automatic CAPTCHA solving or a guarantee of provider access.

The Tk dashboard follows the user's reference: period panel, three real counters, three primary actions, invoice list below. Client administration, API diagnostics, manual opening/import, folder changes and date presets moved into menus. Cancel is visible only during work; clipboard remains available through the messages menu and Ctrl+C. Normal startup defaults to the previous month (January/year rollover and leap February covered). Demo keeps an unrestricted synthetic period. Find performs RequestDocs then lists results; Download and Pending are separate. The explicit Find click initiates the read-only API request without an extra confirmation dialog. No credentials/schema/data migration changed. The date/client/source-bound pending queue is derived from existing attempt history; counters use metadata rather than loading raw XML.

Validation: Linux/Xvfb full suite discovered 123 tests, 122 passed, one native Windows DPAPI test skipped. New tests cover headless factory configuration, cached challenge skipping without network/browser, actual headless Chromium challenge deferral, previous-month boundaries, real-browser launch boundary with synthetic new Downloads files, source/client queue isolation, skipping old/saved files, partial/multiple downloads, rejected confirmations and cancellation. GUI checks cover the three actions/default dates/counters and the complete worker→confirmation queue→archive→counter refresh flow with an existing saved document excluded. Existing API/parser/transaction/GUI/real-Chromium tests, offline demo, compileall and diff checks passed. Native Windows, actual provider Cloudflare completion and real default-browser download monitoring are not exercised here. The first confirmed real PDF was supplied by the user; no new live success is claimed.

## Follow-up v0.9.0 — researched print recovery and download tabs

The Product Owner confirmed one saved PDF and two failures, and requested research before adding a second OCR/print attempt and success/failure tabs. Public upstream docs and issue reports were inspected before implementation; findings, licensing and Windows constraints are recorded in PDF_RECOVERY_RESEARCH.md. A historical headless PDF issue is not asserted to explain the user's current failures. OCR requires accessible document pixels/bytes, so it is deferred to archiving. Existing Chromium printing was selected instead of adding a printer server, Docker, OCR runtime or a cloud dependency.

The background pipeline still prefers the original PDF response/download/blob/PDF.js bytes. If that capture times out, it can print a loaded matching HTML invoice with Chromium. This print fallback checks visible invoice identity, fonts/images and stable text/layout, refuses challenge/login/error pages and outer embedded/canvas viewers, then validates both PDF structure and expected metadata in extracted print text. Its additional deadline is 20 seconds. It preserves multiple print pages in synthetic fixtures, but cannot establish completeness for arbitrary providers, lazy content or unknown viewers. It creates a distinct printed_unverified artifact rather than claiming to preserve original bytes or signatures. Existing browser/AADE credential boundaries and no-overwrite publication remain unchanged. Active challenges are deferred to the actual default browser; they are not solved by printing.

The human browser pass now also supports the user's Save as PDF from the document viewer. It still watches a single stable new PDF in the selected Downloads folder. The GUI confirmation explicitly chooses original PDF, printed PDF or rejection for the displayed MARK, and the worker preserves that provenance. All successful artifacts increment saved; printed is also counted separately. Human-confirmed PDFs are structurally checked but do not claim independent invoice verification. Files in Downloads remain untouched.

Three document tabs provide all records, successful artifacts and attempted failures, with provenance/status/reason columns and independent 200-row pagination. Lists and counters use the same SQL classification before pagination, scoped by client/date/current source hash. Unattempted or canceled records are excluded from failures; canceled saved records remain visible as canceled. A later ordinary failed attempt does not discard a previous saved artifact. A missing/changed file discovered by the next download run appends an invalidation event; only a later success restores saved state. Altered user files and attempt history are preserved. Refresh uses recorded metadata rather than hashing every archived PDF. Database schema stays at 4 and no migration or credential changes are required.

During regression validation, an old fixture used an arbitrary source hash for a successful saved attempt; it was strengthened to prove that a wrong-source success cannot suppress a pending current-source download, then that a matching success can. The schema-3 migration fixture now uses the actual source hash too. Review also identified cancellation during the final browser polling sleep being reported as timeout; it now returns cancellation before print/timeout, with a deterministic fake-clock regression test.

Validation: final Linux/Xvfb suite discovered 147 tests, 146 passed and one native Windows DPAPI test skipped. This includes nine Tk GUI tests, real system-Chromium print tests (two-page preservation, matching without MARK, mismatched/competing identities, hidden/blank/error/login/challenge/viewer rejection, print CSS hiding content, deadline/cancellation and safe failure), and end-to-end synthetic original-failure→print-success→saved-skip and both-attempts-fail pipelines. SQL tests cover more than 200 successes/failures, independent pagination, source/client/date isolation, tombstones, cancellation records and persisted history. The human-print GUI queue moves an invoice from failure to success with its printed provenance. Offline demo, compileall and diff checks passed. No native Windows execution, live provider requests, actual CAPTCHA completion or OCR engine evaluation occurred; the two reported provider failures are not claimed resolved without a Windows trial.

## Follow-up v0.10.0 — open-only browser action, monthly desktop archive and PDF viewer

The Product Owner confirmed a successful print but reported one remaining browser loop and requested that the third button only open the AADE-provided link. It now opens exactly the selected record's stored downloadingInvoiceUrl once in the default browser, then completes. It no longer starts the interactive batch, watches Downloads, asks for a download folder, confirms a newly detected file or automatically advances to another record. The obsolete GUI queue/confirmation wiring was removed; the standalone interactive service remains for compatibility with existing service tests but is not reachable from the UI. Opening does not mutate invoice or PDF-attempt state. There is no provider/DNS preflight for this manual action; HTTP(S) web links are handed over unchanged without provider-path rewriting, while non-web schemes, malformed links and URL credentials are refused. The automatic direct/dynamic/print pipeline remains on the second button. Provider-side page/challenge loops themselves are not claimed fixed by this UI change.

New downloads and manual imports default to the user's Desktop without a destination prompt. Windows SHGetKnownFolderPath with FOLDERID_Desktop respects redirected/OneDrive paths; failure reports an actionable folder-choice message rather than silently selecting the wrong location. The layout is Desktop/client-VAT/YYYY-MM/supplier-VAT/MARK.pdf, based on issue date and preserving production/development separation. A per-client session override remains in the existing folder menu. Recorded existing PDF paths are preserved and deduplication continues to use their stored size/hash, so old archives are not moved, overwritten or duplicated just to adopt the new layout. Schema and credentials are unchanged.

The invoice list now provides Open PDF, a double-click shortcut and an Open invoice folder button. The folder action opens the selected client's archive in Explorer and can create it before the first download. PDF opening resolves the current client/MARK's saved artifact in a worker and checks the recorded size/hash. The read-only Tk preview uses pinned pypdfium2 5.13.0/PDFium to render one page at a time, with previous/next, zoom and scroll controls. Rendering and PDF/file access stay off the Tk thread, native handles are closed in that worker, and a global lock serializes PDFium calls across windows. Resolution is bounded to approximately eight million pixels and 4096 pixels per side. Closing a preview cancels/discards pending results and removes its Tk polling callback. Errors also reach the existing copyable application message log. No external PDF viewer, browser, Pillow or OCR runtime is needed for this view.

Validation: Linux/Xvfb full suite discovered 161 tests; 159 passed and two native Windows tests (DPAPI and Known Folder) were skipped. Thirteen GUI tests exercised actual Tk widgets, including selected-link-only opening with no DNS/downloader/folder-picker invocation or attempt-state mutation; desktop-default downloads across September/October with no prompt; client-folder opening; PDF preview from a legacy path; actual PDFium color rendering, page navigation and zoom; close-during-render cleanup and copyable errors. Service tests cover year/month boundaries, client/supplier layout, invalid dates, missing/changed files, literal browser URL handoff and mocked Windows shell boundaries. Existing real-Chromium direct/dynamic/print tests and the remaining API/parser/database regressions passed. Offline demo, compileall and diff checks passed. The pinned Windows x64 Python 3.11-compatible pypdfium2 wheel was downloaded successfully via pip, but was not executed on Windows. Native Explorer/OneDrive/default-browser behavior and the remaining real provider page are not validated here.
