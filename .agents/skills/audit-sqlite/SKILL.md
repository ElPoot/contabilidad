---
name: audit-sqlite
description: Valida que todo acceso SQLite use threading.Lock(), que los esquemas sean idempotentes y que no haya conexiones directas desde la GUI. Usar esta skill cuando mencionas SQLite, threading lock, concurrencia base de datos, clasificacion.sqlite, migraciones, ALTER TABLE, esquema, ClassificationDB, writes sin lock, race condition en BD, tabla de cuarentena.
---

# Auditoria: Persistencia SQLite y Locking

Sos un auditor especializado en la integridad del acceso concurrente a SQLite. Sin `threading.Lock()` en cada acceso, cualquier operacion desde un worker thread puede corromper datos o generar "database is locked".

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que todo modulo SQLite esta roto. Usa `ClassificationDB` en `classifier.py` como patron de referencia y compara los demas contra ese patron.

## Alcance y limites

Este skill audita SOLO el acceso SQLite y su locking:
- `gestor_contable/core/classifier.py` -- `ClassificationDB` (PATRON DE REFERENCIA)
- `gestor_contable/core/xml_cache.py` -- cache XML
- `gestor_contable/core/duplicates_quarantine.py` -- cuarentena de duplicados
- `gestor_contable/core/ors_purge.py` -- purga ORS
- `gestor_contable/core/receptor_purge.py` -- purga receptor
- `gestor_contable/core/cabys_manager.py` -- catalogo CABYS

**Fuera de alcance** (lo cubren otros skills):
- Si gui/ accede a SQLite directamente --> audit-gui-layers
- Si hay errores silenciosos en BD --> audit-silent-errors
- Coherencia de caches JSON/SQLite --> audit-cache

## Paso 1: Mapear todas las conexiones SQLite

```
Buscar en gestor_contable/:
1. "sqlite3\.connect\(" -- toda conexion SQLite
2. "threading\.Lock()" en los mismos archivos -- Lock presente?
3. "with self\._lock:" -- uso correcto como context manager
```

## Paso 2: Leer el patron de referencia

Leer `ClassificationDB` en `classifier.py` y documentar:
- Como inicializa el Lock
- Si cada metodo que escribe usa `with self._lock:`
- Si los metodos de lectura tambien usan Lock

## Paso 3: Comparar cada modulo contra la referencia

Para cada otro archivo SQLite del alcance, verificar:
- Tiene Lock? Inicializado en __init__?
- Cada escritura pasa por `with self._lock:`?
- Usa context manager (`with sqlite3.connect(...)`) o abre/cierra manual?
- CREATE TABLE usa IF NOT EXISTS?

## Paso 4: Generar reporte

```
AUDITORIA: PERSISTENCIA SQLITE Y LOCKING
=========================================

Archivos revisados: [lista de archivos que realmente leiste]

PATRON DE REFERENCIA (ClassificationDB)
-----------------------------------------
Lock inicializado: [SI/NO linea:N]
Escrituras con lock: [SI/NO -- detalle]
Lecturas con lock: [SI/NO -- detalle]

INVENTARIO DE MODULOS SQLITE
-------------------------------
Modulo                    | Lock | with self._lock | CREATE idempotente | Context manager
classifier.py (REFERENCIA)| [?]  | [?]             | [?]                | [?]
xml_cache.py              | [?]  | [?]             | [?]                | [?]
duplicates_quarantine.py  | [?]  | [?]             | [?]                | [?]
ors_purge.py              | [?]  | [?]             | [?]                | [?]
receptor_purge.py         | [?]  | [?]             | [?]                | [?]
cabys_manager.py          | [?]  | [?]             | [?]                | [?]

HALLAZGOS
---------
[Solo diferencias reales contra el patron de referencia. Formato:]
[SEVERIDAD] archivo.py:linea N -- [que falta o difiere del patron]
  Evidencia: [cita textual del codigo]
  Impacto: [corrupcion, lock, crash -- cual exactamente]

[Si todos siguen el patron: "Ningun hallazgo. Todos los modulos siguen el patron de ClassificationDB."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
