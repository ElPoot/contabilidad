# App 3 — Clasificador Contable Visual
> Reemplaza a App 2 (procesador XML) e integra clasificación contable asistida visualmente.  
> Mientras se desarrolla, App 1 y App 2 siguen funcionando sin cambios.

---

## Contexto

El despacho contable descarga facturas electrónicas (XML + PDF) desde correos de clientes usando App 1. App 2 genera reportes Excel desde esos XMLs. El problema es que clasificar cada PDF en la carpeta contable correcta se hace completamente a mano.

**App 3 resuelve eso:** el contador ve la factura en pantalla, elige la categoría contable con unos clics, y el sistema organiza los archivos automáticamente dejando registro de todo.

---

## Relación con apps existentes

| App | Estado | Descripción |
|-----|--------|-------------|
| **App 1 — Mass Download** | Sigue funcionando | Descarga adjuntos Gmail/IMAP, organiza XML y PDF por cliente |
| **App 2 — Procesador XML** | Sigue funcionando | Lee XMLs, extrae campos, exporta Excel |
| **App 3 — Clasificador** | Nueva | Unifica todo y agrega clasificación visual. Cuando esté estable reemplaza App 2 |

### Código reutilizable de apps anteriores

- `xml_manager.py` (App 2) — parsing XML, extracción clave numérica, estado Hacienda, resolución nombres
- `pdf_classifier.py` (App 1) — lectura de claves numéricas desde PDFs
- `client_profiles.py` (App 1) — resolución carpeta de cliente por cédula
- `settings.py` (App 1) — configuración centralizada, rutas de red, años fiscales
- `security.py` (App 1) — bóveda cifrada AES-256-GCM

---

## Estructura de archivos en disco

App 1 ya crea esta estructura. App 3 la lee y escribe en ella:

```
Z:/DATA/
  PF-2026/
    CLIENTES/
      NOMBRE_CLIENTE/
        XML/              ← facturas electrónicas (.xml)
        PDF/              ← representaciones visuales (.pdf)
          REMITENTE/      ← subcarpeta por remitente (App 1)
        OTROS/
        .metadata/
          state.sqlite         ← registro descargas (App 1)
          clasificacion.sqlite ← registro clasificaciones (App 3, nuevo)
          catalogo_cuentas.json ← catálogo contable del cliente (App 3, nuevo)
  CONFIG/
    client_profiles.json
    settings.json
```

### Vínculo entre archivos

Cada factura puede tener hasta 3 archivos relacionados por **clave numérica de 50 dígitos**:
- XML de la factura (FacturaElectronica, TiqueteElectronico, etc.)
- XML de respuesta de Hacienda (MensajeHacienda)
- PDF de representación visual

**Excepciones frecuentes** que la app debe manejar sin errores:
- Solo PDF sin XML (proveedor mandó solo el impreso)
- Solo XML sin PDF
- PDFs dentro de subcarpetas por remitente — la búsqueda debe ser recursiva

---

## Flujo de sesión

### 1. Inicio por cédula de cliente

```
Usuario ingresa cédula
  → Busca en hacienda_cache.db local
  → Si no está: consulta api.hacienda.go.cr
  → Si no existe: "Cliente no válido o no encontrado"
  → Si existe: muestra nombre y pide confirmación
  → Busca carpeta PF-{año}/CLIENTES/{nombre_exacto}/
  → Si no existe en disco: "Nombre X no tiene datos en esta PC"
  → Si existe: carga sesión y habilita interfaz
```

**Cambio de cliente:** el usuario debe cerrar sesión explícitamente. Limpia todo en memoria y vuelve a la pantalla de cédula.

### 2. Selección de período

- Campos Desde / Hasta en formato DD/MM/AAAA
- Botones rápidos: Este mes / Mes anterior / Este año
- Al confirmar: carga XMLs del cliente cuya fecha de emisión esté en el rango
- Los PDFs se identifican pero **no se cargan todos en memoria**, solo el seleccionado

---

## Interfaz principal

Layout de dos columnas **40% izquierda / 60% derecha**:

### Panel izquierdo — Lista de facturas

Cada fila muestra:
- Indicador de estado: `●` verde (clasificada) / `○` gris (pendiente) / `⚠` sin PDF / `✗` sin XML
- Fecha de emisión
- Nombre del emisor (resuelto desde Hacienda)
- Monto total
- Tipo de documento

