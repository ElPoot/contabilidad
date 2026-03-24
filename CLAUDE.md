# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 🌐 Communication Preference

**ALWAYS respond first in Spanish (Español)** before any other language. This is the primary communication language for this project.

---

## Project Overview

Invoice classification and organization system for a Costa Rican accounting firm.

| App | Path | Status | Purpose |
|-----|------|--------|---------|
| **Gestor Contable** | `gestor_contable/` | Production | Invoice classification and organization; manages XMLs/PDFs into accounting structure; exports Excel reports |

## Running the App

```bash
# Gestor Contable (from repo root)
python gestor_contable/main.py
```

## Installing Dependencies

```bash
pip install -r requirements.txt
```

Key packages: `customtkinter`, `pymupdf` (fitz), `pandas`, `openpyxl`, `requests`, `cryptography`, `keyring`.

## Architecture

### Module Structure (Gestor Contable)

**Core Business Logic:**

```
gestor_contable/core/
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

**GUI:**

```
gestor_contable/gui/
  main_window.py         ← App3Window: 3-column layout (invoice list / PDF viewer / classifier)
  session_view.py        ← SessionView: login modal by tax ID (cédula)
  pdf_viewer.py          ← PDFViewer: pymupdf rendering, zoom, right-click text copy
  loading_modal.py       ← LoadingOverlay: loading spinner during XML/PDF scan
```

**Entry Points:**

```
gestor_contable/
  main.py                ← Application entry point
  config.py              ← network_drive(), client_root(), metadata_dir()
  bootstrap.py           ← No-op (legacy)
```

### File System Layout (Network Drive)

```
Z:/DATA/
  PF-{year}/
    CLIENTES/
      CLIENT_NAME/
        XML/                      ← Source XMLs
        PDF/                      ← Source PDFs
          SENDER/                 ← subfolder by sender
        .metadata/
          pdf_cache.json          ← PDF scan results (cached)
          clasificacion.sqlite    ← classification records (SQLite)
          catalogo_cuentas.json   ← per-client account catalog (JSON)
    Contabilidades/               ← destination folder for classified PDFs
      {mes}/{client_name}/
        COMPRAS/...
        GASTOS/...
        ACTIVO/...
        OGND/...
  CONFIG/
    client_profiles.json          ← client mappings
    settings.json                 ← configuration
  hacienda_cache.db               ← Hacienda API cache (shared, read-only)
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

All SQLite access must use `threading.Lock()`. See `ClassificationDB` in `gestor_contable/core/classifier.py` and `StateDB` in `APP 1/facturacion_system/core/gmail_utils.py`.

## System Status

**Standalone Application:**
- ✅ Unified, self-contained codebase in `gestor_contable/`
- ✅ All business logic native (XML parsing, IVA calculation, classification, etc.)
- ✅ No external code dependencies
- ✅ Reads from network drive (XMLs, PDFs) for source data
- ✅ Writes classified files to `Contabilidades/` directory

---

## Critical Business Rules (App 3)

### Safe File Move (ATOMIC & NEVER SKIP)

Invoices are **fiscal documents**. A failed move that loses the original is critical. The atomic protocol in `gestor_contable/core/classifier.py:classify_record()`:

1. Compute SHA256 of original PDF
2. Create destination folder with `mkdir(parents=True, exist_ok=True)`
3. Copy with `shutil.copy2()` (preserves metadata)
4. Compute SHA256 of copy
5. **If mismatch** → delete copy, raise error, **original stays intact**
6. **If match** → only then delete original
7. Record in `clasificacion.sqlite` with original SHA256

This is **not defensive coding** — fiscal regulations require proving original integrity.

### Invoice Linking (Clave = 50 Digits)

Each invoice is identified by a **50-digit key** (`clave`) from Hacienda:

```
506    DDMMYY    CCCCCCCC  ??  ??  CCCCCCC  ...  TT  ... (50 total)
│      │         │         │   │   │        │    │
País   Fecha     Cédula    │   │   Consecutivo  Tipo Doc (01=factura, 03=NC)
                           │   └─ Situación comprobante
                           └─ Tipo documento
```

