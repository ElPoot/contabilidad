# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Accounting toolset for a Costa Rican accounting firm. Three apps that work together:

| App | Path | Status | Purpose |
|-----|------|--------|---------|
| **App 1 — Mass Download** | `APP 1/facturacion_system/` | Stable | Downloads Gmail/IMAP attachments, organizes XML and PDF by client |
| **App 2 — XML Processor** | `APP 2/facturacion/` | Stable | Parses XMLs, extracts fields, exports Excel reports |
| **App 3 — Clasificador** | `app3/` | In development | Visual classifier; fully independent; will replace App 2 |

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

### App 3 Independence

App 3 is **fully independent** — no imports from App 1 or App 2:

- ✅ Has its own native modules: `xml_manager.py`, `settings.py`, `client_profiles.py`, `classifier.py`, etc.
- ✅ Uses FILES from Apps 1 & 2 (XMLs, PDFs, Hacienda cache) but not their code
- ✅ IVA parsing logic is replicated natively in `iva_utils.py`

For detailed module breakdown, see `app3/core/` and supporting documentation.

### App 3 Module Structure

**Core Business Logic:**

```
app3/core/
  models.py              ← FacturaRecord: all XML fields + IVA + state
  xml_manager.py         ← CRXMLManager: XML parsing, Hacienda API, cache
  factura_index.py       ← FacturaIndexer: loads XMLs, links PDFs, extracts claves
  classifier.py          ← ClassificationDB (SQLite) + classify_record (atomic file move)
  catalog.py             ← CatalogManager: per-client account catalog (JSON)
  session.py             ← ClientSession, resolve_client_session (login by tax ID)
  pdf_cache.py           ← PDFCacheManager: caches PDF extraction results
  settings.py            ← get_setting(): configuration management
  client_profiles.py     ← load_profiles(): client folder mappings
  iva_utils.py           ← parse_decimal_value(): IVA parsing (replicated from App 2)
  classification_utils.py ← filter records, get statistics, classify transactions
```

**GUI:**

```
app3/gui/
  main_window.py         ← App3Window: 3-column layout (invoice list / PDF viewer / classifier)
  session_view.py        ← SessionView: login modal by tax ID (cédula)
  pdf_viewer.py          ← PDFViewer: pymupdf rendering, zoom, right-click text copy
  loading_modal.py       ← LoadingOverlay: loading spinner during XML/PDF scan
```

**Entry Points:**

```
app3/
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
        XML/                      ← XMLs (created by App 1, read by App 3)
        PDF/                      ← PDFs (created by App 1, read by App 3)
          SENDER/                 ← subfolder by sender (App 1)
        .metadata/
          state.sqlite            ← download history (App 1)
          pdf_cache.json          ← PDF scan results (App 3)
          clasificacion.sqlite    ← classification records (App 3)
          catalogo_cuentas.json   ← per-client account catalog (App 3)
    Contabilidades/               ← destination folder for classified PDFs (App 3)
      {mes}/{client_name}/
        COMPRAS/...
        GASTOS/...
        OGND/...
  CONFIG/
    client_profiles.json          ← client mappings (App 1 & App 3)
    settings.json                 ← configuration (App 3)
  hacienda_cache.db               ← Hacienda API cache (App 2 & App 3)
```

**Key point:** App 3 reads from files created by App 1, but does not modify them.

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

## Migration Status (App 3)

### ✅ COMPLETED — Full Independence from Apps 1 & 2

**What was migrated/replicated:**
- XML parsing → `app3/core/xml_manager.py` (native CRXMLManager)
- PDF scanning & clave extraction → `app3/core/factura_index.py`
- IVA calculation → `app3/core/iva_utils.py`
- Classification & file moves → `app3/core/classifier.py`
- Configuration management → `app3/core/settings.py`
- Client profiles → `app3/core/client_profiles.py`
- Hacienda API integration → `app3/core/xml_manager.py`

**What is shared (read-only):**
- XMLs & PDFs on network drive (created by App 1, read by App 3)
- Hacienda cache database (preferentially uses App 2's cache if available)
- Client profiles JSON (created by App 1, read by App 3)

**No code dependencies:**
- ❌ Does NOT import from App 1 (`facturacion_system`)
- ❌ Does NOT import from App 2 (`facturacion`)

---

## Critical Business Rules (App 3)

### Safe File Move (ATOMIC & NEVER SKIP)

Invoices are **fiscal documents**. A failed move that loses the original is critical. The atomic protocol in `app3/core/classifier.py:classify_record()`:

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

### ⚠️ CRITICAL: App 3 Independence Rule

**If you need logic that exists in App 1 or App 2 → replicate it natively in App 3. NEVER import from `facturacion_system` or `facturacion`.**

App 3 is 100% independent. All business logic and utilities must be written in `app3/` or use only standard library/external packages. Code from Apps 1 & 2 is not available — and that's intentional.

### Imports & Dependencies
- **NO imports from App 1 (`facturacion_system`) or App 2 (`facturacion`)** — App 3 is fully independent
- **All modules must be in `app3/` or its subdirectories**
- When you need logic that exists elsewhere, replicate it natively in App 3 (see examples: `iva_utils.py`, `xml_manager.py`, `client_profiles.py`)

### File System
- **Always use `pathlib.Path`** — Windows paths with `Z:/DATA/` notation
- **Never hardcode paths** — use `get_setting('network_drive')` from `app3/core/settings.py`
- **Folder names may have special characters** — use `_sanitize_folder()` from `app3/core/classifier.py`
- **`Z:/` may be unavailable** — wrap all disk operations in `try/except` with clear error messages

### Data Handling
- **Hacienda numeric key = exactly 50 digits**
- **Use `pymupdf` (`fitz`) for all PDF work** — `pdfplumber` is no longer used
- **IVA rates:** see `IVA_TARIFA_CODE_MAP` in `app3/core/iva_utils.py`
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

**App 3 Implementation:**
- `CRXMLManager` in `app3/core/xml_manager.py` handles API calls and caching
- **Cache preference:** `APP 2/data/hacienda_cache.db` if exists (shared with App 2)
- **Fallback:** `app3/data/hacienda_cache.db` if App 2 cache not found
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
