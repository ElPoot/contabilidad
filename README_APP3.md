# App 3 — Clasificador Contable Visual

> Unifica App 1 (descarga) y App 2 (XML) y agrega clasificación contable visual.  
> App 1 y App 2 siguen funcionando sin cambios mientras App 3 se estabiliza.

---

## Contexto

El despacho contable descarga facturas electrónicas (XML + PDF) usando App 1, y genera reportes Excel con App 2. La clasificación de cada factura en la carpeta contable correcta se hacía manualmente.

**App 3 resuelve eso:** el contador selecciona una factura de la lista, la ve en pantalla, elige categoría y subcategoría con un clic, y el sistema mueve el archivo y registra todo automáticamente.

---

## Relación con apps existentes

| App | Estado | Rol |
|-----|--------|-----|
| **App 1 — Mass Download** | Sigue funcionando | Descarga Gmail/IMAP, organiza XML y PDF por cliente |
| **App 2 — Procesador XML** | Sigue funcionando | Parsing XML, extracción de campos, reporte Excel |
| **App 3 — Clasificador** | En desarrollo | Unifica todo + clasificación visual. Reemplaza App 2 cuando esté estable |

### Módulos reutilizados

| Módulo | Origen | Uso en App 3 |
|--------|--------|--------------|
| `xml_manager.py` / `CRXMLManager` | App 2 | Parsing XML, extracción de campos, estado Hacienda, IVA desglosado |
| `pdf_classifier.py` | App 1 | Lectura de clave numérica desde nombre/contenido de PDF |
| `client_profiles.py` | App 1 | Resolución carpeta de cliente por cédula |
| `settings.py` | App 1 | Rutas de red, años fiscales, configuración centralizada |
| `security.py` | App 1 | Bóveda cifrada AES-256-GCM |

---

## Estructura de módulos

```
app3/
  main.py                  ← punto de entrada
  config.py                ← rutas, constantes, metadata_dir, client_root
  bootstrap.py             ← compatibilidad con rutas legacy de App 1/2
  core/
    models.py              ← FacturaRecord (dataclass con todos los campos XML + IVA)
    factura_index.py       ← FacturaIndexer — carga XMLs vía CRXMLManager, vincula PDFs
    classifier.py          ← ClassificationDB (SQLite) + classify_record (movimiento seguro)
    catalog.py             ← CatalogManager — catálogo de cuentas por cliente (JSON)
    session.py             ← ClientSession, resolve_client_session
  gui/
    main_window.py         ← App3Window — layout principal de 3 columnas
    session_view.py        ← SessionView — pantalla inicio/cambio de cliente
    pdf_viewer.py          ← PDFViewer — visor con zoom, Ctrl+scroll, copia de texto
```

---

## Estructura de archivos en disco

App 1 crea esta estructura. App 3 la lee y escribe en ella:

```
Z:/DATA/
  PF-2026/
    CLIENTES/
      NOMBRE_CLIENTE/
        XML/                     ← facturas electrónicas (.xml)
        PDF/                     ← representaciones visuales (.pdf)
          REMITENTE/             ← subcarpeta por remitente (App 1)
        OTROS/
        .metadata/
          state.sqlite           ← registro descargas (App 1)
          clasificacion.sqlite   ← registro clasificaciones (App 3)
          catalogo_cuentas.json  ← catálogo contable del cliente (App 3)
  CONFIG/
    client_profiles.json
    settings.json
```

### Vínculo entre archivos

Cada factura se vincula por **clave numérica de 50 dígitos** presente en el nombre del archivo:
- XML de la factura (`FacturaElectronica`, `TiqueteElectronico`, etc.)
- XML de respuesta Hacienda (`MensajeHacienda`)
- PDF de representación visual

**Excepciones frecuentes manejadas sin error:**
- Solo PDF sin XML → estado `sin_xml`
- Solo XML sin PDF → estado `pendiente_pdf`
- PDFs en subcarpetas por remitente → búsqueda recursiva

---

## Interfaz principal

Layout de **3 columnas** con proporciones `3 : 7 : 2`:

### Columna izquierda — Lista de facturas

Tabla compacta (fuente 9px, filas 19px) con columnas:

