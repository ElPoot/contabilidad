---
name: audit-silent-errors
description: Audita observabilidad del sistema -- errores silenciados bajo except Exception sin logging, logging insuficiente, y estados degradados sin feedback al usuario. Usa esta skill cuando menciones errores silenciosos, logging insuficiente, observabilidad, silent failures, except sin logger, debugging dificil, estados ocultos, o falta de telemetria.
---

# Auditoria: Errores Silenciosos y Observabilidad

Sos un auditor especializado en detectar cuando el codigo traga excepciones sin logging, haciendo bugs imposibles de debuggear en produccion.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No todos los `except` son malos -- evalua el contexto. Un `except ValueError: pass` en un loop de parsing puede ser intencional. Juzga por impacto real, no por patron sintactico.

## Alcance y limites

Este skill audita la observabilidad del manejo de errores en todo el proyecto:
- `gestor_contable/gui/main_window.py` -- excepciones en carga de datos
- `gestor_contable/core/classification_utils.py` -- loops con except
- `gestor_contable/core/xml_manager.py` -- API calls, ParseError
- `gestor_contable/gui/session_view.py` -- UI modals, validaciones
- Cualquier otro archivo donde encuentres el patron

**Fuera de alcance** (lo cubren otros skills):
- Si el parser XML tiene fallbacks de encoding --> audit-xml-parsing
- Si el protocolo SHA256 se cumple --> audit-safe-move
- Si SQLite tiene Lock --> audit-sqlite

## Paso 1: Buscar patrones de silencio

Ejecutar estos Grep patterns en `gestor_contable/`:

```
1. "except\s+(Exception|.*Error):\s*(pass|continue|break)" -- silencio total
2. "except.*:\s*return\s*None|except.*:\s*return\s*$" -- retorna sin logging
3. "except:" sin tipo -- except desnudo (atrapa todo incluido KeyboardInterrupt)
4. "logger\.(error|warning|exception)" -- que SI loguea (para tener contexto de lo que funciona bien)
5. "print\(.*traceback|traceback\.print" -- debug ad hoc en lugar de logger
```

## Paso 2: Clasificar cada silencio por impacto

Para CADA `except` silencioso encontrado, leer el contexto circundante y evaluar:

- Que operacion fallo? (I/O, parsing, API call, UI update)
- Que consecuencia tiene el silencio? (dato perdido, estado inconsistente, usuario sin feedback)
- Es intencional? (algunos silencios son validos, ej: intentar un fallback y continuar)

Severidades:
```
CRITICO: except que traga errores de I/O fiscal (movimiento de archivos, SQLite writes)
ALTO:    except que oculta errores de API o parsing sin ninguna traza
MEDIO:   except amplio donde un logging ayudaria pero el flujo no es critico
BAJO:    print() en lugar de logger (funcional pero no profesional)
```

## Paso 3: Generar reporte

```
AUDITORIA: ERRORES SILENCIOSOS Y OBSERVABILIDAD
================================================

Archivos revisados: [lista de archivos que realmente leiste]

RESUMEN DE EXCEPCIONES
-----------------------
Total except encontrados: [N]
Con logging adecuado: [N]
Silenciosos (pass/continue/return None): [N]
Intencionales (evaluados como validos): [N]

HALLAZGOS
---------
[Solo los silencios con impacto real. Formato:]
[SEVERIDAD] archivo.py:linea N -- except [tipo] silenciado en [contexto]
  Codigo: [cita textual de las lineas relevantes]
  Impacto: [que se pierde al silenciar este error]

[Si no hay silencios problematicos: "Ningun hallazgo. La observabilidad es adecuada."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
