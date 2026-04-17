# AI_INDEX.md

Mapa operativo actualizado del repositorio `contabilidad`, orientado a agentes de IA y asistentes de desarrollo.

## 1. Resumen ejecutivo

- Proyecto productivo: `gestor_contable/`
- Dominio: clasificación, vinculación, saneamiento, cuarentena y exportación de comprobantes electrónicos para una firma contable en Costa Rica.
- Plataforma: aplicación de escritorio en Python con `customtkinter`.
- Datos fuente: XML y PDF ubicados en una unidad lógica de red, típicamente `Z:/DATA`.
- Persistencia operativa: metadatos por cliente en `.metadata/`, con mezcla de JSON y SQLite.
- Estado actual: producción activa con refactor incremental para sacar responsabilidades de `gestor_contable/gui/main_window.py` hacia `gestor_contable/app/`.

## 2. Qué hay en la raíz del repo

- `gestor_contable/`
  - Código productivo, pruebas y datos locales de soporte.
- `_forensics/`
  - Evidencia, repros y reportes forenses. Útil para auditoría; no es código de negocio.
- `AGENTS.md`
  - Entrada corta y operativa para asistentes.
- `AI_INDEX.md`
  - Este mapa.
- `CLAUDE.md`
  - Reglas extensas e historial de arquitectura/refactor.
- `README.md`
  - Arranque, instalación y build.
- `build.py`
  - Script interactivo de release con PyInstaller.
- `gestor_contable.spec`
  - Spec de PyInstaller.
- `MAP.md`, `GEMINI.md`, `reporte_auditoria.md`
  - Documentación adicional e histórica.

## 3. Puntos de entrada reales

### 3.1 `gestor_contable/main.py`

- Configura logging global en `~/.gestor_contable_logs/gestor_contable.log`.
- Asegura la raíz del repo en `sys.path`.
- Llama `ensure_drive_mounted()` antes de abrir la ventana principal.
- Si `Z:` no puede montarse automáticamente, abre `gestor_contable/gui/setup_window.py`.
- Inicia `App3Window`.

### 3.2 `gestor_contable/config.py`

- Resuelve `network_drive()`, `client_root()` y `metadata_dir()`.
- Detecta OneDrive local con tres fuentes:
  - `~/.gestor_contable/local_settings.json`
  - variables de entorno `OneDrive*`
  - carpetas `OneDrive*` bajo el home
- Monta la letra configurada con `subst`.
- Detecta placeholders de OneDrive con atributos Win32.

### 3.3 `build.py`

- Hace bump sobre `gestor_contable/version.py`.
- Actualiza `CHANGELOG.md`.
- Puede commitear y tagear.
- Compila con PyInstaller usando `gestor_contable.spec`.
- Genera ZIP en `releases/`.
- Es interactivo: pide notas de release y confirmación.

## 4. Mapa del código

### 4.1 `gestor_contable/core/`

Núcleo de negocio e infraestructura. No debe depender de GUI.

- `models.py`
  - Define `FacturaRecord` y contratos base.
- `xml_manager.py`
  - Parsing XML en streaming, normalización, asociación de mensajes Hacienda y acceso a cache de Hacienda.
  - Usa `Z:/DATA/hacienda_cache.db` si existe y cae a `gestor_contable/data/hacienda_cache.db`.
  - Recupera algunos XML mal codificados reintentando con Latin-1 -> UTF-8.
- `xml_cache.py`
  - Cache SQLite de parseo XML invalidado por `mtime` + tamaño.
- `factura_index.py`
  - Carga períodos.
  - Vincula XML y PDF.
  - Detecta mensajes ocultables, respuestas purgadas, omitidos y huérfanos.
  - Lee y respeta `ignored_xml_errors.json`.
- `classifier.py`
  - `ClassificationDB`
  - `safe_move_file()`
  - `classify_record()`
  - construcción de rutas contables
  - reparación de rutas clasificadas (`heal_classified_path`)
  - zona fiscal crítica
- `classification_utils.py`
  - Filtros, estadísticas, duplicados, clasificación transaccional y hallazgo de huérfanos.
  - Reutiliza `pdf_cache.json` y `xml_cache.db` cuando puede.
- `session.py`
  - Resolución de sesión por cliente/cédula.
- `settings.py`
  - Configuración central (`get_setting()`).
- `client_profiles.py`
  - Mapeo de perfiles de cliente y apoyo con cache Hacienda.
- `catalog.py`
  - Catálogo contable por cliente en `catalogo_cuentas.json`.
- `iva_utils.py`
  - Parsing decimal robusto y utilidades de IVA.
- `pdf_cache.py`
  - Cache JSON de extracción PDF.
- `pdf_generator.py`
  - Generación de PDF a partir de XML cuando falta el PDF original.
- `report_paths.py`
  - Nombres de mes y resolución incremental de nombres de reporte.
- `corte_engine.py`
  - Lógica base de cortes/reportes.
- `corte_excel.py`
  - Escritura y formato Excel.
- `duplicates_quarantine.py`
  - Cuarentena auditada de duplicados en `.metadata/duplicates_quarantine/`.
  - Registra en `duplicates_quarantine.sqlite`.
