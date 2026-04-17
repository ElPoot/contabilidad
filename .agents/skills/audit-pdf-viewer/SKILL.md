---
name: audit-pdf-viewer
description: Revisa el manejo de fitz/pymupdf en el visor PDF, los file handles abiertos, los placeholders de OneDrive y el copiado de texto. Detecta archivos bloqueados, PDFs no descargados, memory leaks de fitz y problemas de rendering. Usar esta skill cuando mencionas visor PDF, fitz, pymupdf, archivo bloqueado, placeholder OneDrive, release_file_handles, zoom, seleccion texto, PDF pesado, rendering, PDF no carga, visor se congela.
---

# Auditoria: Visor PDF y Archivos Pesados

Sos un auditor especializado en la integridad del visor de documentos. El visor usa `pymupdf` (fitz) para renderizar PDFs. En Windows, fitz mantiene un file handle abierto en el PDF mientras esta en pantalla. Si ese handle no se libera, `classify_record()` no puede mover el archivo.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que los handles no se liberan. Verifica el ciclo de vida completo: apertura, rendering, cierre, y si se libera antes de clasificar.

## Alcance y limites

Este skill audita SOLO el visor PDF y el ciclo de vida de fitz:
- `gestor_contable/gui/pdf_viewer.py` -- rendering, zoom, seleccion de texto
- `gestor_contable/config.py` -- `is_onedrive_placeholder()` (si existe)

**Fuera de alcance** (lo cubren otros skills):
- Protocolo SHA256 de classify_record --> audit-safe-move
- Threading del rendering --> audit-concurrency
- Portabilidad de rutas Z: --> audit-config-paths

## Paso 1: Verificar ciclo de vida de file handles

```
Buscar en pdf_viewer.py:
1. "fitz\.open\(|pymupdf\.open\(" -- toda apertura de documento
2. "\.close()|doc\.close\(\)" -- cierre explicito
3. "release_file_handles|_release|close_doc" -- funcion de liberacion
4. "try.*fitz|finally.*close|with.*fitz" -- garantia de cierre (try/finally o context manager?)
5. "del.*doc|doc\s*=\s*None" -- liberar referencia para GC
```

## Paso 2: Verificar liberacion antes de clasificar

```
Buscar en gui/main_window.py:
1. "release_file_handles" antes de "classify_record" -- orden correcto?
2. "PermissionError|WinError 32" en el flujo de clasificacion -- detecta archivo bloqueado?
```

## Paso 3: Verificar deteccion de placeholders OneDrive

```
Buscar en pdf_viewer.py y config.py:
1. "is_onedrive_placeholder|placeholder" -- funcion de deteccion
2. "stat\(\)\.st_size.*==.*0|size.*==.*0" -- deteccion por tamano cero
3. Que hace si detecta placeholder -- muestra mensaje? intenta abrir y crashea?
```

## Paso 4: Verificar rendering

```
Buscar en pdf_viewer.py:
1. "get_pixmap|render_page|page\.get_pixmap" -- rendering
2. "zoom|matrix.*fitz\.Matrix|scale" -- calculo de zoom
3. "get_text|get_words" -- extraccion de texto para seleccion
```

## Paso 5: Generar reporte

```
AUDITORIA: VISOR PDF
======================

Archivos revisados: [lista de archivos que realmente leiste]

CICLO DE VIDA DEL FILE HANDLE
-------------------------------
Apertura: [fitz.open() linea:N]
Cierre garantizado: [SI (try/finally linea:N) / NO (solo en happy path)]
Liberado antes de classify: [SI linea:N / NO]
Deteccion de placeholder: [SI linea:N / NO]

RENDERING
----------
Zoom: [implementado linea:N / no]
Seleccion de texto: [implementado linea:N / no]
Pixmaps liberados al cambiar documento: [SI / NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [handle bloqueado, crash, memory leak -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. El visor maneja handles correctamente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
