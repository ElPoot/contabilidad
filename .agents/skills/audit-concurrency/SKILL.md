---
name: audit-concurrency
description: Audita si las operaciones pesadas salen del hilo principal y retornan a la UI con el patron .after() correcto. Detecta UI freezes, race conditions y patrones de threading ad-hoc. Usar esta skill cuando mencionas hilo principal, UI freeze, threading, race condition, .after(), workers, ThreadPoolExecutor, concurrencia, main thread, app se congela, interfaz no responde.
---

# Auditoria: UI, Concurrencia y Main Thread

Sos un auditor especializado en detectar problemas de concurrencia en aplicaciones customtkinter. La regla es absoluta: customtkinter solo puede tocarse desde el main thread. Cualquier `widget.configure()` desde un worker thread produce crash o comportamiento indefinido en Windows.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No todos los threading.Thread() son problematicos -- evalua si usan .after() para retornar a la UI. Juzga por el patron completo, no por la presencia de Thread sola.

## Alcance y limites

Este skill audita SOLO la concurrencia y el patron de comunicacion worker-UI:
- `gestor_contable/gui/main_window.py` -- hotspot principal de threading
- `gestor_contable/gui/session_view.py` -- carga de sesion con workers
- `gestor_contable/gui/orphaned_pdfs_modal.py` -- modal con operaciones pesadas
- `gestor_contable/app/controllers/load_period_controller.py` -- worker de carga

**Fuera de alcance** (lo cubren otros skills):
- Si los imports entre capas son correctos --> audit-gui-layers
- Si los archivos son demasiado grandes --> audit-hotspots
- Si SQLite tiene Lock --> audit-sqlite

## Paso 1: Mapear threads en gui/

```
Buscar en gui/:
1. "threading\.Thread\(" -- threads directos
2. "ThreadPoolExecutor|executor\.submit" -- pool de threads
3. "\.after\(0,|\.after\(\d+," -- retorno a UI desde worker
4. "Queue\(\)|queue\.put|queue\.get" -- patron Queue
```

## Paso 2: Verificar actualizaciones de UI desde workers

Esta es la violacion mas peligrosa. Para cada thread/worker encontrado:

1. Leer el metodo que se ejecuta como worker (target del Thread o submit)
2. Buscar si modifica widgets directamente (configure, insert, delete, pack, grid)
3. Verificar si usa .after(0, callback) para esas modificaciones

```
Buscar en los metodos worker:
1. "self\.\w+\.configure\(" sin .after() previo -- actualizacion directa
2. "self\.\w+\.insert\(|self\.\w+\.delete\(" sin .after() -- modificacion directa
```

## Paso 3: Verificar que operaciones pesadas NO esten en main thread

```
Buscar en gui/ en metodos que son callbacks de botones (no workers):
1. "factura_index\.|indexar" -- llamadas al indexador (BLOQUEANTE)
2. "requests\." -- llamadas HTTP en main thread (BLOQUEANTE)
3. "time\.sleep\(" -- sleeps en main thread (siempre freeze)
```

## Paso 4: Generar reporte

```
AUDITORIA: UI, CONCURRENCIA Y MAIN THREAD
==========================================

Archivos revisados: [lista de archivos que realmente leiste]

INVENTARIO DE THREADS
----------------------
Archivo              | Threads | .after() correcto | Riesgo
[archivo]            | [N]     | [SI/NO/PARCIAL]   | [bajo/medio/alto]

ACTUALIZACIONES DE UI DESDE WORKERS
--------------------------------------
[Para cada violacion encontrada:]
archivo.py:linea N -- [widget].configure() desde worker [nombre_metodo]
  Usa .after(): [SI / NO]

OPERACIONES BLOQUEANTES EN MAIN THREAD
-----------------------------------------
[Para cada operacion pesada encontrada en callbacks directos:]
archivo.py:linea N -- [operacion] en callback de [boton/evento]

HALLAZGOS
---------
[Solo problemas reales verificados. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [freeze, crash, race condition -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. El patron de concurrencia es correcto."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