- `ors_purge.py`
  - Purga/cuarentena ORS con `manifest.json` y `ors_purge.sqlite`.
- `receptor_purge.py`
  - Purga/cuarentena de respuestas receptor con `receptor_purge.sqlite`.
- `folder_sanitizer.py`
  - Saneamiento de estructuras/carpetas vinculadas al flujo contable.
- `cabys_manager.py`
  - Gestión CABYS.
- `atv_client.py`
  - Consultas a ATV/Hacienda para verificación de estado.
- `ciiu_affinity.json`
  - Datos auxiliares de afinidad CIIU.

### 4.2 `gestor_contable/app/`

Capa de aplicación usada para sacar lógica de `gui/main_window.py`.

- `use_cases/export_report_use_case.py`
  - Exportación de reportes a Excel/CSV.
- `controllers/load_period_controller.py`
  - Orquesta carga de sesión/rango.
  - Calcula meses faltantes y filtra `sin_xml` fuera del rango.
- `controllers/pdf_swap_controller.py`
  - Intercambio de PDF ganador/descartado cuando hay duplicados.
- `selection_controller.py`
  - Construye `SelectionVM` para selección simple y múltiple.
- `selection_vm.py`
  - View model puro del panel derecho.
- `state/main_window_state.py`
  - Estado mutable de la ventana principal sin widgets.
- `services/forensic_overwrite_audit.py`
  - Soporte de auditoría forense para sobreescrituras.

### 4.3 `gestor_contable/gui/`

Capa visual en `customtkinter`. Debe renderizar y delegar.

- `main_window.py`
  - Ventana principal.
  - Sigue siendo el hotspot principal.
  - Integra árbol, visor PDF, panel de clasificación, exportaciones, cuarentenas y purgas.
- `classify_panel.py`
  - Panel derecho que consume `SelectionVM`.
- `pdf_viewer.py`
  - Renderizado PDF con `fitz`.
- `session_view.py`
  - Login/selección de cliente.
- `setup_window.py`
  - Configuración inicial cuando no se puede montar `Z:`.
- `loading_modal.py`
  - Overlay de carga.
- `modal_overlay.py`
  - Sistema modal reusable.
- `orphaned_pdfs_modal.py`
  - Gestión visual de PDFs huérfanos.
- `corte_ambiguo_modal.py`
  - Resolución de ambigüedades de cortes.
- `icons.py`, `fonts.py`, `icons/`
  - Recursos visuales.

### 4.4 `gestor_contable/tests/`

- `test_observabilidad_logging.py`
  - Cobertura automatizada actual.
  - Valida logging y estados degradados en:
    - catálogos corruptos
    - fallos de cache XML/PDF
    - `setup_window`
    - `config.py`
    - fechas inválidas
    - `FacturaIndexer` con `ignored_xml_errors.json` inválido

### 4.5 `gestor_contable/data/`

- `hacienda_cache.db`
  - Fallback local de cache.
- `_tmp_test_observabilidad/`
  - Artefactos temporales de pruebas.
- Otros subdirectorios temporales
  - Sirven como fixture o salida de tests/repros.

## 5. Flujo principal del sistema

1. `main.py` inicia logging y prepara `sys.path`.
2. `config.ensure_drive_mounted()` intenta exponer la ruta local como unidad lógica (`Z:` por defecto).
3. Si no puede montarla, `setup_window.py` permite guardar `subst_source` en `~/.gestor_contable/local_settings.json`.
4. `App3Window` abre sesión de cliente desde `session_view.py`.
5. `load_period_controller.load_session_worker()` carga:
   - catálogo del cliente
   - `clasificacion.sqlite`
   - XML/PDF del rango
   - filtros de `sin_xml`
   - detección de huérfanos/renames
6. `main_window.py` actualiza `MainWindowState`, árbol, visor y panel derecho.
7. `selection_controller.py` construye `SelectionVM`.
8. `classify_panel.py` renderiza el estado y habilita acciones.
9. La clasificación usa `ClassificationDB` + `classify_record()`.
10. Exportaciones, cuarentenas y purgas usan módulos dedicados en `app/` y `core/`.

## 6. Invariantes y reglas importantes

### 6.1 Integridad fiscal

- No usar `shutil.move()` ni atajos para mover comprobantes fiscales.
- El patrón válido es:
  - hash SHA256 del original
  - copia preservando metadata
  - hash de la copia
  - si coincide, eliminar original
  - si no coincide, eliminar copia y abortar
- `safe_move_file()` y `classify_record()` son zonas protegidas.

### 6.2 Clave Hacienda y linking XML/PDF

- La clave fiscal válida tiene exactamente 50 dígitos.
- El linking PDF/XML es delicado y tiene varias fases reales en el código:
  - extracción exacta desde filename
  - pre-link por tokens de filename contra índice de consecutivos
  - extracción desde texto PDF con `fitz`
  - escaneo de raw bytes como último recurso rápido
  - reconciliación final por consecutivo si la coincidencia es única
- Si un PDF queda sin match, el registro puede caer en `sin_xml`.
- Razones de omisión activas:
  - `non_invoice`
  - `timeout`
  - `extract_failed`
