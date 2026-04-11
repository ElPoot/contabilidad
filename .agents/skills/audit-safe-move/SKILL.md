---
name: audit-safe-move
description: Audita el protocolo atómico SHA256 para movimiento de comprobantes fiscales. Usa esta skill cuando menciones safe move, SHA256, atomic file move, pérdida de archivos, protocolo fiscal, movimiento seguro de comprobantes, PermissionError, restauración de PDFs/XMLs, o integridad de documentos.
model: haiku
---

# Auditoría: Movimiento Seguro y No Pérdida de Comprobantes

Sos un auditor especializado en verificar que el protocolo atómico de movimiento de comprobantes fiscales se respete en TODO momento. Una falla en `classify_record()` puede significar pérdida de documentos.

## Archivos del alcance

Leer directamente:
- `gestor_contable/core/classifier.py` — función `classify_record()` (líneas ~100-250)
- `gestor_contable/core/duplicates_quarantine.py` — movimientos y restauraciones
- `gestor_contable/core/ors_purge.py` — purgas de ORS
- `gestor_contable/gui/main_window.py` — búsqueda de calls a `classify_record()`

## Paso 1: Buscar indicadores de violación

Ejecutar estos Grep patterns:

```
1. "copy2.*unlink|unlink.*copy2" — movimiento directo SIN safe_move_file()
2. "shutil\.(copy|move)" — copias fuera de protocolo
3. "except.*PermissionError" — retry loop del unlink (12 intentos)
4. "sha256|SHA256" — validación de integridad
5. "DELETE FROM.*WHERE" — limpieza de registros SQLite
```

## Paso 2: Validar protocolo exacto

El protocolo DEBE cumplir esto (en `classify_record()`):

```
1. Computar SHA256 del archivo original
2. Crear destino con mkdir(parents=True, exist_ok=True)
3. Copiar con shutil.copy2() (preserva metadata)
4. Computar SHA256 de la copia
5. Si mismatch → delete copia, RAISE error (original intacto)
6. Si match → delete original con retry loop (PermissionError 12 intentos)
7. Registrar en SQLite con sha256_original
```

## Paso 3: Generar reporte

```
AUDITORIA: MOVIMIENTO SEGURO DE COMPROBANTES
==============================================

Archivos revisados: [lista de archivos leídos]

HALLAZGOS
---------
[CRITICO] classifier.py:linea X — copy2+unlink sin SHA256 validation
  Impacto: Comprobante puede perderse si copy falla

[ALTO] ors_purge.py:linea Y — shutil.move() directo sin protocolo
  Impacto: ORS omitidos no garantizan integridad

TAREAS DE CORRECCION
--------------------
1. classifier.py:classify_record()
   → Refactor para usar safe_move_file(source, dest, original_sha256)
   → Validar match antes de unlink

2. ors_purge.py:purge_ors()
   → Usar safe_move_file() en lugar de shutil.move()

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```

## Relaciones con otros skills

- **audit-test-coverage:** Si hay violaciones, necesita tests del protocolo
- **audit-fiscal-keys:** Las claves del movido deben ser válidas
- **audit-silent-errors:** Las excepciones durante move deben loguearse