| Col | Contenido |
|-----|-----------|
| Estado | `✓ clasificado` / `· pendiente` / `! sin PDF` / `— sin XML` |
| Fecha | DD/MM/AAAA |
| Tipo | `FE` / `NC` / `ND` / `TQ` |
| Emisor | Nombre abreviado (S.A., S.R.L., etc.) |
| Mon. | `CRC` / `USD` |
| Total | `137 131,77` (miles=espacio, decimal=coma) |

Barra de progreso: `X/Y (Z%)` en teal sobre el header.

### Columna central — Visor PDF

`PDFViewer` con:
- **Fit-to-width automático** al cargar (se ajusta al ancho disponible)
- **Ctrl + scroll** → zoom in/out continuo (30%–300%, pasos de 10%)
- **Scroll normal** → desplazamiento vertical
- **Clic derecho** → copia la línea de texto bajo el cursor al portapapeles
- **Botón ↺** → vuelve al fit-to-width
- **Resize reactivo** → recalcula fit si se redimensiona la ventana

### Columna derecha — Clasificación

Panel delgado (`minsize=200`) con solo lo necesario:
1. Pill de estado Hacienda (`✓ Aceptada` verde / `⚠ Rechazada` amarillo)
2. Categoría (ComboBox del catálogo)
3. Subcategoría (ComboBox dependiente)
4. Proveedor (Entry prellenado con emisor del XML)
5. Botón **✔ Clasificar**
6. Sección ANTERIOR con la última clasificación registrada

---

## Flujo de sesión

```
Pantalla inicio (SessionView)
  → Input cédula con debounce 500ms
  → Consulta resolve_party_name() de CRXMLManager (cache local primero)
  → Muestra nombre en teal si encontrado
  → Lista accesos rápidos (clientes con datos en disco)
  → Confirmar → ClientSession → App3Window
  
Botón "⇄ Cambiar cliente"
  → Reabre SessionView sin cerrar la ventana principal
  → Al confirmar nuevo cliente → recarga todo
```

---

## Modelo de datos — FacturaRecord

```python
@dataclass
class FacturaRecord:
    clave: str                  # 50 dígitos Hacienda

    # Identificación
    fecha_emision: str
    tipo_documento: str         # "Factura Electrónica", "Nota de Crédito", etc.
    consecutivo: str

    # Partes
    emisor_nombre: str
    emisor_cedula: str
    receptor_nombre: str
    receptor_cedula: str

    # Montos
    subtotal: str
    iva_1: str                  # IVA por tasa (vacío si no aplica)
    iva_2: str
    iva_4: str
    iva_8: str
    iva_13: str
    impuesto_total: str
    total_comprobante: str
    moneda: str                 # "CRC" | "USD"
    tipo_cambio: str

    # Hacienda
    estado_hacienda: str        # "Aceptado" | "Rechazado" | ""

    # Rutas
    xml_path: Path | None
    pdf_path: Path | None

    # Estado App 3
    estado: str                 # "pendiente" | "pendiente_pdf" | "sin_xml" | "clasificado"
```

---

## Lógica de clasificación

### Ruta de destino

```
Z:/DATA/PF-{año}/CLIENTES/{cliente}/{categoria}/{subcategoria}/{proveedor}/
```

### Movimiento seguro — CRÍTICO

```
1. SHA256 del PDF original
2. mkdir parents=True en destino
3. shutil.copy2 al destino
4. SHA256 de la copia
5. Si SHA256 difieren → borrar copia, error, ORIGINAL INTACTO
6. Solo si coinciden → borrar original
7. Registrar en clasificacion.sqlite
```

**Nunca borrar el original antes de verificar la copia.**

### Casos especiales

| Caso | Comportamiento |
|------|----------------|
| Solo PDF sin XML | Clasifica normalmente, estado `sin_xml` |
| Solo XML sin PDF | No mueve archivos, estado `pendiente_pdf` |
| Ya clasificado | Muestra clasificación anterior, permite reclasificar |
| Nombre duplicado en destino | Sufijo con primeros 8 chars del SHA256 |
| Nota de Crédito | Monto en negativo, clasificación normal |
| Moneda extranjera | Muestra tipo de cambio del XML |

