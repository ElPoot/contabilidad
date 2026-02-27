# Contexto del proyecto

Estoy desarrollando App 3, un clasificador contable visual en Python con CustomTkinter. La app lee facturas electrónicas de Costa Rica (XML + PDF), las muestra en una interfaz de 3 columnas (lista | visor PDF | panel de clasificación) y permite mover físicamente los PDFs a carpetas contables organizadas.

El stack es: Python 3.10+, CustomTkinter, pymupdf (fitz), pandas, SQLite. La app corre en Windows con archivos en una unidad de red Z:\.

Los módulos relevantes para esta tarea son:
- `app3/core/classifier.py` — lógica de movimiento de archivos y base de datos SQLite
- `app3/core/catalog.py` — gestión del catálogo de cuentas por cliente
- `app3/gui/main_window.py` — interfaz principal, panel de clasificación

---

# Lo que quiero implementar

## 1. Nueva estructura de rutas de destino

Cuando el contador clasifica una factura, el PDF debe moverse a esta estructura:

```
Z:\DATA\PF-{año}\Contabilidades\{mes}\{cliente}\
```

Donde `{mes}` es `01-ENERO`, `02-FEBRERO`, ... `12-DICIEMBRE` (con número para que ordene correctamente en Windows).

Dentro de la carpeta del cliente, la estructura depende de la categoría:

**COMPRAS:**
```
COMPRAS\{proveedor}\archivo.pdf
```
Simple. El proveedor es el nombre del emisor del XML. No hay subcarpetas adicionales.

**GASTOS:**
```
GASTOS\{tipo_de_gasto}\{nombre_cuenta}\{proveedor}\archivo.pdf
```
Donde `{tipo_de_gasto}` es `GASTOS ESPECIFICOS` o `GASTOS GENERALES`, y `{nombre_cuenta}` es la cuenta del catálogo (ej: ELECTRICIDAD, TELECOMUNICACIONES, ALQUILER).

**OGND (Gastos No Deducibles):**
```
OGND\{tipo}\archivo.pdf
```
Donde `{tipo}` es `OGND`, `DNR`, `ORS` o `CNR`. No lleva proveedor porque estos gastos no son deducibles y no se necesita tanta granularidad.

**Regla crítica:** Las carpetas NUNCA se crean vacías. La carpeta se crea en el momento exacto en que se mueve el primer PDF a ella. Si un cliente ese mes no tuvo gastos de electricidad, la carpeta de electricidad no existe.

**Movimiento atómico obligatorio:**
1. Calcular SHA256 del original
2. Copiar al destino
3. Calcular SHA256 de la copia
4. Si difieren: borrar copia, lanzar error, original intacto
5. Solo si coinciden: borrar original
6. Registrar en SQLite

---

## 2. Panel de clasificación mejorado

El panel derecho de la interfaz actualmente tiene dos ComboBox genéricos (categoría y subcategoría) que no reflejan la jerarquía real del catálogo. Necesito reemplazarlo con un flujo inteligente de 4 niveles que cambia dinámicamente según lo que el contador seleccione.

**Flujo para COMPRAS:**
```
[Categoría: COMPRAS]
[Proveedor: input de texto — prellenado con emisor del XML]
→ Clasificar
```

**Flujo para GASTOS:**
```
[Categoría: GASTOS]
[Tipo: GASTOS GENERALES | GASTOS ESPECIFICOS]  ← ComboBox
[Cuenta: ELECTRICIDAD / TELECOMUNICACIONES / ...]  ← ComboBox que cambia según el tipo
[Proveedor: input de texto]
→ Clasificar
```

**Flujo para OGND:**
```
[Categoría: OGND]
[Tipo: OGND | DNR | ORS | CNR]  ← ComboBox
→ Clasificar  (sin proveedor)
```

El panel debe mostrar un preview de la ruta destino en texto pequeño antes del botón Clasificar, para que el contador vea exactamente a dónde va el archivo. Algo como:
`…/Contabilidades/02-FEBRERO/NOMBRE CLIENTE/GASTOS/GASTOS GENERALES/ELECTRICIDAD/CNFL/`

Los ComboBox de cuenta deben actualizarse automáticamente al cambiar de tipo. El catálogo se carga desde `.metadata/catalogo_cuentas.json` que existe por cliente.

---

## 3. Catálogo de cuentas

El catálogo se lee desde un archivo `.dm` con formato `CODIGO|NOMBRE|PADRE`. Te adjunto el catálogo estándar del despacho. Este catálogo es el punto de partida — cada cliente puede tener variaciones (un hotel tiene cuentas que una soda no tiene).

La jerarquía del catálogo es:

```
5000 COMPRAS
  5010 COMPRAS DE CONTADO
  5020 COMPRAS DE CREDITO

6000 GASTOS
  6100 GASTOS ESPECIFICOS
    6110 ALQUILER
    6120 COMISIONES
    6130 GASTOS FINANCIEROS
    6140 HONORARIOS PROFESIONALES
  6200 GASTOS GENERALES
    6210 ACUEDUCTOS
    6220 ACCESORIOS PARA EL HOTEL
    ... (ver .dm adjunto)

7000 GASTOS NO DEDUCIBLES  (va a carpeta OGND, no a GASTOS)
  7100 OGND
  7200 DNR
  7300 ORS
  7400 CNR
```

`CatalogManager` debe:
- Leer el `.dm` y construir la jerarquía en memoria
- Guardar por cliente en `.metadata/catalogo_cuentas.json`
- Exponer métodos: `categorias()`, `subtipos(categoria)`, `cuentas(categoria, subtipo)`
- Permitir agregar cuentas nuevas que se persistan inmediatamente

---

## 4. Base de datos actualizada

La tabla `clasificaciones` en SQLite necesita columnas adicionales para reflejar la jerarquía nueva:

```sql
CREATE TABLE clasificaciones (
    clave_numerica      TEXT PRIMARY KEY,
    estado              TEXT,
    categoria           TEXT,   -- COMPRAS | GASTOS | OGND
    subtipo             TEXT,   -- GASTOS GENERALES | GASTOS ESPECIFICOS | OGND | DNR...
    nombre_cuenta       TEXT,   -- ELECTRICIDAD | ALQUILER | COMPRAS DE CONTADO...
    proveedor           TEXT,
    ruta_origen         TEXT,
    ruta_destino        TEXT,
    sha256              TEXT,
    fecha_clasificacion TEXT,
    clasificado_por     TEXT
);
```

Si la tabla ya existe de una versión anterior, usar `ALTER TABLE ... ADD COLUMN` para migrar sin perder datos.

---

## Lo que NO quiero

- No crear carpetas vacías bajo ninguna circunstancia
- No hardcodear rutas — usar siempre `get_setting('network_drive')` de `settings.py`
- No bloquear la UI — el movimiento de archivos va en hilo secundario con `threading.Thread`
- No perder el original si falla la copia

---

## Archivos que debes modificar

1. `app3/core/classifier.py` — función `build_dest_folder()` y `classify_record()`
2. `app3/core/catalog.py` — clase `CatalogManager` completa
3. `app3/gui/main_window.py` — método `_build_classify_panel()`, `_on_categoria_change()`, `_on_subtipo_change()`, `_classify_selected()`

Muéstrame los 3 archivos completos al final.
