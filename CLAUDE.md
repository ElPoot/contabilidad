# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication Preference

**ALWAYS respond first in Spanish (Español).** This is the primary communication language for this project.

---

## Project Overview

Invoice classification and organization system for a Costa Rican accounting firm. The entire product lives in `gestor_contable/`.

## Commands

```bash
# Run the app (from repo root)
python gestor_contable/main.py

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest gestor_contable/tests/

# Run a single test file
python -m pytest gestor_contable/tests/test_observabilidad_logging.py
```

## Architecture

### Layer Map

```
gestor_contable/
  core/        ← domain logic, XML parsing, classification, file moves, caches
  app/         ← use cases, controllers, state, view models (being extracted from GUI)
  gui/         ← views only: events + render, no business rules
  tests/       ← automated tests (focused on observability/logging)
  data/        ← fallback local cache and test artifacts
```

### core/ — Key Modules

```
models.py              ← FacturaRecord: all XML fields + IVA + state
xml_manager.py         ← CRXMLManager: XML parsing, Hacienda API, cache
factura_index.py       ← FacturaIndexer: loads XMLs, links PDFs, extracts claves
classifier.py          ← ClassificationDB (SQLite) + classify_record (atomic file move)
catalog.py             ← CatalogManager: per-client account catalog (JSON)
session.py             ← ClientSession, resolve_client_session (login by tax ID)
pdf_cache.py           ← PDFCacheManager: caches PDF extraction results
settings.py            ← get_setting(): configuration management
client_profiles.py     ← load_profiles(): client folder mappings
iva_utils.py           ← parse_decimal_value(): IVA parsing
classification_utils.py ← filter records, get statistics, classify transactions
```

### app/ — Current Modules (Refactoring Layer)

```
use_cases/
  export_report_use_case.py   ← ExportPeriodReportUseCase
  classify_use_case.py        ← ClassifySelectionUseCase
controllers/
  load_period_controller.py   ← LoadPeriodController (load_session / load_range)
  orphaned_pdfs_controller.py ← OrphanedPdfsController
  pdf_swap_controller.py      ← PdfSwapController
services/
  forensic_overwrite_audit.py ← ForensicOverwriteAudit
state/
  main_window_state.py        ← MainWindowState
selection_controller.py       ← SelectionController
selection_vm.py               ← SelectionVM (view model)
```

### gui/ — Key Files

```
main_window.py   ← App3Window: 3-column layout (invoice list / PDF viewer / classifier)
session_view.py  ← SessionView: login modal by tax ID (cédula)
pdf_viewer.py    ← PDFViewer: pymupdf rendering, zoom, right-click text copy
loading_modal.py ← LoadingOverlay: loading spinner during XML/PDF scan
setup_window.py  ← shown if Z: cannot be mounted on startup
```

### Startup / Config Behavior

`config.py` tries to mount `Z:` via `subst` on startup:
- Reads `~/.gestor_contable/local_settings.json` for `subst_source` key
- If mount fails → opens `gui/setup_window.py` for manual path configuration
- App logs to `~/.gestor_contable_logs/gestor_contable.log`
- `xml_manager.py` uses `Z:/DATA/hacienda_cache.db`; falls back to `gestor_contable/data/hacienda_cache.db`

### File System Layout (Network Drive)

```
Z:/DATA/
  PF-{year}/
    CLIENTES/
      CLIENT_NAME/
        XML/
        PDF/
          SENDER/               ← subfolder by sender
        .metadata/
          clasificacion.sqlite
          catalogo_cuentas.json
          pdf_cache.json
          xml_cache.db
          duplicates_quarantine.sqlite
          ors_purge.sqlite
          receptor_purge.sqlite
          ignored_xml_errors.json
    Contabilidades/             ← destination for classified PDFs
      {mes}/{client_name}/
        COMPRAS/ GASTOS/ ACTIVO/ OGND/
  CONFIG/
    client_profiles.json
    settings.json
  hacienda_cache.db             ← Hacienda API cache (shared, read-only)
```

### Threading Rules

UI runs on the main thread. All slow operations go on worker threads:

```python
# CORRECT — update UI from worker thread
self.after(0, lambda: self.label.configure(text="..."))

# WRONG — causes random crashes
self.label.configure(text="...")  # from worker thread
```

Use `Queue` + `.after()` polling pattern.

### SQLite Thread Safety

All SQLite access must use `threading.Lock()`. See `ClassificationDB` in `gestor_contable/core/classifier.py`.

---

## Critical Business Rules

### Safe File Move (ATOMIC & NEVER SKIP)

Invoices are **fiscal documents**. The atomic protocol in `gestor_contable/core/classifier.py:classify_record()`:

1. Compute SHA256 of original PDF
2. Create destination folder with `mkdir(parents=True, exist_ok=True)`
3. Copy with `shutil.copy2()` (preserves metadata)
4. Compute SHA256 of copy
5. **If mismatch** → delete copy, raise error, **original stays intact**
6. **If match** → only then delete original
7. Record in `clasificacion.sqlite` with original SHA256

This is **not defensive coding** — fiscal regulations require proving original integrity. Never bypass with `shutil.move()` or direct deletion.

### Invoice Linking (Clave = 50 Digits)

Each invoice is identified by a **50-digit key** (`clave`) from Hacienda:

```
506    DDMMYY    CCCCCCCC  ??  ??  CCCCCCC  ...  TT  ... (50 total)
│      │         │         │   │   │              │
País   Fecha     Cédula    │   │   Consecutivo    Tipo Doc (01=factura, 03=NC)
                           │   └─ Situación comprobante
                           └─ Tipo documento
```

