---
name: audit-silent-errors
description: Audita observabilidad del sistema — errores silenciados bajo except Exception sin logging, logging insuficiente, y estados degradados sin feedback al usuario. Usa esta skill cuando menciones errores silenciosos, logging insuficiente, observabilidad, silent failures, except sin logger, debugging difícil, estados ocultos, o falta de telemetría.
model: haiku
---

# Auditoría: Errores Silenciosos y Observabilidad

Sos un auditor especializado en detectar cuándo el código traga excepciones sin logging, haciendo bugs imposibles de debuggear en producción.

## Archivos del alcance

Archivos de máximo riesgo:
- `gestor_contable/gui/main_window.py` — excepciones en carga de datos (líneas ~50-200, ~3500-4200)
- `gestor_contable/core/classification_utils.py` — loops con except
- `gestor_contable/core/xml_manager.py` — API calls, ParseError
- `gestor_contable/gui/session_view.py` — UI modals, validaciones (líneas ~50-150)

## Paso 1: Buscar patrones de silencio

Ejecutar estos Grep patterns GLOBALES:

```
1. "except\s+(Exception|.*Error):" — broad exception catching
2. "except.*:\s+(pass|continue|break)" — silencio total o salto
3. "except.*:\s+return|except.*:\s+return\s+None" — retorna sin logging
4. "logger\.(error|warning|exception)" — qué SÍ loguea
5. "print\(|traceback\." — debug output ad hoc en lugar de logger
```

## Paso 2: Clasificar severidad de cada silencio

Para CADA `except` encontrado:

```
CRITICO:
  - except Exception: pass
  - except: pass (desnudo)
  - API call sin logging si falla, retorna None/default
  - XML parsing sin error context

ALTO:
  - except sin logger.error(), retorna None
  - Loop continúa tras error sin contar/loguear qué falló
  - Usuario no ve error claro en UI

MEDIO:
  - Warnings genéricos que no explican contexto
  - print() en lugar de logger formal
```

## Paso 3: Generar reporte

```
AUDITORIA: ERRORES SILENCIOSOS Y OBSERVABILIDAD
================================================

Archivos revisados: [lista]

HALLAZGOS
---------
[CRITICO] main_window.py:linea X — except Exception: pass en _load_invoices()
  Problema: Si XML load falla, usuario no sabe
  Impacto: Debugging imposible en producción

[CRITICO] xml_manager.py:linea Y — API call except sin logging
  Evidencia: requests.RequestException tragado, retorna None
  Impacto: Timeout o error no rastreado

[ALTO] classification_utils.py:linea Z — except ParseError continúa sin registrar
  Problema: Qué registro falló? Por qué?
  Impacto: Errores silenciosos en procesamiento batch

TAREAS DE CORRECCION
--------------------
1. main_window.py:_load_invoices()
   → Agregar try-except con logger.exception()
   → Mostrar error claro al usuario en UI

2. xml_manager.py:fetch_from_hacienda()
   → Loguear requests.RequestException completa (traceback)
   → Incluir retry count, timeouts, códigos HTTP

3. classification_utils.py:filter_records()
   → Loguear qué registro falló (XML path, clave)
   → No silenciar ParseError

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```

## Relaciones (transversal)

- Bloquea debugging de: audit-xml-parsing, audit-safe-move, audit-fiscal-keys
- **audit-test-coverage:** Sin tests, silencios nunca se detectan en CI
