# Entersoft EBS extension — inspection and staged plan

## Baseline and separation

Baseline is myInvoices v0.13.0, commit 3e1d9eaaa313f8ad27019aaa531169e8e87409cd, on Linux/Python 3.12. Existing API, XML parser, cursor, client DPAPI, PDF retrieval/browser recovery, archives, preview and backup remain the working product. This extension is additive, on feature/entersoft-readonly-extension. It does not replace the normal workflow or automatically fetch PDFs.

Existing structured fields: MARK/UID, issuer/counterpart VAT, issue date, invoice type, original series and original aa as strings, net/VAT/gross Decimal values, raw XML and cancellation events. VAT category and expense line details are not separate DB columns: the extension may inspect those explicit elements in the existing raw XML, without altering the main parser/schema or inventing a value when absent. Supplier names are not currently stored as a dedicated field.

## Recording evidence — before implementation

Inspected the uploaded 48.67-second recording with frames sampled at 0–48 seconds and full-resolution views around 16, 24, 32 and 46 seconds. The recording composites two EBS workspaces into the upper part of a 1920x1080 video. Private customer frames are not copied into this repository or fixtures.

Confirmed visually: an expense-summary/linking dialog on the left, a TED expense form on the right, supplier lookup/selection, separate date/reference inputs beside a label rendered as "Εναλ. παραστατικό", a different "Σχετικά παρ/κά" row, and a manually selected expense line with net/VAT/gross values near the end. The recording shows user interaction/context menus and selection lists.

Not established: actual UI Automation IDs/classes; keyboard sequence independent of focus; EBS company binding; exact business meaning of each date/reference field; whether series and aa must be combined or separate; supplier lookup uniqueness; expense item versus GL account relationship; VAT control semantics; duplicate search; required extra fields; or a saved-document receipt. A populated form/visible number does not prove a successful save. No accounting account or supplier code from the recording becomes a default rule.

## Integration architecture and staged gates

A separate `entersoft` package reads the existing dataset through a read-only SQLite connection. It stores client/environment/company-scoped supplier mappings, invoice overrides, preparation queue and audit in a separate local sidecar DB. Main schema stays 4 and source records/cursors/files are not updated. A new optional menu entry opens its own window; normal myInvoices buttons keep their behavior.

1. **Stage A, this delivery:** local source/filter/selection, user mappings and overrides, strict Decimal/XML checks, persisted preparation queue/audit, test-mode pause/resume/stop, and optional read-only Windows window/control inspection. No EBS field writes or Save calls. Valid preparations remain Pending; they are never called Completed TEDs.
2. **Stage B, gated:** after confirming real UI selectors/company/supplier/reference/VAT/expense item semantics, populate exactly one document without saving. User compares read-back fields. Not enabled by Stage A success.
3. **Stage C, gated:** explicit approval for one Save; observe an unambiguous EBS saved reference and verify the record. Unknown outcome enters Manual Review, never blind Save retry.
4. **Stage D, gated:** only after tested B/C and external duplicate checks, process selected records sequentially. Interrupted Processing records must require review; absence of local history is not an external duplicate check.

## Controls still requiring verification

Exact target process/window and company; open-TED navigation; supplier search and read-back VAT/code; original reference destination; document versus supplier invoice date; expense item and explicit accounting account; VAT category field; required line/header fields; duplicate lookup; Save control, error/loading indicators and durable saved-document reference. Coordinates/OCR are not justified by this recording while UIA accessibility has not been inspected.

## Data decisions

Accounts are entered by the user, with explicit per-invoice override. No ML/OCR/account inference. Missing recipient/supplier identity, missing required XML VAT details, multiple VAT categories/classifications, credit notes, special VAT/adjustments or inconsistent totals go to Manual Review. Stage A has no authority to choose missing treatment. Full original invoice strings, including leading zeros, are preserved. Entersoft bindings and UI write enablement remain unverified.

## Implemented Stage A

- `entersoft/source.py`: read-only URI/query-only connection; validates frozen client VAT/environment, date/supplier filters, cancellation/classification-change/duplicate-reference facts. Missing mappings filter is applied through a read-only sidecar attachment before pagination.
- `entersoft/domain.py`: separate typed RPA states, explicit supplier/account mapping, original strings preserved, Decimal monetary rules, XML metadata comparison, VAT-line/category/classification checks, currency/special-adjustment review. Only ordinary categories 1–3 and invoice types 1.1/2.1 can become locally prepared; no rate/account conversion is inferred. Missing XML details stay unresolved.
- `entersoft/store.py`: independent schema 1 with application marker; user mappings, invoice overrides, immutable audit events, source fingerprints and indexed reference-identity hashes. Same-reference/different-MARK is a possible duplicate requiring review. Completed history is not resubmitted; changed completed source or interrupted Processing needs review. Completed states are never produced by Stage A.
- `entersoft/service.py`: selected records only, sequential preparation, condition-based pause/resume/stop and persistent outcomes. Source and target scope must agree. No desktop input, API or PDF service is invoked.
- `entersoft/desktop.py`: optional Windows UIA enumeration/read-only bounded tree inspection, actual process/handle/title recheck. No coordinates, focus/input/invoke calls or value-pattern reads. Nonstandard control names and password controls are suppressed; inspected IDs/classes remain local. The traversal budget is bounded between UIA calls, not a guarantee against a stalled native COM call.
- `entersoft/ui.py`: independent Greek window under a single extra existing menu command; source selection/filter/mapping/overrides, queue/test controls, counts, details, audit exports and optional Windows inspector. The core app is not made busy by this window's local preparation.
- `entersoft/excel.py`: streaming standard-library XLSX, literal text identity cells, no formulas/external links; optional UTF-8 BOM CSV. The XLSX was also read with the already available independent openpyxl reader during development; openpyxl is not a runtime dependency.
- `requirements-entersoft.txt` / `INSTALL_ENTERSOFT.bat`: optional Windows-only pywinauto 0.6.9. No OpenCV, OCR, pyautogui or new default runtime dependency.

