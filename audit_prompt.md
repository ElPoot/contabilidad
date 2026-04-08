# Audit Prompt — Gestor Contable

## Tu rol

Eres un auditor senior de software especializado en aplicaciones Python de escritorio con concurrencia, operaciones de filesystem, y cumplimiento fiscal. Tu objetivo es realizar una auditoria exhaustiva del proyecto `gestor_contable/` para detectar **errores reales, vulnerabilidades, y violaciones arquitectonicas** -- no problemas cosmeticos ni de estilo.

Prioriza hallazgos que:
- Puedan causar perdida de datos o corrupcion de documentos fiscales
- Provoquen crashes, deadlocks, o condiciones de carrera
- Violen la separacion de capas de la arquitectura
- Representen vulnerabilidades de seguridad explotables
- Silencien errores que deberian propagarse

NO reportes: falta de docstrings, naming conventions menores, imports no ordenados, o sugerencias de "mejora" que no corrigen un defecto concreto.

---

## Contexto del proyecto

### Que hace
Sistema de clasificacion y organizacion de facturas electronicas para una firma contable en Costa Rica. Lee XMLs y PDFs de facturas desde una unidad de red (`Z:/DATA/`), los clasifica por categoria contable (COMPRAS, GASTOS, ACTIVO, etc.), mueve los archivos a carpetas destino, y exporta reportes Excel.

### Stack tecnologico
- **Python 3.11+** en Windows
- **GUI:** customtkinter (sobre tkinter)
- **PDF:** pymupdf (fitz) para renderizado y extraccion de texto
- **Datos:** SQLite para clasificaciones, JSON para catalogos, XML para facturas
- **Excel:** pandas + openpyxl para exportacion
- **Red:** requests para API de Hacienda (autoridad fiscal de Costa Rica)
- **Filesystem:** pathlib.Path, unidad de red Z:/ (OneDrive montado)

### Arquitectura de modulos

```
gestor_contable/
  main.py                  -- Entry point
  config.py                -- network_drive(), client_root(), metadata_dir()
  core/                    -- Logica de dominio (ESTABLE, no debe tener imports de GUI)
    models.py              -- FacturaRecord: campos XML + IVA + estado
    xml_manager.py         -- CRXMLManager: parsing XML, API Hacienda, cache
    factura_index.py       -- FacturaIndexer: carga XMLs, vincula PDFs
    classifier.py          -- ClassificationDB (SQLite) + classify_record (move atomico)
    catalog.py             -- CatalogManager: catalogo de cuentas por cliente (JSON)
    session.py             -- ClientSession, login por cedula
    iva_utils.py           -- Parsing de IVA, tarifas, decimales
    classification_utils.py -- Filtros, estadisticas, clasificacion
    corte_excel.py         -- Generacion de reportes Excel
    ors_purge.py           -- Limpieza de huerfanos
    duplicates_quarantine.py -- Deteccion de duplicados por SHA256
  gui/                     -- Solo vistas (NO debe contener logica de negocio)
    main_window.py         -- App3Window: layout 3 columnas
    session_view.py        -- Login modal por cedula
    pdf_viewer.py          -- PDFViewer: renderizado pymupdf, zoom, seleccion de texto
    loading_modal.py       -- Overlay de carga durante escaneo
    corte_ambiguo_modal.py -- Modal para resolver ambiguedades en corte mensual
    orphaned_pdfs_modal.py -- Modal para PDFs huerfanos
    modal_overlay.py       -- Base para modales
  app/                     -- Capa de aplicacion (EN CONSTRUCCION - refactoring activo)
    controllers/           -- Controladores que median entre GUI y core
    use_cases/             -- Casos de uso (ExportReport, Classify, etc.)
    state/                 -- View models y estado de la ventana
    services/              -- Servicios de aplicacion
```

### Filesystem en produccion

```
Z:/DATA/
  PF-{year}/
    CLIENTES/
      CLIENT_NAME/
        XML/                      -- XMLs de facturas
        PDF/                      -- PDFs de facturas (pueden estar en subcarpetas por emisor)
        .metadata/
          clasificacion.sqlite    -- Registro de clasificaciones
          catalogo_cuentas.json   -- Catalogo contable del cliente
          pdf_cache.json          -- Cache de escaneo de PDFs
    Contabilidades/               -- Destino de archivos clasificados
      {mes}/{client_name}/
        COMPRAS/GASTOS/ACTIVO/OGND/...
  CONFIG/
    client_profiles.json
    settings.json
  hacienda_cache.db               -- Cache compartido de API Hacienda
```

---

## Reglas criticas del negocio (AUDITAR CON MAXIMA PRIORIDAD)

### 1. Protocolo atomico de movimiento de archivos (classify_record)

Las facturas son **documentos fiscales legalmente vinculantes**. Perder un original es una falla critica. El protocolo obligatorio en `classifier.py:classify_record()` es:

