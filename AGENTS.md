# AGENTS.md

Guía corta para Codex y otros asistentes que trabajen en este repositorio.

## Idioma

- Responder primero en español.

## Prioridad de contexto

1. Pedido explícito del usuario.
2. Código real del repositorio.
3. `AI_INDEX.md`.
4. Este archivo.
5. Documentación histórica adicional como `CLAUDE.md`, `MAP.md` o `README.md`.

Si una doc queda desfasada frente al código, prevalece el código.

## Qué es este repo

- Proyecto activo: `gestor_contable/`
- Tipo: aplicación de escritorio en Python + `customtkinter` para cargar XML/PDF, vincular por clave Hacienda, consultar Hacienda/ATV, clasificar comprobantes y exportar reportes.
- Entrada principal: `python gestor_contable/main.py`

## Alcance de trabajo

- El producto vive en `gestor_contable/`; ahí deben caer casi todos los cambios de lógica.
- Se permite editar archivos raíz de documentación o tooling cuando el pedido lo requiera explícitamente, por ejemplo:
  - `AGENTS.md`
  - `AI_INDEX.md`
  - `README.md`
  - `build.py`
  - `gestor_contable.spec`
- No crear módulos de producto fuera de `gestor_contable/`.

## Mapa rápido

- `gestor_contable/core/`: dominio, parsing XML, caches, clasificación, cuarentenas, purgas y reportes.
- `gestor_contable/app/`: controladores, use cases, estado y view models extraídos de GUI.
- `gestor_contable/gui/`: vistas `customtkinter`, visor PDF, setup inicial y panel de clasificación.
- `gestor_contable/tests/`: pruebas automatizadas actuales; hoy están centradas en observabilidad/logging.
- `gestor_contable/data/`: fallback local de cache y artefactos temporales de prueba.
- `_forensics/`: evidencia y repros forenses; no es código productivo.

## Invariantes no negociables

- No romper el protocolo fiscal de `gestor_contable/core/classifier.py`.
  - Para mover comprobantes usar `safe_move_file()` o `classify_record()`.
  - No reemplazar eso por `shutil.move()` ni por borrado directo.
- La clave fiscal válida tiene exactamente 50 dígitos.
- El tipo documental se determina leyendo el XML real y su `root.tag`, no por nombre de archivo.
- El trabajo con PDF usa `pymupdf` (`fitz`), no `pdfplumber`.
- Toda la UI corre en el main thread.
  - I/O, red y escaneos pesados salen a workers.
  - El retorno a UI se hace con `.after(...)`.
- Todo acceso SQLite concurrente debe quedar protegido con `threading.Lock()`.
- Usar `pathlib.Path`.
- Manejar siempre errores de `Z:`, `subst`, OneDrive y placeholders con mensajes claros.

## Operación real

- `gestor_contable/main.py` escribe logs en `~/.gestor_contable_logs/gestor_contable.log`.
- `gestor_contable/config.py` intenta montar `Z:` con `subst`.
  - Lee `~/.gestor_contable/local_settings.json` si existe.
  - Busca la clave `subst_source`.
  - Si no puede montar, abre `gestor_contable/gui/setup_window.py`.
- `gestor_contable/core/xml_manager.py` usa `Z:/DATA/hacienda_cache.db` si existe y cae a `gestor_contable/data/hacienda_cache.db` como fallback local.
- Los metadatos por cliente viven en `.metadata/` y hoy incluyen, según el flujo:
  - `clasificacion.sqlite`
  - `catalogo_cuentas.json`
  - `pdf_cache.json`
  - `xml_cache.db`
  - `ignored_xml_errors.json`
  - `duplicates_quarantine.sqlite`
  - `ors_purge.sqlite`
  - `receptor_purge.sqlite`

## Hotspots actuales

- `gestor_contable/gui/main_window.py` sigue siendo el hotspot principal y el archivo más riesgoso.
- Si una responsabilidad ya existe en `gestor_contable/app/`, extender esa capa es preferible a reinyectar lógica en GUI.
- Cambios en linking XML/PDF, cuarentenas, purgas o clasificación fiscal requieren validación cuidadosa.

## Validación mínima sugerida

- Cargar cliente y cambiar rango.
- Selección simple y múltiple.
- Clasificación individual y por lote.
- Exportación de reporte.
- Visor PDF.
- Si aplica, cuarentena/purga/restauración.

## Documentación relacionada

- `AI_INDEX.md`: mapa operativo actualizado.
- `CLAUDE.md`: reglas extendidas e historial de refactor.
- `README.md`: arranque y uso básico.