The main schema remains 4; no migration was needed. New RPA tables are in `entersoft/agent.sqlite3`, with separate client/VAT/environment/company scopes. Existing main backup behavior is untouched: RPA data has an explicit separate SQLite backup button with consistency checks. API credentials/browser data are not copied. No active EBS write adapter exists; `require_write_access` rejects every write stage, even when called with an approval argument. Test Mode cannot be unchecked to bypass verification.

## API/schema evidence and remaining unknowns

The previously reviewed official ERP API v2.0.2 document remains the integration reference in AADE_RESEARCH.md. A new attempt to fetch the exact official PDF URL returned HTTP 403 in this environment; this is not reported as a successful fresh documentation fetch or a real API request. The user-provided aade-mydata 5.13.0 XSD mirror confirms `invoiceDetails/netValue`, `vatCategory`, `vatAmount`, optional `vatExemptionCategory` and `expensesClassification`, plus summary withholding/fees/tax/deduction fields. Its VatType range is 1–10; categories outside this narrow Stage A handling are reviewed instead of guessed. No new AADE endpoint or unsupported supplier/account API was invented.

Actual Windows EBS controls, company identity, supplier resolution, expense-item/account relationship, source series/number formatting, document dates, reference destination, VAT selectors, duplicate lookup and durable Save receipt remain unverified. These are concrete gates for B/C/D, not values inferred from screenshots. Stage A never claims that an absent local job means an invoice is absent from EBS. No real customer recording frames, supplier accounts or source data were committed.

## Validation and delivery evidence

Full regression actually executed after the final code changes:

```sh
DISPLAY=:97 PYTHONPATH=src:/tmp/myinvoices-delete-deps python -m unittest discover -s tests -v
PYTHONPATH=src:/tmp/myinvoices-delete-deps python -m myinvoices --check-demo
python -m compileall -q src tests tools
git diff --check
```

Results: **262 tests, 260 passed, 2 skipped**, 107.507 seconds. The skips remain native Windows DPAPI and Known Folder API; all 229 existing tests and 33 new extension tests were discovered. The 33 new tests comprise 27 domain/store/source/runner/desktop tests and six actual Tk extension tests. Initial new tests exposed namespace-sensitive fixture edits (fixed to exercise actual intended XML mutations), and actual screenshots exposed footer clipping (fixed with reserved grid rows and a meaningful viewport/control-position test).

New coverage: source bytes/cursor/schema preservation, scope mismatch refusal, client/environment/company mapping isolation, exact references/leading zeros, override precedence/removal, missing mapping filters before pagination, Decimal validation, missing XML lines, multiple VAT/classifications, credit/special/foreign-currency handling, classification updates, same-reference/different-MARK review, Completed-history suppression, interrupted-state review, transaction rollback, scope-isolated audit/backup, sequential test mode, pause/resume/stop and no implicit writes/downloads/API. The real Tk tests assert the old primary buttons remain intact and that the extension does not make the main app busy. UIA is exercised with fake read-only controls that fail on attempted click/value reads; actual Windows/EBS inspection is not claimed validated.

The standard-library XLSX passed structural tests and a separate openpyxl read-only roundtrip for Greek headers and literal MARK/leading-zero invoice identity cells. No Excel desktop execution was performed; openpyxl was already available only as a development check. CSV remains optional; use XLSX when Excel's automatic CSV type inference could alter identity display.

Offline demo, compileall and diff checks passed. Real synthetic Tk captures at 1366x768: [preparation](screenshots/entersoft-stage-a.png), [audit](screenshots/entersoft-audit.png). Reproduction helper: `tools/capture_entersoft.py`, with developer-only Pillow and a display. These images contain synthetic data, not frames from the uploaded private recording.

No user API credentials were used or copied. No production/development API request, PDF request, real EBS field entry, Save, native Windows probe, build, push or Release was performed. The existing Windows build pipeline is left unchanged; the provided instructions explicitly defer final EXE packaging until RPA validation. This is a working Stage A addition plus verified local services and an unvalidated read-only native adapter, not a production booking robot. B/C/D require the exact unresolved controls and verified workflow described above.
