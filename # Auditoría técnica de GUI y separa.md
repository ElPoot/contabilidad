# Auditoría técnica de GUI y separación de responsabilidades

## 1. Resumen ejecutivo
- **Diagnóstico general**  
  La base de negocio en Python ya tiene valor y, en varios puntos, está razonablemente separada en `core`, por ejemplo en [classification_utils.py](/C:/GITHUB/contabilidad/gestor_contable/core/classification_utils.py#L34), [classifier.py](/C:/GITHUB/contabilidad/gestor_contable/core/classifier.py#L342), [session.py](/C:/GITHUB/contabilidad/gestor_contable/core/session.py#L25) y [folder_sanitizer.py](/C:/GITHUB/contabilidad/gestor_contable/core/folder_sanitizer.py#L11). El problema principal no es el lenguaje ni el stack: es que la GUI, sobre todo [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L749), está actuando como vista, controlador, orquestador, coordinador de threads y contenedor de casos de uso al mismo tiempo.
- **Nivel de acoplamiento actual**  
  Alto. La interacción entre tabla, selección, panel derecho, visor PDF, filtros, exportación, carga de sesión y acciones operativas depende de estado implícito dentro de widgets y atributos de ventana, no de una capa explícita de estado y coordinación.
- **Nivel de riesgo técnico**  
  Medio-alto. No se ve una crisis arquitectónica irreversible, pero sí un patrón que ya está produciendo bugs de sincronización, decisiones duplicadas y fixes localizados que vuelven a aparecer en otra pantalla o flujo.
- **Conclusión principal en lenguaje claro**  
  No hace falta reescribir ni migrar la GUI hoy. Hace falta dejar de meter inteligencia operativa en las ventanas y empezar a extraer, por fases, una capa de estado y una capa de casos de uso/controladores. La app ya tiene suficiente base para hacerlo de forma incremental y segura.
- **Alcance y salvedad**  
  Esta auditoría es estática, basada en el código visible. No se ejecutó una validación E2E completa de todos los flujos.

## 2. Hallazgos principales

### Hallazgo 1: `main_window.py` es un God Object de presentación y aplicación
**Qué está mal**  
[main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L749) concentra construcción de la ventana, estado de pantalla, carga de sesión, filtros, selección, clasificación, exportes, saneamiento, recuperación, vinculación, limpieza de duplicados, diálogo de fechas y coordinación de fondo. La evidencia es muy clara: carga de sesión en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L843), exportación en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2358), sanitización en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3231), vinculación de omitidos en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3479), clasificación en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3770) y limpieza de duplicados en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L4305).

**Por qué es un problema**  
La ventana principal dejó de ser una vista y se convirtió en el “cerebro” del sistema. Esto destruye la separabilidad entre presentación, coordinación y dominio.

**Riesgo actual**  
Cada cambio local tiene radio de impacto alto. Un fix en clasificación, selección, exportación o carga puede romper zonas no relacionadas.

**Impacto en producción**  
Los bugs recientes encajan con este patrón: condiciones de carrera, flujos de UI inconsistentes, masks duplicadas en reportes y necesidad de corregir manualmente comportamientos dispersos.

**Impacto en mantenibilidad y evolución**  
Una futura migración de GUI no podría reutilizar solo “pantallas”; tendría que portar también gran parte de la lógica operacional incrustada en la ventana.

**Cómo corregirlo sin romper nada**  
Extraer primero los casos de uso más pesados a módulos nuevos, manteniendo las firmas actuales en la ventana como delegadores. El orden conservador sería: `export_report`, `load_session/load_range`, `classify_selected`, `sanitize/recover/link/cleanup`. No mover todo de golpe; mover un flujo vertical por vez.

**Prioridad**  
crítica

**Esfuerzo**  
alto

**Riesgo del cambio**  
medio

### Hallazgo 2: El estado de pantalla es implícito y está repartido entre atributos, widgets y caches
**Qué está mal**  
El estado operativo vive dentro de la ventana en una mezcla de atributos y widgets: `records`, `all_records`, `_db_records`, `selected`, `selected_records`, `_records_map`, `_loaded_months`, `_active_tab`, `_prev_dest_path`, `from_var`, `to_var` y otros en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L762). Además, métodos como [_apply_filters()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L4016), [_sync_category_for_record()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2017) y [_on_classify_ok()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3878) mezclan lectura de widgets, caches y selección visual.