**Linking strategy** (in order of precedence):
1. Extract clave from PDF **filename** (50 consecutive digits)
2. Extract clave from PDF **text content** using pymupdf
3. Extract clave from **raw bytes** (for special layouts/tables)
4. Match PDF by **consecutive number** if clave extraction fails
5. Mark as `sin_xml` if PDF cannot be linked

**PDF search is recursive** — PDFs may be in SENDER subfolders.

### Invoice Record States

| `estado` | Meaning | Classification | File Move |
|----------|---------|-----------------|-----------|
| `pendiente` | XML + PDF, not classified | blocked | blocked |
| `pendiente_pdf` | XML only, no PDF | optional | blocked |
| `sin_xml` | PDF only, no XML | allowed | allowed |
| `clasificado` | Already classified | allow reclassify | allow move |

### Omission Reasons (razon_omisión)

- `non_invoice` — heuristics identified as non-fiscal document
- `timeout` — PDF extraction exceeded `hacienda_timeout`
- `extract_failed` — PDF text extraction failed

### Multiple Claves in PDFs

Some NC PDFs contain two claves. **Always use the last/latest clave found** — it's the current document, not a reference.

---

## Development Rules

### Scope

**ONLY modify files inside `gestor_contable/` directory** (plus repo-root docs/tooling like `AGENTS.md`, `build.py`, `gestor_contable.spec` when explicitly requested).

### File System
- **Always use `pathlib.Path`**
- **Never hardcode paths** — use `get_setting('network_drive')` from `gestor_contable/core/settings.py`
- **Folder names may have special characters** — use `_sanitize_folder()` from `gestor_contable/core/classifier.py`
- **`Z:/` may be unavailable** — wrap all disk operations in `try/except`

### Data Handling
- **Hacienda numeric key = exactly 50 digits**
- **Use `pymupdf` (`fitz`) for all PDF work** — `pdfplumber` is not used
- **Decimal parsing:** use `parse_decimal_value()` from `iva_utils.py` (handles EU and US formats)

### XML Classification Rules (CRITICAL)
- **NEVER classify XML files by filename or suffix patterns** (`_respuesta`, `_firmado`, `_NC`, etc.) — filenames are unreliable and vary per vendor.
- **ALWAYS read the XML content** — use the root element tag (`FacturaElectronica`, `MensajeHacienda`, `MensajeReceptor`, `NotaCreditoElectronica`, etc.) extracted by `flatten_xml_stream()`.

### UI Color Palette (Dark Theme)
- `BG="#0d0f14"`, `SURFACE="#13161e"`, `CARD="#181c26"`, `BORDER="#252a38"`
- `TEAL="#2dd4bf"` (accent), `TEXT="#e8eaf0"`, `MUTED="#6b7280"`
- `DANGER="#f87171"`, `SUCCESS="#34d399"`, `WARNING="#fbbf24"`

---

## Key External Dependencies

### Hacienda API (Costa Rica Tax Authority)

`https://api.hacienda.go.cr/fe/ae?identificacion={cedula}`

- `CRXMLManager` in `xml_manager.py` handles API calls and caching
- **Timeout:** `hacienda_timeout` setting (default 10s)
- **Retries:** `hacienda_retries` setting (default 2)

### Python Packages

Required (see `requirements.txt`):
- `customtkinter` >=5.2 — UI framework
- `pymupdf` >=1.24 — PDF rendering (fitz)
- `pandas` >=2.0 — data processing
- `openpyxl` >=3.1 — Excel I/O
- `requests` >=2.31 — HTTP client
- `cryptography` >=42.0 — encryption
- `keyring` >=24.0 — credential storage

---

## Refactoring Active: GUI Decoupling (Audit 2026-04-02)

Goal: extract a thin `app/` layer between GUI and core — without rewriting the app and without breaking production.

### Target Architecture

```
gui/           ← views only: events, render, no business rules
app/           ← use cases + controllers (being built incrementally)
  use_cases/
  controllers/
  state/
  services/
core/          ← domain logic (DO NOT move rules from here)
```

### Refactoring Progress

- **Phase 1 done:** `ExportPeriodReportUseCase`, `SelectionController`, `LoadPeriodController`, orphaned/swap controllers extracted.
- **Phase 2 — next:** `MainWindowController`, `SessionController`, unified render functions.
- **Phase 3 — future:** Controllers and use cases free of customtkinter; stable view models; ports for PDF viewer and dialogs.

### Rules for Every Refactor (MANDATORY)

1. **Delegate, don't replace** — create the new module, make the existing method call it. Never rewrite an entire flow in one PR.
2. **No business rules in GUI** — if it belongs to core, put it in `core/` or `app/`.
3. **One vertical flow per PR** — each change moves exactly one responsibility.
4. **Reuse core, don't move it** — `core/` is stable. New `app/` modules import from `core/`; never copy-paste logic.
5. **Don't introduce new threading patterns** — converge to a single `TaskRunner` pattern; don't add new `threading.Thread` calls directly from views.
6. **Preserve existing contracts** — callback signatures, button names, messages, and UI behavior must remain identical unless explicitly requested.
7. **View models for render** — pass a typed view model; never read widgets or dispersed caches directly.
8. **Validate before merging** — manual scenario matrix:
   - Load client + change date range
   - Single selection and multi-selection
   - Classify individual and batch
   - Export Excel/CSV
   - Sanitize, recover orphan, link omitted
   - Verify tree, PDF viewer, and right panel stay in sync

### Prohibited in This Phase

- Touching `classify_record()` in `classifier.py` without explicit request
- Moving SQLite access outside `ClassificationDB` without a `threading.Lock()`
- Adding any new direct imports of `customtkinter` in `core/` or `app/`
- Refactoring multiple flows in one commit
