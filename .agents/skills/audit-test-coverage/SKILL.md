---
name: audit-test-coverage
description: Audita ausencia de suite de tests automatizados y dependencia total de validacion manual. Usa esta skill cuando menciones falta de tests, cobertura de pruebas, regresiones no verificables, smoke tests, integracion test, validacion manual, matriz de testing, cambios criticos sin tests, o red de seguridad.
---

# Auditoria: Cobertura de Pruebas y Validacion Manual

Sos un auditor especializado en detectar si existen tests automatizados y que tan expuestas estan las funciones criticas a regresiones no verificables.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres. Este proyecto puede no tener tests y eso es un hallazgo valido -- pero no inventes recomendaciones detalladas de que testear. Limitate a diagnosticar el estado actual.

## Alcance

Verificar la existencia y cobertura de:
- Directorio `tests/` o `gestor_contable/tests/`
- Archivos `conftest.py`, `pytest.ini`, `tox.ini`, `setup.cfg` con seccion pytest
- CI/CD: `.github/workflows/` con jobs de tests
- Documentacion: `TESTING.md` o seccion de testing en `CLAUDE.md`

Funciones criticas a verificar si tienen tests:
- `classify_record()` / `safe_move_file()` en `classifier.py`
- `flatten_xml_stream()` / `_safe_parse_xml_file()` en `xml_manager.py`
- `_extract_clave_from_pdf()` en `factura_index.py`

## Paso 1: Buscar infraestructura de tests

```
1. Glob: "tests/**/*.py" o "gestor_contable/tests/**/*.py"
2. Glob: "**/conftest.py", "**/pytest.ini", "**/tox.ini"
3. Glob: ".github/workflows/*.yml"
4. Grep: "import pytest|import unittest|from pytest" en todo el proyecto
```

## Paso 2: Si existen tests, evaluar cobertura

Leer los archivos de test encontrados y verificar:
- Que funciones/modulos estan cubiertos
- Si las funciones criticas listadas arriba tienen tests
- Si los tests se pueden ejecutar (dependencias, fixtures)

## Paso 3: Verificar matriz de validacion manual

Buscar en `CLAUDE.md` o `TESTING.md` si hay una lista de escenarios de validacion manual.

## Paso 4: Generar reporte

```
AUDITORIA: COBERTURA DE PRUEBAS
================================

INFRAESTRUCTURA DE TESTS
-------------------------
Directorio tests/: [EXISTE / NO EXISTE]
Framework (pytest/unittest): [ENCONTRADO / NO ENCONTRADO]
CI/CD con tests: [SI / NO]
Archivos de test encontrados: [lista o "ninguno"]

FUNCIONES CRITICAS
-------------------
classify_record(): [TEST EXISTE en X / SIN TEST]
safe_move_file(): [TEST EXISTE en X / SIN TEST]
flatten_xml_stream(): [TEST EXISTE en X / SIN TEST]
_safe_parse_xml_file(): [TEST EXISTE en X / SIN TEST]
_extract_clave_from_pdf(): [TEST EXISTE en X / SIN TEST]

MATRIZ DE VALIDACION MANUAL
-----------------------------
Documentada: [SI en archivo X / NO]
Actualizada: [SI / NO / NO APLICA]

HALLAZGOS
---------
[Solo hallazgos basados en lo encontrado. Formato:]
[SEVERIDAD] descripcion del gap de cobertura
  Impacto: [que riesgo crea la falta de test]

[Si hay tests adecuados: "Cobertura aceptable para las funciones criticas."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