**Por qué es un problema**  
No existe una fuente de verdad clara del estado de pantalla. El sistema “recuerda” cosas en demasiados lugares y la consistencia depende del orden de llamadas.

**Riesgo actual**  
Estados obsoletos, selección incoherente, panel derecho desalineado con árbol o visor, filtros que dependen de variables Tk y no de un estado serializable.

**Impacto en producción**  
Esto ya se traduce en bugs de UI difíciles de rastrear, sobre todo cuando una acción resetea parcialmente selección, árbol, visor o formulario.

**Impacto en mantenibilidad y evolución**  
Sin un `AppState` o equivalente, no hay forma limpia de testear transiciones ni de cambiar de toolkit sin reimplementar lógica de estado embebida en widgets.

**Cómo corregirlo sin romper nada**  
Introducir un `MainWindowState` mínimo y mutable al inicio, sin cambiar la UI todavía. Empezar por meter ahí: sesión activa, tab activa, rango de fechas, selección, registros visibles, estado de carga y errores. Luego hacer que la vista renderice desde ese estado en lugar de calcular desde widgets.

**Prioridad**  
alta

**Esfuerzo**  
medio

**Riesgo del cambio**  
medio

### Hallazgo 3: La carga de sesión y la estrategia de background tasks están orquestadas desde la vista de forma inconsistente
**Qué está mal**  
La carga de cliente en [_load_session()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L843) y la carga incremental por rango en [_start_range_load()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2247) hacen trabajo de coordinación de aplicación dentro de la ventana. Además, la app usa varios patrones distintos de background: `Queue + poll` en unas partes, `threading.Thread + after(0, ...)` en otras, y hasta `self.update()`/`self.update_idletasks()` para forzar visualización en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L878) y [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2258). Solo en [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py) aparecen 16 lanzamientos directos de `threading.Thread`; en [session_view.py](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py) aparecen 6 más.

**Por qué es un problema**  
La política de concurrencia no está centralizada. La ventana decide cómo correr tareas, cómo mostrar progreso, cómo invalidar generaciones y cómo reconciliar resultados.

**Riesgo actual**  
Condiciones de carrera, overlays que dependen de hacks visuales, callbacks tardíos y repetición de mecanismos de cancelación/invalidación.

**Impacto en producción**  
Se vuelve más probable repetir bugs como respuestas obsoletas, bloqueos por confirmaciones o desincronización entre worker y UI.

**Impacto en mantenibilidad y evolución**  
Una nueva GUI tendría que heredar también esta estrategia artesanal de threading, en vez de apoyarse en una capa intermedia reutilizable.

**Cómo corregirlo sin romper nada**  
Crear una abstracción única de tareas en fondo, por ejemplo `TaskRunner` o `BackgroundJobRunner`, que reciba una función, publique `running/success/error` y ejecute la reconciliación UI siempre de la misma forma. No hace falta meter asyncio ni rediseños complejos; basta con unificar el patrón actual detrás de una pequeña API.

**Prioridad**  
alta

**Esfuerzo**  
medio

**Riesgo del cambio**  
medio

### Hallazgo 4: Selección, panel derecho, árbol y visor PDF están acoplados por refrescos manuales y coreografía de widgets
**Qué está mal**  
[_on_select_single()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L1771) y [_on_multi_select()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L1909) actualizan a mano demasiadas piezas: viewer, pill de Hacienda, proveedor, botones, panel de clasificación anterior, ruta destino, formulario de categoría y mensajes contextuales. A eso se suma que múltiples flujos también limpian o refrescan árbol/visor/selección manualmente, por ejemplo en [_load_range_if_needed()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2215), [_poll_range_load()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2294) y [_on_classify_ok()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3878).

**Por qué es un problema**  
La selección no se modela como estado; se modela como una cadena de side effects sobre widgets.

**Riesgo actual**  
Cualquier variante de selección simple, múltiple, omitido, huérfano, pendiente PDF o clasificado puede dejar botones, visor o panel en un estado engañoso.

**Impacto en producción**  
Este patrón ya generó bugs de UI reales y genera mucha fragilidad en cambios aparentemente pequeños.

**Impacto en mantenibilidad y evolución**  
Una futura migración de GUI tendría que reproducir exactamente esta coreografía manual, en lugar de consumir un `SelectionViewModel` claro.

**Cómo corregirlo sin romper nada**  
Crear un `SelectionController` que reciba `record` o `records` y devuelva un modelo de render: documento a cargar, mensaje del visor, acciones habilitadas, datos del panel derecho, clasificación previa, estado Hacienda y ruta anterior. Luego dividir el render en métodos pequeños: `render_pdf_area`, `render_action_buttons`, `render_previous_classification`, `render_classify_form`.