Filtros disponibles: todas / pendientes / clasificadas / por clave numérica / por emisor / por monto

Barra de progreso permanente: `X de Y clasificadas (Z%)`

### Panel derecho — Visor y clasificador

**Parte superior:** visor de PDF
- Renderizado con `pymupdf (fitz)` — sin dependencias externas ni Adobe
- Controles de zoom y navegación de páginas
- Si no hay PDF: mensaje claro pero permite clasificar igual basándose en el XML

**Parte inferior:** panel de clasificación

```
Paso 1 → Seleccionar categoría principal (Ingresos / Compras / Gastos / etc.)
Paso 2 → Seleccionar subcategoría (si aplica)
Paso 3 → Nombre del proveedor (opcional, por defecto usa emisor del XML)
Paso 4 → Botón "Clasificar" → ejecuta movimiento del archivo
```

El catálogo es **editable en tiempo real**: botón `+` junto al selector agrega una categoría nueva que queda guardada inmediatamente para ese cliente.

---

## Lógica de clasificación

### Ruta de destino

```
Z:/DATA/PF-{año}/CLIENTES/{nombre_cliente}/{categoria}/{subcategoria}/{proveedor}/
```

Ejemplos:
```
.../SUPERMERCADO XYZ/COMPRAS/COMPRAS DE CONTADO/DISTRIBUIDORA CARNES S.A./
.../SUPERMERCADO XYZ/GASTOS/GASTOS GENERALES/PAPELERIA Y UTILES DE OFICINA/LIBRERÍA UNIVERSAL/
.../SUPERMERCADO XYZ/GASTOS/GASTOS GENERALES/ELECTRICIDAD/CNFL/
```

### Movimiento seguro — CRÍTICO

Un error aquí puede perder documentos fiscales. El proceso es **atómico**:

```
1. Calcular SHA256 del PDF original
2. Crear carpeta destino (mkdir parents=True)
3. Copiar archivo al destino (shutil.copy2)
4. Calcular SHA256 de la copia
5. Comparar SHA256 → si difieren: borrar copia, lanzar error, ORIGINAL INTACTO
6. Solo si SHA256 coincide: borrar original
7. Registrar en clasificacion.sqlite
```

**Nunca borrar el original antes de verificar la copia.**

### Casos especiales

| Caso | Comportamiento |
|------|---------------|
| Solo PDF sin XML | Clasificar normalmente, advertencia "Sin XML asociado" |
| Solo XML sin PDF | Clasificar como "pendiente de documento", no mueve archivos |
| PDF ya clasificado | Muestra clasificación anterior, pide confirmación para reclasificar |
| Nombre duplicado en destino | Agregar sufijo con primeros 8 chars del SHA256 |
| Nota de Crédito | Fondo verde en lista, montos en negativo, clasificación normal |
| Moneda extranjera | Muestra tipo de cambio del XML, clasificación normal |

---

## Base de datos de clasificación

**Ubicación:** `.metadata/clasificacion.sqlite` dentro de la carpeta del cliente

```sql
CREATE TABLE clasificaciones (
  clave_numerica       TEXT PRIMARY KEY,
  estado               TEXT,  -- 'clasificado' | 'pendiente_pdf' | 'sin_xml'
  categoria            TEXT,
  subcategoria         TEXT,
  proveedor            TEXT,
  ruta_origen          TEXT,
  ruta_destino         TEXT,
  sha256               TEXT,
  fecha_clasificacion  TEXT,
  clasificado_por      TEXT   -- reservado para uso futuro multi-usuario
);
```

Una BD por cliente por año fiscal.

---

## Catálogo de cuentas

**Ubicación:** `.metadata/catalogo_cuentas.json` dentro de la carpeta del cliente

