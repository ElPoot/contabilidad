---
name: audit-xml-pdf-link
description: Audita la estrategia de vinculacion entre PDFs y XMLs por clave de 50 digitos, heuristicas de nombre, raw bytes y consecutivo fallback. Detecta falsos matches, omisiones indebidas y seleccion del PDF incorrecto. Usar esta skill cuando mencionas vinculacion XML-PDF, PDF huerfano, clave no encontrada, heuristicas de nombre, raw bytes, consecutivo fallback, PDF mal enlazado, sin_xml incorrecto, PDF duplicado ganador, linking strategy, link PDF, PDF no enlazado.
---

# Auditoria: Vinculacion XML-PDF y Heuristicas Documentales

Sos un auditor especializado en la integridad del linking entre comprobantes. Si un PDF queda como `sin_xml` cuando tiene XML valido, el contador no puede clasificarlo. Si un PDF se enlaza al XML equivocado, se clasifica mal un documento fiscal.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas bugs. Si el linking funciona correctamente con las 5 estrategias en cascada, reporta "SIN PROBLEMAS".

## Alcance y limites

Este skill audita SOLO la vinculacion PDF-XML (las 5 estrategias en cascada):
- `gestor_contable/core/factura_index.py` -- logica completa de indexacion y linking
- `gestor_contable/core/pdf_cache.py` -- cache de extraccion PDF (afecta que texto esta disponible)

**Fuera de alcance** (lo cubren otros skills):
- Validacion de que la clave sea de 50 digitos y tipo documental por root.tag --> audit-fiscal-keys
- Robustez del parser XML --> audit-xml-parsing
- Cache stale o corrupto --> audit-cache
- Movimiento del archivo clasificado --> audit-safe-move

## Paso 1: Verificar que las 5 estrategias estan implementadas

Leer `factura_index.py` y buscar las 5 estrategias de linking en orden de precedencia:

```
1. Clave de 50 digitos en NOMBRE DE ARCHIVO del PDF
2. Clave de 50 digitos en CONTENIDO DE TEXTO del PDF (pymupdf)
3. Clave de 50 digitos en BYTES CRUDOS del PDF
4. CONSECUTIVO del XML vs texto del PDF (fallback)
5. Marcar como sin_xml si ninguna estrategia conecta
```

Para cada una, documentar: existe? en que linea? funciona correctamente?

## Paso 2: Verificar regla de multiples claves en NC

Buscar como se maneja el caso de PDFs de Nota Credito con dos claves:

```
Buscar en factura_index.py:
1. "claves|all_claves|findall.*\d{50}" -- lista de claves encontradas
2. "claves\[-1\]|last|ultima" -- seleccion de la clave correcta
```

Regla: si un PDF tiene multiples claves, SIEMPRE usar la ultima.

## Paso 3: Verificar razones de omision

```
Buscar en factura_index.py:
1. "non_invoice|timeout|extract_failed" -- razones de omision de PDFs
2. "razon_omision|omit_reason" -- como se asigna la razon
```

Verificar que las tres razones estan bien diferenciadas y se asignan en el contexto correcto.

## Paso 4: Generar reporte

```
AUDITORIA: VINCULACION XML-PDF
================================

Archivos revisados: [lista de archivos que realmente leiste]

ESTRATEGIAS DE LINKING
-----------------------
Estrategia 1 (clave en filename):    [IMPLEMENTADA linea:N / NO IMPLEMENTADA]
Estrategia 2 (clave en texto PDF):   [IMPLEMENTADA linea:N / NO IMPLEMENTADA]
Estrategia 3 (clave en bytes crudos):[IMPLEMENTADA linea:N / NO IMPLEMENTADA]
Estrategia 4 (consecutivo fallback): [IMPLEMENTADA linea:N / NO IMPLEMENTADA]
Estrategia 5 (sin_xml):             [IMPLEMENTADA linea:N / NO IMPLEMENTADA]

REGLA DE MULTIPLES CLAVES (NC)
-------------------------------
Manejo de multiples claves: [USA ULTIMA linea:N / USA PRIMERA / NO MANEJA]

RAZONES DE OMISION
--------------------
non_invoice: [IMPLEMENTADA / NO]
timeout: [IMPLEMENTADA / NO]
extract_failed: [IMPLEMENTADA / NO]

HALLAZGOS
---------
[Solo si encontraste problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema real encontrado
  Evidencia: [cita textual del codigo]
  Impacto: [consecuencia concreta]

[Si no hay problemas: "Ningun hallazgo. El linking funciona correctamente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