**Prioridad**  
alta

**Esfuerzo**  
medio

**Riesgo del cambio**  
medio

### Hallazgo 5: Los workflows de negocio y reportes pesados viven dentro de la GUI
**Qué está mal**  
La ventana principal contiene casos de uso completos y lógica de dominio operativo. El ejemplo más fuerte es [_export_report()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2358), que hace extracción de datos, transformación, clasificación para hojas, conversión monetaria, layout Excel y escritura con `pandas/openpyxl`. Lo mismo ocurre con [_sanitize_folders()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3231), [_recover_selected()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3434), [_link_omitted_to_xml()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3479), [_classify_selected()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L3770) y [_find_duplicate_pdfs()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L4305).

**Por qué es un problema**  
Estos flujos no deberían depender de widgets para existir. La GUI debería dispararlos, no implementarlos.

**Riesgo actual**  
Duplicación de reglas y divergencias entre pantallas. El bug reciente de tiquetes en la hoja “Sin Receptor” es precisamente un síntoma de esto: el Excel reconstruía una clasificación propia en vez de consumir la regla central.

**Impacto en producción**  
Cada ajuste en negocio obliga a perseguir implementaciones escondidas en UI, reportes y acciones laterales.

**Impacto en mantenibilidad y evolución**  
Mientras estos workflows vivan en GUI, cambiar la vista implica reescribir también procesos operativos completos.

**Cómo corregirlo sin romper nada**  
Extraer por casos de uso, no por capas abstractas genéricas. Prioridad recomendada: `ExportPeriodReportUseCase`, `ClassifySelectionUseCase`, `LinkOmittedPdfUseCase`, `FolderSanitizationUseCase`, `DuplicateCleanupUseCase`. La ventana sigue mostrando diálogos y mensajes, pero delega toda la ejecución y recibe resultados tipados.

**Prioridad**  
alta

**Esfuerzo**  
medio

**Riesgo del cambio**  
medio

### Hallazgo 6: `session_view.py` mezcla presentación de login con infraestructura y provisioning
**Qué está mal**  
El módulo [session_view.py](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L96) no solo renderiza la pantalla de acceso. También lee carpetas y SQLite para accesos rápidos en [_load_saved_clients()](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L96), escribe `client_profiles.json` en [_save_cedula()](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L148), crea carpetas en [_create_client_folder()](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L594) y resuelve sesiones/creación desde la propia vista en [session_view.py](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L915) y [session_view.py](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L988).

**Por qué es un problema**  
La pantalla de login está acoplada a filesystem, perfiles, provisioning y resolución de sesión.

**Riesgo actual**  
Cambios en perfiles o creación de clientes obligan a editar código visual, y viceversa.

**Impacto en producción**  
Menor que en `main_window.py`, pero relevante: la entrada al sistema concentra demasiada responsabilidad para una vista que debería ser simple y confiable.

**Impacto en mantenibilidad y evolución**  
La futura migración de GUI tendría que portar también lógica de acceso rápido, creación de cliente y persistencia de cédulas.

**Cómo corregirlo sin romper nada**  
Mover esas funciones a un servicio de acceso de clientes, por ejemplo `client_access_service.py` o `session_service.py`, dejando `SessionView` solo con input, preview, debounce y render. La interfaz pública no necesita cambiar.

**Prioridad**  
media

**Esfuerzo**  
bajo

**Riesgo del cambio**  
bajo