- Regla documental vigente: si un PDF trae múltiples claves, usar la clave actual del documento, no una referencia histórica.

### 6.3 Clasificación de XML

- No inferir tipo documental por filename ni por sufijos como `_respuesta` o `_NC`.
- Leer el XML real y basarse en `root.tag`.
- Si un XML falla, inspeccionar el XML concreto antes de generalizar.
- Existe lista de ignorados manuales en `ignored_xml_errors.json`.

### 6.4 Concurrencia

- La UI corre en el main thread.
- I/O pesado, parsing, red y movimientos de disco deben salir a workers.
- El retorno a UI se hace con `.after(...)`.
- No introducir patrones de threading ad hoc si puede reutilizarse el patrón existente.

### 6.5 SQLite

- Toda lectura/escritura concurrente debe quedar protegida con `threading.Lock()`.
- Esto aplica a:
  - clasificación
  - xml cache
  - caches auxiliares
  - cuarentenas
  - purgas

### 6.6 OneDrive y `Z:`

- `Z:/DATA` es la ruta por defecto, pero puede no existir al arranque.
- La letra puede variar vía `get_setting("subst_drive_letter")`.
- El origen local para `subst` puede venir de `~/.gestor_contable/local_settings.json`.
- Archivos placeholder de OneDrive existen y deben tratarse como tal; intentar leerlos puede causar timeouts o errores de acceso.

## 7. Estado actual del refactor

Refactor vigente: extraer responsabilidades de `gui/main_window.py` sin reescribir la aplicación.

Piezas ya extraídas:

- Exportación de reportes -> `app/use_cases/export_report_use_case.py`
- Carga de período/sesión -> `app/controllers/load_period_controller.py`
- Selección y render decision -> `app/selection_controller.py` + `app/selection_vm.py`
- Estado de ventana -> `app/state/main_window_state.py`
- Intercambio de PDF duplicado -> `app/controllers/pdf_swap_controller.py`
- Panel derecho desacoplado -> `gui/classify_panel.py`

Riesgo principal:

- `gui/main_window.py` sigue concentrando demasiada orquestación.
- Cambios allí deben ser quirúrgicos.
- Si una responsabilidad ya existe en `app/`, expandir esa capa es preferible a devolver lógica a GUI.

## 8. Rutas y artefactos operativos

### 8.1 Layout de red

```text
Z:/DATA/
  PF-{year}/
    CLIENTES/
      CLIENTE/
        XML/
        PDF/
        .metadata/
    Contabilidades/
      {mes}/
        {cliente}/
```

### 8.2 `.metadata/` por cliente

Puede contener, según el flujo:

- `clasificacion.sqlite`
- `catalogo_cuentas.json`
- `pdf_cache.json`
- `xml_cache.db`
- `ignored_xml_errors.json`
- `duplicates_quarantine.sqlite`
- `ors_purge.sqlite`
- `receptor_purge.sqlite`
- carpetas de cuarentena con `manifest.json`

### 8.3 Logs

- `~/.gestor_contable_logs/gestor_contable.log`

### 8.4 Cache Hacienda

- Prioridad real:
  - variable de entorno `HACIENDA_CACHE_DB`
  - `Z:/DATA/hacienda_cache.db`
  - fallback local `gestor_contable/data/hacienda_cache.db`

## 9. Comandos útiles

- Instalar dependencias:
  - `pip install -r requirements.txt`

- Ejecutar la app:
  - `python gestor_contable/main.py`

- Ejecutar pruebas automatizadas actuales:
  - `python -m unittest gestor_contable.tests.test_observabilidad_logging`

- Generar release:
  - `python build.py`
  - `python build.py minor`
  - `python build.py major`
  - `python build.py 1.2.3`
  - `python build.py --no-tag --no-commit --no-zip`

## 10. Guía práctica para agentes

- Leer primero este archivo; usar `CLAUDE.md` como contexto extendido, no como sustituto del código.
- Buscar con `rg` antes de abrir archivos grandes.
- Si vas a tocar `main_window.py`, revisar antes si esa responsabilidad ya vive en `app/`.
- No crear scripts temporales en la raíz salvo pedido explícito.
- No romper contratos visuales existentes sin orden expresa:
  - textos de botones
  - mensajes al usuario
  - layout principal
  - firmas de callbacks
- No tocar `classifier.py` en su protocolo fiscal sin un pedido explícito y una validación fuerte.

## 11. Validación manual mínima

- Cargar cliente.
- Cambiar rango.
- Selección simple.
- Selección múltiple.
- Clasificación individual.
- Clasificación por lote.
- Exportación de reporte.
- Visor PDF.
- Si aplica, cuarentena/purga/restauración.

## 12. Documentación relacionada

- `AGENTS.md`
  - Entrada corta para asistentes.
- `CLAUDE.md`
  - Reglas extendidas, restricciones y contexto histórico.
- `README.md`
  - Arranque y build.
- `MAP.md`
  - Contexto adicional del repo.
- `reporte_auditoria.md`
  - Hallazgos técnicos previos.