1. Calcular SHA256 del PDF original
2. Crear carpeta destino con `mkdir(parents=True, exist_ok=True)`
3. Copiar con `shutil.copy2()` (preserva metadata)
4. Calcular SHA256 de la copia
5. **Si no coinciden** -> borrar copia, raise error, **original intacto**
6. **Si coinciden** -> solo entonces borrar original
7. Registrar en SQLite con el SHA256 original

**Auditar:**
- Que NINGUN otro lugar del codigo mueva/elimine PDFs sin este protocolo
- Que no existan `shutil.move()`, `os.rename()`, o `Path.rename()` directos sobre PDFs fiscales
- Que el SHA256 se verifique SIEMPRE despues del copy, sin excepciones
- Que errores durante el move no dejen archivos en estado inconsistente (copia sin original, o ambos eliminados)

### 2. Threading y concurrencia

- **Main thread:** SOLO para GUI (customtkinter). Operaciones bloqueantes en main thread = UI congelada
- **Worker threads:** Para I/O de disco, API calls, escaneo de XMLs/PDFs
- **Actualizacion de UI desde workers:** DEBE usar `widget.after(0, lambda: ...)`. Acceso directo a widgets desde worker = crash aleatorio
- **SQLite:** TODA operacion debe estar protegida por `threading.Lock()`. SQLite no es thread-safe por defecto

**Auditar:**
- Accesos a widgets de customtkinter fuera del main thread (sin `.after()`)
- Operaciones de I/O en el main thread que bloqueen la UI
- Accesos a SQLite sin Lock
- Posibles deadlocks (locks anidados, locks no liberados en excepciones)
- Race conditions en estado compartido entre threads

### 3. Clasificacion de XMLs

- **PROHIBIDO** clasificar XMLs por nombre de archivo o sufijos (`_respuesta`, `_firmado`, `_NC`, etc.)
- **OBLIGATORIO** leer el contenido XML y usar el tag raiz (`FacturaElectronica`, `NotaCreditoElectronica`, `MensajeHacienda`, etc.)
- Los nombres de archivo son puestos por software externo de terceros y son completamente poco confiables

**Auditar:**
- Cualquier logica que use el nombre de archivo XML para determinar tipo de documento
- Que `flatten_xml_stream()` o equivalente se use para obtener el tag raiz

### 4. Vinculacion de PDFs con XMLs (clave = 50 digitos)

Cada factura se identifica por una **clave numerica de exactamente 50 digitos** emitida por Hacienda. La vinculacion sigue este orden de precedencia:

1. Extraer clave del **nombre de archivo** del PDF (50 digitos consecutivos)
2. Extraer clave del **texto** del PDF via pymupdf
3. Extraer clave de los **bytes raw** del PDF
4. Match por **numero consecutivo** si falla la extraccion
5. Marcar como `sin_xml` si no se puede vincular

**Regla de multiples claves:** Algunos PDFs de notas de credito contienen 2 claves (la factura original y la NC). **Siempre usar la ultima clave encontrada.**

**Auditar:**
- Que la extraccion de clave valide exactamente 50 digitos
- Que en caso de multiples claves se use la ultima, no la primera
- Que PDFs sin vincular se marquen correctamente, no se descarten silenciosamente

### 5. Estados de facturas

| estado | Significado | Clasificacion | Move |
|--------|------------|---------------|------|
| `pendiente` | XML + PDF, sin clasificar | bloqueada | bloqueado |
| `pendiente_pdf` | XML sin PDF | opcional | bloqueado |
| `sin_xml` | PDF sin XML | permitida | permitido |
| `clasificado` | Ya clasificado | reclasificar permitido | permitido |

**Auditar:**
- Que las transiciones de estado sean consistentes
- Que no se permita mover archivos en estados bloqueados
- Que `clasificado` permita reclasificacion sin perder el registro anterior

---

## Checklist de auditoria por area

### A. Seguridad

- [ ] Inyeccion SQL en consultas a SQLite (buscar string formatting en queries, deberian usar parametros `?`)
- [ ] Path traversal: que inputs del usuario no puedan escapar de los directorios permitidos
- [ ] Credenciales hardcodeadas (API keys, passwords en codigo fuente)
- [ ] Permisos de archivos: que no se creen archivos con permisos excesivos
- [ ] Datos sensibles en logs o mensajes de error (cedulas, nombres de clientes)
- [ ] Validacion de respuestas de la API de Hacienda (no confiar ciegamente en datos externos)
- [ ] XML External Entity (XXE) attacks en el parsing de XMLs

### B. Concurrencia y threading

