---
name: audit-test-coverage
description: Audita ausencia de suite de tests automatizados y dependencia total de validación manual. Usa esta skill cuando menciones falta de tests, cobertura de pruebas, regresiones no verificables, smoke tests, integración test, validación manual, matriz de testing, cambios críticos sin tests, o red de seguridad.
model: haiku
---

# Auditoría: Cobertura de Pruebas y Validación Manual

Sos un auditor especializado en detectar cambios críticos (fiscales, atómicos, API) que NO tienen tests automatizados y dependen 100% de validación manual o matriz de testing sin automatizar.

## Alcance

Estructura del repo:
- `gestor_contable/tests/` — ¿existe? ¿qué contiene?
- `gestor_contable/` root — buscar `conftest.py`, `pytest.ini`
- `.github/workflows/` — ¿CI/CD con job de tests?
- Documentación — `TESTING.md`, `CLAUDE.md` (matriz manual)

Funciones críticas SIN tests esperado (validar):
- `classify_record()` en classifier.py → protocolo SHA256 (CRÍTICA)
- `flatten_xml_stream()` en xml_manager.py → encoding fallbacks (CRÍTICA)
- `_extract_clave_from_pdf()` en factura_index.py → múltiples estrategias (CRÍTICA)
- `fetch_from_hacienda()` en xml_manager.py → API retry logic (CRÍTICA)

## Paso 1: Indicadores de ausencia de tests

Ejecutar estos Grep patterns:

```
1. "^tests/|test_.*\.py|_test\.py" en root — directorio de tests
2. "import pytest|import unittest|from pytest" en código — framework
3. "\.github/workflows.*test" — CI/CD con tests
4. "TESTING\.md|test-strategy" en docs — documentación de testing
5. "pytest.ini|tox.ini|conftest.py" en root — configuración
```

## Paso 2: Evaluar si hay matriz manual documentada

Si NO hay tests automatizados, ¿existe TESTING.md con matriz manual?

Checklist esperado en CLAUDE.md o TESTING.md:

```
[ ] Load client + change date range
[ ] Single selection and multi-selection  
[ ] Classify individual and batch
[ ] Export Excel/CSV
[ ] Sanitize, recover orphan, link omitted
[ ] Verify tree, PDF viewer, right panel in sync
```

¿Está actualizado? ¿Se ejecuta antes de release?

## Paso 3: Generar reporte

```
AUDITORIA: COBERTURA DE PRUEBAS
================================

Status de tests automatizados: [NO EXISTE / VACÍO / PARCIAL]

HALLAZGOS
---------
[CRITICO] No existe tests/ en gestor_contable/
  Impacto: Cambios a classifier.py, xml_manager.py no son verificables

[CRITICO] classify_record() sin test de protocolo SHA256
  Evidencia: Función crítica, cambios sin cobertura
  Impacto: Pérdida de comprobantes no detectada hasta producción

[CRITICO] flatten_xml_stream() sin test de encoding fallbacks
  Evidencia: FAPEMO XMLs (Latin-1 en UTF-8) requieren test
  Impacto: ParseError no recuperables

[ALTO] _extract_clave_from_pdf() sin test de múltiples claves
  Evidencia: NC con 2 claves requiere test de "usar ÚLTIMA"
  Impacto: Duplicados no detectados

[ALTO] Matriz manual de testing no documentada
  Evidencia: Sin TESTING.md o sección en CLAUDE.md
  Impacto: Cambios ejecutados sin cobertura conocida

TAREAS DE CORRECCION
--------------------
1. Crear tests/ directorio con pytest + coverage

2. tests/test_classifier.py
   → Happy path: copy, verify SHA256, delete original
   → Failure cases: SHA256 mismatch, PermissionError retry
   → Sqlite registration with sha256_original

3. tests/test_xml_manager.py
   → UTF-8 parsing
   → Latin-1 fallback (FAPEMO case)
   → ParseError con línea:columna
   → API timeout/retry 429/5xx

4. tests/test_factura_index.py
   → Clave en filename
   → Clave en texto PDF
   → Múltiples claves (NC con original)
   → Fallback por consecutivo

5. tests/test_gui_smoke.py (si aplicable)
   → Load client + period
   → Classify one invoice
   → Export Excel
   → Verify file moved

6. Documentar TESTING.md con:
   → Instrucciones para ejecutar: pytest, coverage
   → Matriz manual para cambios sin test coverage aún
   → Policy: cambios a core/ requieren test previo

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```

## Relaciones

- **Bloqueador para:** audit-safe-move, audit-xml-parsing, audit-fiscal-keys (validar cambios)
- **Depende de:** Si cambias funciones críticas, tests son OBLIGATORIO primero
