# v0.13.0 — implementation and validation report

## Baseline and scope

- Repository: `/workspace/legend`; execution host Linux, Python 3.12.14.
- Original branch: `myinvoices-starter`; baseline `f669efa73b1bc1251db0439f1c0d94819f82898e` (v0.12.0).
- Feature branch: `feature/ux-reliability-v013`; original checkout was clean. No worktree or legacy application reconstruction.
- Baseline rerun: 198 tests, 196 passed, 2 native Windows skips, 93.023 seconds.
- No existing Windows build script, PyInstaller spec or CI workflow was present. New build infrastructure is prepared, not executed on Windows.
- No real AADE/provider calls, imported customer credentials or original customer documents were used. No push or release was performed.

## Implemented changes

| Area | Modules | Result |
|---|---|---|
| Status adapter | `presentation.py` | Typed reason groups, safe next actions, distinct provenance, Greek date/amount display; persisted state strings unchanged. |
| Batch reporting | `batch.py`, `pdf_downloads.py` | Events reflect completed/committed work; current MARK, known total, saved/printed/pending/skipped counters and cancellation. AADE total remains explicitly unknown. Last summary persists per client in a fixed-field log. |
| Diagnostics | `diagnostics.py` | `pdf`, `pdf_import`, backup/export stages retained. Explicit support export only includes allowlisted version/runtime/stage/HTTP fields. |
| Workspace | `ui_layout.py`, `ui_components.py`, `ui_actions.py`, `gui.py` | Compact context, two main workflow buttons, contextual personal-browser action, stored tab, failure details, multi-selection, separated client management. |
| Querying | `database.py`, `presentation.py` | Whitelisted SQL sort and parameterized combined filters before pagination; typed date/amount/MARK ordering; small recovery count query instead of loading all failed rows on each search. |
| Export/settings | `table_export.py`, `settings.py` | Explicit selected/filtered export, UTF-8 BOM and CSV escaping/formula protection, no URLs/XML/paths; atomic allowlisted preferences and valid per-client destinations. |
| Backup | `backup.py`, `backup_ui.py` | SQLite backup API, validated manifest/hashes/counts, optional recorded-PDF copies, explicit partial result, temporary restore preview without touching the active DB. |
| Browser contract | `print_fallback.py`, `browser_recovery.py` | Public print integration names replace private-helper imports. Original helpers remain compatible; recovery accepts an optional table/selection scope. |
| Distribution | `build.bat`, `tools/build_windows.py`, Windows workflow | Windows-only self-contained directory build, including Chromium, source tests and packaged CLI/GUI smoke-test definitions. No built artifact yet. |

The actual data model has issuer VAT but no supplier-name field; supplier-name search was not invented. The legacy interactive download helper remains because it has references/tests; it was not deleted.

## UX implementation decisions

- **Split view / selector:** client list remains separate; selected invoice details load asynchronously and are invalidated when selection/client changes.
- **Information hierarchy:** compact totals, table-dominant center, persistent visible destination and explicit `[ΔΟΚΙΜΑΣΤΙΚΟ ΑΑΔΕ]` text. Date fields are adjacent to that context, avoiding a duplicated period in the summary.
- **Progressive disclosure:** destructive/reset actions moved to client management; logs collapse; full last batch has its own read-only view. At high scaling, decorative heading, duplicate summary and filters collapse to preserve document space; Filters / Ctrl+F reveals the filters.
- **Dynamic queries:** 300 ms debounce, SQL filtering and independent 200-row pages. Selection is current-page selection; filtering does not mutate invoice data.
- **Error guidance:** full cause and a concrete Greek next action in details; source URL and local path are only in explicit record details, not technical reports/CSV.
- **Cancelability:** existing cancellation stays in the service layer. Closing the managed browser still stops recovery; the personal-browser action remains a single unchanged-link handoff.
- **Keyboard:** Ctrl+F, Ctrl+C rows, Ctrl+Shift+C cell/MARK, Shift+F10, Enter and Escape; native focus/disabled rendering is preserved. No formal screen-reader/WCAG audit is claimed.
- **Tokens:** common spacing, readable system/DejaVu fallback fonts, restrained palette, native Windows theme, accessible text labels alongside state colors. Linux fallback uses ttk clam; no third-party UI toolkit.

## Data and security contracts

Schema remains **4**; no migration, cursor/parser/DPAPI rewrite or archive-layout change was introduced. Archives remain `Desktop/registered-client-VAT/YYYY-MM/MARK.pdf`, or the explicitly chosen custom base. Old paths remain intact. Reset/recycle, exact AADE URL opening and PDF preview regressions still pass.

A stored PDF, printed HTML copy and manual import are separate provenance values; no new VERIFIED state exists. Source-isolated attempts and size/hash checks remain. Recovery selection/counts exclude unattempted, saved, reset, cancelled and stale-source records. Browser tests additionally cover the same VAT in a different AADE environment with an independent profile.

UI settings store only column widths/order and valid destination paths. CSV excludes credentials, cookies, raw XML, URLs and local file paths. Support exports exclude arbitrary exception text, filenames, customer data and URLs. SQLite, backup data and provider profiles are not presented as encrypted; DPAPI is limited to API credentials on the current Windows user.

## Backup boundaries