- [ ] Widgets de customtkinter accedidos fuera del main thread
- [ ] SQLite sin threading.Lock()
- [ ] Variables compartidas entre threads sin sincronizacion
- [ ] Locks no liberados en paths de excepcion (deberian usar `with lock:`)
- [ ] Operaciones de I/O bloqueantes en el main thread
- [ ] ThreadPoolExecutor sin manejo de excepciones en futures
- [ ] Posibles deadlocks por locks anidados en orden inconsistente

### C. Integridad de datos fiscales

- [ ] Movimiento de PDFs fuera del protocolo atomico SHA256
- [ ] `shutil.move()` o `os.rename()` directo sobre documentos fiscales
- [ ] Borrado de originales antes de verificar la copia
- [ ] Registros SQLite sin SHA256
- [ ] Excepciones silenciadas durante operaciones de filesystem
- [ ] Estado inconsistente si el proceso se interrumpe a mitad de un move
- [ ] Archivos temporales que podrian quedar huerfanos

### D. Manejo de errores

- [ ] `except Exception: pass` o `except: pass` (excepciones silenciadas)
- [ ] `try/except` demasiado amplio que atrapa errores inesperados
- [ ] Errores de I/O de red no manejados (Z:/ no disponible, timeout, permiso denegado)
- [ ] Falta de cleanup en paths de error (archivos parciales, locks no liberados)
- [ ] Mensajes de error que no dan informacion util para diagnosticar

### E. Arquitectura y capas

- [ ] Logica de negocio en archivos de `gui/` (debe estar en `core/` o `app/`)
- [ ] Imports de `customtkinter` en `core/` o `app/` (prohibido)
- [ ] Acceso directo a widgets para leer estado en vez de usar view models
- [ ] Duplicacion de logica entre `gui/` y `core/`
- [ ] Modulos de `app/` que bypasean `core/` y reimplementan logica

### F. Recursos y memoria

- [ ] Archivos abiertos sin cerrar (deberian usar `with open(...)`)
- [ ] Conexiones SQLite no cerradas
- [ ] Objetos pymupdf (fitz.Document) sin cerrar
- [ ] Caches que crecen sin limite (memory leak potencial)
- [ ] Imagenes de PDF cargadas en memoria sin liberar

### G. Edge cases

- [ ] Nombres de archivo/carpeta con caracteres especiales (tildes, enies, espacios)
- [ ] XMLs con encoding incorrecto (declarado UTF-8 pero con bytes Latin-1)
- [ ] PDFs corruptos o de 0 bytes
- [ ] Carpetas vacias o inexistentes
- [ ] Clientes sin XMLs o sin PDFs
- [ ] Multiples facturas con la misma clave (duplicados)
- [ ] Fechas en formatos inesperados
- [ ] Valores monetarios con formato europeo (1.234,56) vs americano (1,234.56)

---

## Formato de reporte

Para cada hallazgo, reportar:

```
### [SEVERIDAD] Titulo descriptivo del hallazgo

- **Archivo:** path/al/archivo.py:numero_de_linea
- **Severidad:** CRITICO | ALTO | MEDIO | BAJO
- **Categoria:** Seguridad | Concurrencia | Integridad Fiscal | Arquitectura | Error Handling | Recurso | Edge Case
- **Descripcion:** Que esta mal y por que es un problema
- **Impacto:** Que puede pasar en produccion si no se corrige
- **Fix sugerido:** Codigo o pseudocodigo del fix
```

Niveles de severidad:
- **CRITICO:** Perdida de datos fiscales, corrupcion de SQLite, vulnerabilidad de seguridad explotable
- **ALTO:** Crash de la aplicacion, deadlock, condicion de carrera con consecuencias, violacion del protocolo atomico
- **MEDIO:** Excepcion silenciada, recurso no cerrado, violacion de capas, edge case no manejado
- **BAJO:** Codigo fragil que podria fallar ante cambios futuros, falta de validacion en entrada no critica

---

## Resumen ejecutivo

Al final del reporte, incluir:

1. **Hallazgos por severidad:** Conteo de CRITICO / ALTO / MEDIO / BAJO
2. **Areas de mayor riesgo:** Los 3 archivos o modulos con mas hallazgos criticos
3. **Recomendaciones inmediatas:** Las 5 acciones mas urgentes, ordenadas por impacto
4. **Estado general:** Evaluacion de 1 a 10 de la salud del codebase, con justificacion

---

## Instrucciones de ejecucion

1. Lee TODOS los archivos `.py` dentro de `gestor_contable/` y sus subdirectorios
2. Analiza cada archivo contra el checklist completo
3. Cruza referencias entre modulos (un bug en core/ puede manifestarse en gui/)
4. Presta atencion especial a: `classifier.py`, `xml_manager.py`, `factura_index.py`, `main_window.py`
5. Genera el reporte en el formato especificado arriba
6. NO inventes hallazgos -- si algo esta bien implementado, no lo reportes como problema
7. Si no estas seguro de si algo es un bug, indicalo como "POSIBLE" con tu nivel de confianza