El catálogo **varía por cliente** (una panadería no es igual a un hotel). Estructura:

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
      "PAPELERIA Y UTILES DE OFICINA": {},
      "TELECOMUNICACIONES": {},
      "TRANSPORTE": {}
    },
    "GASTOS NO DEDUCIBLES": {
      "OGND": {}, "DNR": {}, "ORS": {}, "CNR": {}
    }
  }
}
```

- Agregar categoría: botón `+` en la UI, guarda inmediatamente
- Eliminar categoría: solo si no tiene facturas clasificadas en ella
- Importar catálogo: desde JSON externo para inicializar cliente nuevo con catálogo estándar del despacho

---

## Arquitectura técnica

### Stack

| Componente | Tecnología |
|-----------|-----------|
| Interfaz | Python 3.10+ + CustomTkinter |
| Visor PDF | `pymupdf (fitz)` |
| BD clasificación | SQLite + `threading.Lock` |
| Catálogo cuentas | JSON con escritura atómica |
| Parsing XML | `CRXMLManager` (reutilizado de App 2) |
| Lectura claves PDF | `pdf_classifier.py` (reutilizado de App 1) |
| Configuración | `settings.py` (reutilizado de App 1) |
| Hash archivos | `hashlib.sha256` |

### Estructura de módulos sugerida

```
app3/
  main.py                   ← punto de entrada
  config.py                 ← rutas y constantes
  core/
    xml_manager.py          ← de App 2
    pdf_reader.py           ← de App 1
    clasificador.py         ← movimiento seguro + BD
    catalogo.py             ← gestión catálogo por cliente
    cliente_session.py      ← sesión por cédula/carpeta
  gui/
    session_view.py         ← pantalla inicio de sesión
    main_view.py            ← layout principal
    factura_list.py         ← lista con estados
    pdf_viewer.py           ← visor con zoom/páginas
    clasificador_panel.py   ← selección de categoría
    catalogo_editor.py      ← editor en tiempo real
```

### Threading — regla crítica

Siguiendo el patrón correcto de App 2 (Queue + polling):

```python
# CORRECTO — actualizar UI desde hilo secundario
self.after(0, lambda: self.label.configure(text="..."))

# INCORRECTO — causa crashes aleatorios
self.label.configure(text="...")  # desde hilo secundario
```

Operaciones que van en hilo secundario: carga de XMLs, consultas API Hacienda, movimiento de archivos.

---

## Criterios para reemplazar App 2

App 3 reemplaza a App 2 cuando cumpla todo esto:

- [ ] Inicio de sesión por cédula funciona y rechaza clientes inválidos
- [ ] Lista de facturas muestra estados correctamente
- [ ] Visor PDF carga cualquier factura electrónica CR sin errores
- [ ] Movimiento seguro nunca pierde archivos (SHA256 verificado)
- [ ] Catálogo editable en tiempo real con persistencia
- [ ] Reclasificación actualiza registro correctamente
- [ ] Casos de excepción no rompen la app
- [ ] Todas las operaciones en hilos secundarios, UI no se congela
- [ ] Funciona correctamente con 500+ facturas por cliente
- [ ] Exporta el mismo reporte Excel que App 2 (funcionalidad heredada)

---

## Notas para desarrollo con IA

- Usar siempre `pathlib.Path` — el sistema corre en Windows con rutas `Z:/DATA/`
- Nombres de carpetas pueden tener caracteres especiales — usar `sanitize_folder_name()` de `file_manager.py`
- Clave numérica de Hacienda CR = exactamente **50 dígitos**
- Drive `Z:/` puede no estar disponible — todas las operaciones con `try/except` y mensajes claros
- No hardcodear rutas — usar siempre `get_setting('network_drive')` de `settings.py`
- Para SQLite con múltiples hilos: usar `threading.Lock()` como en `StateDB` de `gmail_utils.py`
- Para PDFs firmados digitalmente: usar `pymupdf`, no `PyPDF2`

---

## Estado implementación App 3 (v1 inicial)

Se agregó una primera versión ejecutable en `app3/` que reutiliza lógica de App 1 y App 2:

- Reusa `CRXMLManager` (App 2) para parsing XML y datos de factura.
- Reusa `extract_clave_and_cedula` (App 1) para asociar PDFs con clave numérica.
- Reusa `settings.py` y `client_profiles.py` (App 1) para resolver rutas y sesión cliente.
- Implementa `clasificacion.sqlite` y movimiento seguro con SHA256 antes de borrar origen.
- Implementa `catalogo_cuentas.json` con guardado atómico.

### Ejecutar App 3 v1

```bash
python -m app3.main
```

### Dependencias (App 3 v1)

Instalar dependencias base:

```bash
pip install -r requirements.txt
```

Para desarrollo y pruebas:

```bash
pip install -r requirements-dev.txt
```
