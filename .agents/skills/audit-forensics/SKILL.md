---
name: audit-forensics
description: Audita las herramientas forenses internas para investigar incidentes historicos sin corromper evidencia. Detecta shadow copies incompletas, SQL configurable inseguro y conclusiones debiles. Usar esta skill cuando mencionas forense, forensic, auditoria de sobreescritura, cadena de evidencia, shadow copy, WAL, incidente historico, evidencia.
---

# Auditoria: Herramientas Forenses Internas

Sos un auditor especializado en la capacidad forense del sistema. El modulo forense permite investigar incidentes historicos (sobreescrituras, perdidas) sin corromper la evidencia original.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que la herramienta forense es insegura. Verifica las garantias reales.

## Alcance y limites

Este skill audita SOLO las herramientas forenses:
- `gestor_contable/app/services/forensic_overwrite_audit.py` -- auditoria de sobreescrituras
- Reportes derivados (CSV, Markdown) generados por la herramienta

**Fuera de alcance** (lo cubren otros skills):
- Cuarentenas y purgas --> audit-quarantine
- Movimiento seguro de archivos --> audit-safe-move
- Persistencia SQLite --> audit-sqlite

## Paso 1: Verificar integridad de evidencia

```
Buscar en forensic_overwrite_audit.py:
1. "read_only|readonly|wal|shm" -- acceso read-only a la evidencia?
2. "copy|shadow|backup" -- crea shadow copies?
3. "modify|update|delete|insert" -- modifica datos originales? (RIESGO)
4. "lock|connect" -- tipo de conexion a SQLite
```

## Paso 2: Verificar cadena de evidencia

```
Buscar en forensic_overwrite_audit.py:
1. "sha256|hash|checksum" -- verifica integridad de archivos?
2. "timestamp|fecha|when" -- registra cuando se hizo la auditoria?
3. "consecutive|clave" -- cruza datos entre XML y SQLite?
4. "csv|markdown|report" -- formato del reporte generado
```

## Paso 3: Verificar seguridad del SQL

```
Buscar en forensic_overwrite_audit.py:
1. "f\"|f'|format\(|%" -- SQL con interpolacion de strings? (RIESGO)
2. "parameterize|\?" -- queries parametrizadas?
3. "user.*input|external.*sql" -- acepta SQL externo? (RIESGO)
```

## Paso 4: Generar reporte

```
AUDITORIA: HERRAMIENTAS FORENSES INTERNAS
=============================================

Archivos revisados: [lista de archivos que realmente leiste]

INTEGRIDAD DE EVIDENCIA
--------------------------
Acceso read-only: [SI / NO (RIESGO)]
Shadow copies: [SI (WAL/SHM copiados) / NO]
Modifica datos originales: [NO / SI (CRITICO -- donde)]

CADENA DE EVIDENCIA
---------------------
Verifica hashes: [SI / NO]
Registra timestamp: [SI / NO]
Cruza XML con SQLite: [SI (como) / NO]
Formato de reporte: [CSV / Markdown / otro]

SEGURIDAD SQL
--------------
Queries parametrizadas: [SI / NO]
SQL con interpolacion: [NO / SI (RIESGO -- donde)]
Acepta SQL externo: [NO / SI (evaluar riesgo)]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [evidencia corrompida, SQL injection -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. Las herramientas forenses son seguras."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
