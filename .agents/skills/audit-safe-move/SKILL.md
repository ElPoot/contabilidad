---
name: audit-safe-move
description: Audita el protocolo atomico SHA256 para movimiento de comprobantes fiscales. Usa esta skill cuando menciones safe move, SHA256, atomic file move, perdida de archivos, protocolo fiscal, movimiento seguro de comprobantes, PermissionError, restauracion de PDFs/XMLs, o integridad de documentos.
---

# Auditoria: Movimiento Seguro y No Perdida de Comprobantes

Sos un auditor especializado en verificar que el protocolo atomico de movimiento de comprobantes fiscales se respete en TODO momento. Una falla en `classify_record()` puede significar perdida de documentos.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas bugs. Si el codigo cumple el protocolo, reporta "SIN PROBLEMAS". No inventes hallazgos para llenar el reporte.

## Alcance y limites

Este skill audita SOLO el movimiento de archivos en el flujo de clasificacion:
- `gestor_contable/core/classifier.py` -- `classify_record()`, `safe_move_file()`
- `gestor_contable/core/ors_purge.py` -- movimientos de archivos ORS
- `gestor_contable/core/receptor_purge.py` -- movimientos de archivos receptor
- `gestor_contable/gui/main_window.py` -- buscar calls a `classify_record()` o `safe_move_file()`

**Fuera de alcance** (lo cubren otros skills):
- Cuarentenas y restauraciones de duplicados --> audit-quarantine
- Threading y locks de SQLite --> audit-sqlite, audit-concurrency
- Errores silenciosos genericos --> audit-silent-errors

## Paso 1: Buscar indicadores de violacion

Ejecutar estos Grep patterns:

```
1. "shutil\.(copy|move)" -- copias/movimientos de archivos (verificar que pasen por safe_move_file)
2. "sha256|SHA256|hashlib" -- validacion de integridad (donde aparece y donde falta)
3. "except.*PermissionError" -- retry loop del unlink
4. "\.unlink()\|os\.remove" -- eliminacion de archivos originales (debe ser DESPUES de verificar SHA256)
5. "safe_move_file\|classify_record" -- call sites del protocolo
```

## Paso 2: Validar protocolo exacto

Leer `classify_record()` y `safe_move_file()` en `classifier.py` y verificar que el flujo real sea:

```
1. Computar SHA256 del archivo original
2. Crear destino con mkdir(parents=True, exist_ok=True)
3. Copiar con shutil.copy2() (preserva metadata)
4. Computar SHA256 de la copia
5. Si mismatch --> delete copia, RAISE error (original intacto)
6. Si match --> delete original (con retry si PermissionError)
7. Registrar en SQLite con sha256_original
```

Verificar cada paso contra el codigo real. Si algun paso difiere, documentar la diferencia exacta con numero de linea.

## Paso 3: Verificar que otros modulos usen el protocolo

Leer `ors_purge.py` y `receptor_purge.py`. Si mueven archivos, verificar si:
- Usan `safe_move_file()` directamente, o
- Tienen su propia implementacion del protocolo, o
- Usan `shutil.move()` sin verificacion de integridad

## Paso 4: Generar reporte

```
AUDITORIA: MOVIMIENTO SEGURO DE COMPROBANTES
==============================================

Archivos revisados: [lista de archivos que realmente leiste]

PROTOCOLO EN classify_record() / safe_move_file()
---------------------------------------------------
Paso 1 (SHA256 original):     [CUMPLE / NO CUMPLE -- evidencia linea:N]
Paso 2 (mkdir destino):       [CUMPLE / NO CUMPLE -- evidencia linea:N]
Paso 3 (copy2):               [CUMPLE / NO CUMPLE -- evidencia linea:N]
Paso 4 (SHA256 copia):        [CUMPLE / NO CUMPLE -- evidencia linea:N]
Paso 5 (mismatch = rollback): [CUMPLE / NO CUMPLE -- evidencia linea:N]
Paso 6 (delete original):     [CUMPLE / NO CUMPLE -- evidencia linea:N]
Paso 7 (registro SQLite):     [CUMPLE / NO CUMPLE -- evidencia linea:N]

OTROS MODULOS QUE MUEVEN ARCHIVOS
------------------------------------
[archivo:funcion -- usa safe_move_file: SI/NO -- detalle]

HALLAZGOS
---------
[Solo si encontraste problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema real encontrado
  Evidencia: [cita textual del codigo]
  Impacto: [consecuencia concreta]

[Si no hay problemas: "Ningun hallazgo. El protocolo se cumple correctamente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