**Linking strategy** (in order of precedence):
1. Extract clave from PDF **filename** (50 consecutive digits)
2. Extract clave from PDF **text content** using pymupdf
3. Extract clave from **raw bytes** (for special layouts/tables)
4. Match PDF by **consecutive number** if clave extraction fails
5. Mark as `sin_xml` if PDF cannot be linked

**PDF search is recursive** — PDFs may be in SENDER subfolders created by App 1.

### Invoice Record States

| `estado` | Meaning | Classification | File Move |
|----------|---------|-----------------|-----------|
| `pendiente` | XML + PDF, not classified | ❌ blocked | ✖️ blocked |
| `pendiente_pdf` | XML only, no PDF | ⏸️ optional | ✖️ blocked |
| `sin_xml` | PDF only, no XML | ✅ allowed | ✅ allowed |
| `clasificado` | Already classified | ✅ allow reclassify | ✅ allow move |

### Omission Reasons (razon_omisión)

PDFs may be excluded from classification for these reasons:
- `non_invoice` — PDF matched heuristics as non-fiscal document (brochure, catalog, etc.)
- `timeout` — PDF extraction exceeded `hacienda_timeout` threshold
- `extract_failed` — PDF text extraction failed (corrupted, image-only, etc.)

### Multiple Claves in PDFs

Some NC (credit notes) PDFs contain **two claves**:
- **Clave 1:** Original invoice (tipo 01) — for reference
- **Clave 2:** NC itself (tipo 03) — the actual document

**Rule:** Always use the **last/latest clave found** in the PDF (it's the current document, not a reference).

## Development Rules (App 3)

### Development Scope

**RULE: ONLY modify files inside `gestor_contable/` directory.**

- ✅ **All modules must be in `gestor_contable/` or its subdirectories**
- ✅ **Allowed externals:** pandas, openpyxl, requests, cryptography, customtkinter, pymupdf, etc.
- ✅ **Use standard library + external packages only**

### File System
- **Always use `pathlib.Path`** — Windows paths with `Z:/DATA/` notation
- **Never hardcode paths** — use `get_setting('network_drive')` from `gestor_contable/core/settings.py`
- **Folder names may have special characters** — use `_sanitize_folder()` from `gestor_contable/core/classifier.py`
- **`Z:/` may be unavailable** — wrap all disk operations in `try/except` with clear error messages

### Data Handling
- **Hacienda numeric key = exactly 50 digits**
- **Use `pymupdf` (`fitz`) for all PDF work** — `pdfplumber` is no longer used
- **IVA rates:** see `IVA_TARIFA_CODE_MAP` in `gestor_contable/core/iva_utils.py`
- **Decimal parsing:** use `parse_decimal_value()` from `iva_utils.py` (handles EU and US formats)

### Threading & UI
- **UI runs on main thread** — customtkinter
- **Heavy I/O on worker threads** — ThreadPoolExecutor
- **UI updates from workers:** use `.after(0, lambda: ...)` pattern
- **SQLite access:** always use `threading.Lock()` (see `ClassificationDB`)

### UI Color Palette (Dark Theme)
Consistent across all GUI modules:
- `BG="#0d0f14"`, `SURFACE="#13161e"`, `CARD="#181c26"`, `BORDER="#252a38"`
- `TEAL="#2dd4bf"` (accent), `TEXT="#e8eaf0"`, `MUTED="#6b7280"`
- `DANGER="#f87171"`, `SUCCESS="#34d399"`, `WARNING="#fbbf24"`

## Key External Dependencies

### Hacienda API (Costa Rica Tax Authority)

`https://api.hacienda.go.cr/fe/ae?identificacion={cedula}`

**Implementation:**
- `CRXMLManager` in `gestor_contable/core/xml_manager.py` handles API calls and caching
- **Cache location:** `Z:/DATA/hacienda_cache.db` (shared, read-only)
- **Timeout:** `hacienda_timeout` setting (default 10s)
- **Retries:** `hacienda_retries` setting (default 2)

### Python Packages

Required (see `requirements.txt`):
- `customtkinter` ≥5.2 — UI framework
- `pymupdf` ≥1.24 — PDF rendering (fitz)
- `pandas` ≥2.0 — data processing
- `openpyxl` ≥3.1 — Excel I/O
- `requests` ≥2.31 — HTTP client
- `cryptography` ≥42.0 — encryption
- `keyring` ≥24.0 — credential storage