## 3. Mapa de responsabilidades recomendado
- **GUI / vistas**  
  `App3Window`, `SessionView`, `PDFViewer`, `ModalOverlay` deben recibir eventos, llamar a un coordinador y renderizar. [pdf_viewer.py](/C:/GITHUB/contabilidad/gestor_contable/gui/pdf_viewer.py#L203) ya va en buena dirección: expone `load`, `clear`, `show_message` y `release_file_handles` sin llevar reglas de negocio.
- **Estado de pantalla / app state**  
  Introducir `MainWindowState` y `SessionScreenState` con sesión activa, tab, rango, selección, registros visibles, carga, errores, clasificación previa y progreso. El widget no debe ser la fuente de verdad.
- **Controladores o coordinadores**  
  `MainWindowController`, `SelectionController` y `SessionController` deben traducir eventos de UI a acciones de aplicación y devolver estado o view models.
- **Casos de uso**  
  `LoadClientPeriod`, `LoadAdditionalMonths`, `ClassifySelection`, `ExportPeriodReport`, `SanitizeFolders`, `RecoverOrphanedPdf`, `LinkOmittedPdf`, `FindDuplicateFiles`.
- **Servicios**  
  Servicios enfocados y pragmáticos: `ReportExportService`, `ClientAccessService`, `FolderMaintenanceService`, `DuplicateScanService`. No microservicios; solo módulos reutilizables de aplicación.
- **Workers / background tasks**  
  Un `TaskRunner` central con un contrato uniforme: ejecutar, reportar progreso, publicar éxito/error y reconciliar en UI. La vista no debería decidir cada vez cómo correr un thread.
- **Adaptadores de infraestructura**  
  Wrappers pequeños para filesystem, diálogos, apertura de carpetas, SQLite/DB y quizá un `PdfViewerPort` si más adelante se cambia el visor.
- **Modelos para render de UI / view models**  
  `InvoiceRowVM`, `SelectionVM`, `ClassificationPanelVM`, `QuickClientVM`, `LoadProgressVM`. La vista no debería deducir por sí misma qué botones mostrar o qué mensaje pintar.

## 4. Plan de mejora incremental

### Fase 1: estabilización segura
**Objetivo**  
Sacar de las vistas los hotspots más grandes sin cambiar comportamiento visible.

**Cambios concretos**  
- Extraer `export_report` a un servicio de reporte manteniendo [_export_report()](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2358) como delegador.
- Extraer `sanitize/recover/link/cleanup` a casos de uso.
- Crear helpers de render para selección y panel derecho sin introducir todavía un estado formal completo.
- Introducir un `TaskRunner` pequeño y reutilizarlo primero en 2 o 3 flujos de alto valor.

**Orden recomendado**  
1. `ExportPeriodReportUseCase`  
2. `FolderSanitizationUseCase` y `LinkOmittedPdfUseCase`  
3. `SelectionRenderer` o `SelectionController` inicial  
4. `TaskRunner`

**Riesgos**  
Suposiciones ocultas en el orden actual de side effects.

**Validaciones necesarias antes de pasar a la siguiente fase**  
- Cargar cliente y cambiar rango  
- Selección simple y múltiple  
- Clasificar individual y lote  
- Exportar Excel/CSV  
- Sanitizar, recuperar huérfano, vincular omitido  
- Verificar que el árbol, visor y panel derecho sigan sincronizados

### Fase 2: desacoplamiento estructural
**Objetivo**  
Separar responsabilidades y centralizar estado sin reescribir la app.

**Cambios concretos**  
- Introducir `MainWindowState`
- Crear `MainWindowController` para carga, filtros, clasificación y selección
- Reemplazar refrescos dispersos por funciones de render explícitas
- Hacer que `SessionView` consuma un `ClientAccessService` en vez de filesystem/SQLite directos

**Orden recomendado**  
1. Estado de selección y tab  
2. Estado de carga y filtros  
3. Controlador de sesión  
4. Controlador de clasificación

**Riesgos**  
Duplicar temporalmente estado viejo y nuevo mientras conviven.

**Validaciones necesarias antes de pasar a la siguiente fase**  
- Que la vista siga leyendo una única fuente de verdad por flujo  
- Que desaparezcan fixes “de sincronización manual” entre widgets  
- Que los casos de uso se puedan probar sin Tk

### Fase 3: preparación para evolución futura de GUI
**Objetivo**  
Dejar la app lista para que una futura migración visual sea barata y local.

**Cambios concretos**  
- Encapsular dependencias directas de `customtkinter` en la capa de vista
- Mantener controladores y casos de uso libres de widgets
- Exponer view models estables para tabla, panel derecho, sesión y progreso
- Definir puertos simples para visor PDF, diálogos y apertura de carpetas

**Orden recomendado**  
1. Blindar controladores y casos de uso  
2. Reducir dependencias directas entre vistas  
3. Formalizar contratos de render  
4. Revisar qué partes del toolkit siguen filtrándose a capas superiores

**Riesgos**  
Sobreingeniería si se intenta abstraer demasiado temprano.

**Validaciones necesarias antes de pasar a la siguiente fase**  
- Poder cambiar una vista sin tocar casos de uso  
- Poder testear clasificación, exportación y sesión sin levantar widgets  
- Poder reemplazar el toolkit de una pantalla piloto sin tocar el core

## 5. Refactors prioritarios sugeridos
1. sacar la lógica de exportación de [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2358) a `ReportExportService`
2. separar la carga de sesión y carga por rango de [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L843) y [main_window.py](/C:/GITHUB/contabilidad/gestor_contable/gui/main_window.py#L2247) en un `LoadPeriodController`
3. introducir `MainWindowState` para tab activa, rango, selección, carga y registros visibles
4. separar selección + panel derecho + visor en un `SelectionController` con `SelectionVM`
5. unificar la estrategia de workers en una sola abstracción y dejar de lanzar threads directos desde la vista
6. mover `sanitize/recover/link/cleanup` a casos de uso de aplicación
7. sacar de [session_view.py](/C:/GITHUB/contabilidad/gestor_contable/gui/session_view.py#L96) la carga de clientes, persistencia de cédula y creación de cliente
8. reducir `main_window.py` por responsabilidades, no por capricho, hasta que la ventana quede enfocada en render y eventos

## 6. Reglas de implementación para no romper producción
- Crear módulos nuevos y hacer que los métodos actuales deleguen; no reemplazar flujos enteros de una vez.
- No mover reglas de negocio maduras desde `core`; reutilizarlas desde la nueva capa de aplicación.
- No agregar nuevas reglas de negocio en archivos GUI.
- No introducir otro patrón de threading distinto; converger gradualmente a uno solo.
- Validar cada refactor con una matriz fija de escenarios manuales críticos antes de fusionar.
- Trabajar módulo por módulo y flujo por flujo, no “por capas completas”.
- Mantener contratos existentes de callbacks, nombres de botones y mensajes salvo necesidad explícita.
- Cuando una vista necesite datos para pintar, preferir un view model antes que leer widgets o caches dispersos.
- Cada PR debería mover una responsabilidad concreta y dejar menos inteligencia en la vista que antes.
- Si un refactor obliga a tocar negocio y GUI a la vez, primero extraer la lógica a un módulo reusable y luego adaptar la vista.

## 7. Señales de que la arquitectura ya va mejorando
- La vista ya no decide reglas de clasificación ni máscaras de reporte por su cuenta.
- `main_window.py` deja de crecer y empieza a bajar de tamaño por extracción real de responsabilidades.
- Los widgets ya no se actualizan desde cualquier parte, sino desde funciones de render identificables.
- La selección se representa como estado, no como side effects sobre árbol, visor y botones.
- Los workers siguen una sola estrategia y la vista no conoce detalles de threading.
- `SessionView` deja de crear carpetas o escribir perfiles directamente.
- Los casos de uso principales pueden probarse sin instanciar `customtkinter`.
- Los bugs de sincronización entre pestaña, exportación, selección y panel derecho dejan de corregirse en más de un lugar.

## 8. Conclusión final
La recomendación concreta y pragmática es **no migrar la GUI ahora** y **no reescribir la app**. La jugada correcta es extraer una pequeña capa de aplicación entre la vista y el core, empezando por exportación, carga de período, selección y clasificación. La app ya tiene base suficiente en `core` y piezas visuales relativamente sanas como [pdf_viewer.py](/C:/GITHUB/contabilidad/gestor_contable/gui/pdf_viewer.py#L203) y [modal_overlay.py](/C:/GITHUB/contabilidad/gestor_contable/gui/modal_overlay.py#L169) para evolucionar de forma segura. Si el equipo ejecuta 3 o 4 refactors verticales bien escogidos, la GUI actual va a ser más mantenible de inmediato y una futura migración visual dejará de ser traumática.

## 9. Estado del refactoring

- [x] R1: Extraer lógica de exportación de `main_window.py` → `app/use_cases/export_report_use_case.py`
- [ ] R2: Separar carga de sesión y rango de `main_window.py` → `LoadPeriodController`
- [x] R3: Introducir `MainWindowState` (tab activa, rango, selección, registros, carga, errores) — slice 1: dataclass + propiedades delegadas (sin cambio de comportamiento)
- [x] R4: Separar selección + panel derecho + visor → `SelectionController` + `SelectionVM`
- [ ] R5: Unificar estrategia de workers en `TaskRunner` (eliminar threads directos desde la vista)
- [ ] R6: Mover `sanitize/recover/link/cleanup` → casos de uso de aplicación
- [ ] R7: Sacar lógica de `session_view.py` (clientes, cédulas, creación) → `ClientAccessService`
- [ ] R8: Reducir `main_window.py` por responsabilidades hasta que quede enfocado en render y eventos