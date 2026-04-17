---
name: audit-quarantine
description: Evalua los flujos reversibles de cuarentena y purga de comprobantes duplicados, ORS y respuestas receptor. Detecta cuarentenas incompletas, manifests inconsistentes y restauraciones parciales. Usar esta skill cuando mencionas cuarentena, purga, duplicados, ORS, receptor purge, manifest, restaurar, lote, batch_id, quarantine, restore, restauracion parcial.
---

# Auditoria: Cuarentenas, Purgas y Restauraciones

Sos un auditor especializado en la reversibilidad de las operaciones de aislamiento documental. Una cuarentena que mueve archivos sin registrar el lote en el manifest no puede restaurarse.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que las cuarentenas estan rotas. Verifica cada flujo completo: mover, registrar, restaurar.

## Alcance y limites

Este skill audita SOLO las cuarentenas y sus flujos de restauracion:
- `gestor_contable/core/duplicates_quarantine.py` -- cuarentena de PDFs duplicados
- `gestor_contable/core/ors_purge.py` -- purga/cuarentena de ORS
- `gestor_contable/core/receptor_purge.py` -- purga de respuestas receptor

**Fuera de alcance** (lo cubren otros skills):
- Protocolo SHA256 de classify_record --> audit-safe-move
- Locking SQLite de las DBs de cuarentena --> audit-sqlite
- Errores silenciosos durante la cuarentena --> audit-silent-errors

## Paso 1: Verificar protocolo de movimiento

Para cada archivo del alcance, verificar si el movimiento de archivos:
```
1. "shutil\.copy2|safe_move_file|shutil\.move" -- que funcion de movimiento usa
2. "sha256|hashlib|checksum" -- verifica integridad antes de eliminar original?
3. "unlink|os\.remove" -- cuando elimina el original (antes o despues de confirmar copia?)
```

## Paso 2: Verificar manifest y registro de lotes

```
1. "manifest|batch_id|lote" -- estructura del lote
2. "json\.dump|write.*manifest" -- escritura del manifest (antes o despues del movimiento?)
3. "sqlite3.*INSERT|db.*insert" -- registro en BD
```

## Paso 3: Verificar flujo de restauracion

```
1. "restore|restaur|recover" -- funcion de restauracion
2. "manifest.*read|json\.load.*manifest" -- lectura del manifest
3. "DELETE FROM|delete.*batch" -- limpieza post-restauracion
```

## Paso 4: Generar reporte

```
AUDITORIA: CUARENTENAS Y RESTAURACIONES
=========================================

Archivos revisados: [lista de archivos que realmente leiste]

INVENTARIO DE CUARENTENAS
---------------------------
Modulo                    | Movimiento seguro | Manifest completo | Restauracion funcional
duplicates_quarantine.py  | [SI/NO]           | [SI/NO]           | [SI/NO]
ors_purge.py              | [SI/NO]           | [SI/NO]           | [SI/NO]
receptor_purge.py         | [SI/NO]           | [SI/NO]           | [SI/NO]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [cuarentena irrestaurrable, archivo perdido -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. Las cuarentenas son reversibles."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