---

## Base de datos — clasificacion.sqlite

Una BD por cliente por año. Ubicación: `.metadata/clasificacion.sqlite`

```sql
CREATE TABLE clasificaciones (
    clave_numerica      TEXT PRIMARY KEY,
    estado              TEXT,   -- 'clasificado' | 'pendiente_pdf' | 'sin_xml'
    categoria           TEXT,
    subcategoria        TEXT,
    proveedor           TEXT,
    ruta_origen         TEXT,
    ruta_destino        TEXT,
    sha256              TEXT,
    fecha_clasificacion TEXT,
    clasificado_por     TEXT    -- reservado multi-usuario
);
```

Acceso thread-safe con `threading.Lock()`.

---

## Catálogo de cuentas — catalogo_cuentas.json

Varía por cliente. Ubicación: `.metadata/catalogo_cuentas.json`

```json
{
  "INGRESOS": {
    "FACTURAS ELECTRONICAS": {},
    "TIQUETES ELECTRONICOS": {}
  },
  "COMPRAS": {
    "COMPRAS DE CONTADO": {},
    "COMPRAS DE CREDITO": {}
  },
  "GASTOS": {
    "GASTOS ESPECIFICOS": {
      "ALQUILER": {},
      "HONORARIOS PROFESIONALES": {}
    },
    "GASTOS GENERALES": {
      "ELECTRICIDAD": {},
      "PAPELERIA Y UTILES": {},
      "TELECOMUNICACIONES": {},
      "TRANSPORTE": {}
    },
    "GASTOS NO DEDUCIBLES": {
      "OGND": {}, "DNR": {}, "ORS": {}, "CNR": {}
    }
  }
}
```

---

## Stack técnico

| Componente | Tecnología |
|-----------|-----------|
| UI | Python 3.10+ + CustomTkinter |
| Tabla lista | `tkinter.ttk.Treeview` (CTk no tiene tabla nativa) |
| Visor PDF | `pymupdf (fitz) >= 1.24` — renderizado, zoom, texto |
| BD clasificación | SQLite + `threading.Lock` |
| Parsing XML | `CRXMLManager` de App 2 (`pandas`, `openpyxl`) |
| Lectura claves PDF | `pdf_classifier.py` de App 1 |
| Config/rutas | `settings.py` de App 1 |
| Seguridad | `cryptography`, `keyring` de App 1 |
| Hash archivos | `hashlib.sha256` (stdlib) |

---

## Notas para desarrollo con IA

- `pathlib.Path` siempre — rutas Windows `Z:/DATA/`
- Nombres de carpeta con caracteres especiales → `sanitize_folder_name()` de `file_manager.py`
- Clave numérica Hacienda CR = exactamente **50 dígitos**
- Drive `Z:/` puede no estar montado → `try/except` en toda operación de disco con mensaje claro
- No hardcodear rutas → `get_setting('network_drive')` de `settings.py`
- SQLite multi-hilo → `threading.Lock()` como en `StateDB` de `gmail_utils.py`
- Actualizar UI desde hilo secundario → `self.after(0, lambda: ...)` — nunca directo
- **pdfplumber ya no se usa** — fue reemplazado por `pymupdf` para renderizado visual

---

## Checklist para reemplazar App 2

- [ ] Inicio de sesión por cédula funciona y rechaza clientes inválidos
- [ ] Lista muestra todos los estados correctamente con formato numérico correcto
- [ ] Visor PDF carga cualquier factura CR, fit-to-width automático, sin espacio vacío
- [ ] Ctrl+scroll para zoom funciona
- [ ] Clic derecho copia texto del PDF
- [ ] Clasificación mueve archivos con verificación SHA256
- [ ] Catálogo editable en tiempo real con persistencia
- [ ] Reclasificación actualiza registro y mueve archivo
- [ ] Casos de excepción (sin XML, sin PDF, duplicados) no rompen la app
- [ ] Todas las operaciones lentas en hilos secundarios — UI no se congela
- [ ] Funciona correctamente con 500+ facturas por cliente
- [ ] Exporta el mismo reporte Excel que App 2