The new backup is a local directory containing metadata SQLite and a manifest. Optional PDF copies cover recorded successful artifacts, including recorded historical paths, not every unrelated file in an archive folder. Missing/changed/unreadable PDFs are listed as partial-copy issues; they do not silently become a complete PDF backup. Existing source files and backups are never overwritten by creation.

Validation checks manifest structure, file hashes/sizes, unsafe relative paths/symlinks, schema, foreign keys and record counts. It copies metadata into a temporary directory and checks the restored temporary DB. Interrupted creation removes only the operation's own unpublished working directory. Hashes detect corruption; there is no cryptographic authentication/signature.

**Active restore is intentionally not implemented in this delivery.** Correct coordinated replacement of an active DB, file relocation across PCs and credentials re-entry is not covered by the preview. The UI explicitly states this. The existing database is never replaced; copied PDFs retain original-path references in the manifest. API credentials, browser profiles and UI preferences are excluded. DPAPI ciphertext is not promised portable.

## Test evidence

Full regression command actually executed:

```sh
DISPLAY=:96 PYTHONPATH=src:/tmp/myinvoices-delete-deps python -m unittest discover -s tests -v
```

The temporary dependency path supplies the pinned Send2Trash package in this cloud environment; a normal installation uses `requirements-browser.txt`. Xvfb and system Chromium provide real Tk and browser execution.

- Full run: **229 discovered, 227 passed, 2 skipped**, 101.672 seconds.
- Skips: native Windows DPAPI and Windows Known Folder API only; no new skip in this Linux run.
- After the final compact-layout adjustment: **23 GUI/classification regressions passed**, 11.736 seconds. This includes meaningful minimum table/details heights at 1366×768 and 1920×1080 with fresh Tk instances using scaling 96/72, 120/72 and 144/72. An earlier mapping-only test was strengthened after screenshots exposed an almost-empty viewport.
- New tests: 31, covering domain/progress/diagnostics, SQL filters/sort/export/settings, backup and the actual Tk workspace/close path.
- Existing browser test extended for same-VAT/different-environment session isolation.
- Existing label assertion updated only for the deliberately contextual, unnumbered browser action.
- Offline demo passed: two synthetic records, no duplicates, cursor 102, no PDF claimed verified.
- Compileall and diff checks passed.
- Synthetic 10,003-row SQL filtered/sorted page query measured **0.0117 seconds** in the full run. This measures that query on this host, not end-to-end GUI latency, a 100k-row workload or Windows performance. Substring searches remain linear; exports stream rows.

Additional actual commands:

```sh
PYTHONPATH=src:/tmp/myinvoices-delete-deps python -m myinvoices --check-demo
python -m compileall -q src tests tools
git diff --check
```

Tests include malformed/interrupted/corrupt backups, mismatch counts/schema, partial PDF copies, no overwrite, cancellation, client/source/date boundaries, clipboard/CSV safety, settings persistence, async details invalidation and clean close after preference-save completion. No Excel application, native Windows UI, actual CAPTCHA success or live-provider success was validated.

## Actual screenshots

All captures use synthetic data, Linux/Xvfb and real Tk widgets. They are not mockups or Windows screenshots. `tools/capture_screenshots.py` reproduces them using developer-only Pillow. For the baseline only, its fallback font is changed at runtime from Xvfb's broken core Helvetica Greek glyphs to DejaVu Sans; baseline layout/behavior is otherwise loaded from the recorded v0.12 Git revision.

| View | Capture |
|---|---|
| v0.12 before, 1366×768 | [Before](screenshots/v012-before.png) |
| v0.13 main, 1366×768 | [Main](screenshots/v013-main.png) |
| Failure selection and full guidance | [Details](screenshots/v013-failures-details.png) |
| Actual synthetic downloader running | [Progress](screenshots/v013-batch-progress.png) |
| Actual synthetic batch finished | [Summary](screenshots/v013-batch-summary.png) |
| 1366×768, Tk scaling equivalent to 150% | [High scaling](screenshots/v013-1366x768-scale150.png) |

Columns can still need horizontal scrolling, especially at 150%; full selected-record information remains accessible in the scrollable details panel. This is not a Windows DPI or screen-reader certification.

## Build status and remaining work

`python tools/build_windows.py` was invoked on Linux and correctly refused with exit code 2: Windows Python/runner required. PyInstaller 6.22.3 availability was checked, but no Windows build or packaged executable was run. The newly prepared workflow cannot run in GitHub until the branch is uploaded; the supplied instruction explicitly requires approval before push/release. No such publication was performed.

The build targets a self-contained **directory** containing `myInvoices.exe`, Chromium and DLLs; it is not single-file. The workflow defines Windows source tests, a packaged offline-demo check and a native-window startup smoke check before artifact upload. Those are definitions, not validation results. Browser distribution/AV behavior and packaged resource collection remain to be verified by that actual run.

Remaining concrete boundaries: active restore/file relocation, Windows-native execution/build, large-volume workload beyond the measured 10k query, and formal accessibility/Excel testing. No new provider was exercised, so this delivery makes no new unsupported-provider diagnosis or universal recovery claim. OCR stays a separate future milestone.

## Logical commits

- `afe10df`: typed reason/progress model and pdf diagnostic regression.
- `347f847`: verified local backup and non-destructive restore preview.
- `e8360f5`: workspace redesign, SQL query tools, details, CSV, settings and integration tests.
- Build/docs/screenshots are delivered in the following local commit on the same feature branch. The final hash is reported with delivery.
