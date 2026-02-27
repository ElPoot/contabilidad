# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Accounting toolset for a Costa Rican accounting firm. Three apps that work together:

| App | Path | Status | Purpose |
|-----|------|--------|---------|
| **App 1 — Mass Download** | `APP 1/facturacion_system/` | Stable | Downloads Gmail/IMAP attachments, organizes XML and PDF by client |
| **App 2 — XML Processor** | `APP 2/facturacion/` | Stable | Parses XMLs, extracts fields, exports Excel reports |
| **App 3 — Clasificador** | `app3/` | In development | Visual classifier; reuses modules from Apps 1 & 2; will replace App 2 |

## Running the Apps

```bash
# App 1 — Mass Download
python "APP 1/facturacion_system/main.py"

# App 2 — XML Processor
cd "APP 2" && python facturacion/ui_main.py

# App 3 — Clasificador (from repo root)
python app3/main.py
```

## Installing Dependencies

```bash
pip install -r requirements.txt
```

Key packages: `customtkinter`, `pymupdf` (fitz), `pandas`, `openpyxl`, `requests`, `cryptography`, `keyring`.

## Architecture

### Cross-App Module Reuse

`app3/bootstrap.py` adds App 1 and App 2 to `sys.path` so App 3 can import from them:

```python
from facturacion.xml_manager import CRXMLManager          # App 2
from facturacion_system.core.pdf_classifier import ...    # App 1
from facturacion_system.core.settings import get_setting  # App 1
from facturacion_system.core.client_profiles import ...   # App 1
```

### App 3 Module Structure

```
app3/
  main.py              ← entry point
  config.py            ← network_drive(), client_root(), metadata_dir()
  bootstrap.py         ← sys.path setup for legacy imports
  core/
    models.py          ← FacturaRecord dataclass (all XML fields + IVA)
    factura_index.py   ← FacturaIndexer: loads XMLs via CRXMLManager, links PDFs
    classifier.py      ← ClassificationDB (SQLite) + classify_record (safe move)
    catalog.py         ← CatalogManager: per-client account catalog (JSON)
    session.py         ← ClientSession, resolve_client_session
  gui/
    main_window.py     ← App3Window: 3-column layout (list / PDF viewer / classifier)
    session_view.py    ← SessionView: login by client tax ID (cédula)
    pdf_viewer.py      ← PDFViewer: pymupdf rendering, zoom, right-click copy
```

### File System Layout (Network Drive)

```
Z:/DATA/
  PF-{year}/
    CLIENTES/
      CLIENT_NAME/
        XML/                    ← electronic invoices (.xml)
        PDF/                    ← visual representations (.pdf)
          SENDER/               ← subfolder by sender (App 1)
        .metadata/
          state.sqlite          ← download history (App 1)
          clasificacion.sqlite  ← classification records (App 3)
          catalogo_cuentas.json ← per-client account catalog (App 3)
  CONFIG/
    client_profiles.json
    settings.json
```

### Threading Rules

UI runs on the main thread. All slow operations (XML loading, Hacienda API calls, file moves) go on worker threads:

```python
# CORRECT — update UI from worker thread
self.after(0, lambda: self.label.configure(text="..."))

# WRONG — causes random crashes
self.label.configure(text="...")  # from worker thread
```

Use `Queue` + `.after()` polling pattern (as in App 2's `ui_main.py`).

### SQLite Thread Safety

All SQLite access must use `threading.Lock()`. See `ClassificationDB` in `app3/core/classifier.py` and `StateDB` in `APP 1/facturacion_system/core/gmail_utils.py`.

## Critical Business Rules

### Safe File Move (NEVER skip this)

Invoices are fiscal documents — a failed move that loses the original is a serious problem. The atomic protocol in `classifier.py`:

1. SHA256 of original PDF
2. `mkdir(parents=True)` at destination
3. `shutil.copy2()` to destination
4. SHA256 of copy
5. If SHA256 mismatch → delete copy, raise error, **original intact**
6. Only if SHA256 match → delete original
7. Record in `clasificacion.sqlite`

### Invoice Linking

Each invoice is identified by a **50-digit numeric key** (`clave`) present in the XML and PDF filename. Three related files share the same key: the invoice XML, the Hacienda response XML (`MensajeHacienda`), and the PDF. PDF search must be recursive (PDFs may be in sender subfolders).

### Exception States

| Condition | `estado` value | Behavior |
|-----------|---------------|----------|
| XML exists, no PDF | `pendiente_pdf` | No file move |
| PDF exists, no XML | `sin_xml` | Classify normally, warn |
| Already classified | `clasificado` | Show previous, allow reclassify |
| Duplicate name at dest | — | Append first 8 chars of SHA256 |

## Development Rules

- **Always use `pathlib.Path`** — the system runs on Windows with `Z:/DATA/` paths
- **Never hardcode paths** — use `get_setting('network_drive')` from `settings.py`
- **Folder names may have special characters** — use `sanitize_folder_name()` from `file_manager.py`
- **`Z:/` may be unavailable** — wrap all disk operations in `try/except` with clear error messages
- **Hacienda numeric key = exactly 50 digits**
- **Use `pymupdf` (`fitz`) for all PDF work** — `pdfplumber` is no longer used
- **UI color palette** (dark theme, defined in each GUI module):
  - `BG="#0d0f14"`, `SURFACE="#13161e"`, `CARD="#181c26"`, `BORDER="#252a38"`
  - `TEAL="#2dd4bf"` (accent), `TEXT="#e8eaf0"`, `MUTED="#6b7280"`

## Key External Dependency

**Hacienda API** (Costa Rica tax authority) for resolving company names from tax IDs:
`https://api.hacienda.go.cr/fe/ae?identificacion={cedula}`

Results are cached in `APP 2/data/hacienda_cache.db` (SQLite). `CRXMLManager` in App 2 handles both the API calls and the local cache.
