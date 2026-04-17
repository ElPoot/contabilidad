---
name: audit-pdf-generator
description: Audita la generacion de PDF desde XML cuando solo existe el XML sin comprobante PDF. Detecta PDFs no reindexables, representacion incompleta y errores silenciosos al extraer lineas. Usar esta skill cuando mencionas generar PDF, PDF desde XML, pdf_generator, factura sin PDF, fallback PDF, layout PDF.
---

# Auditoria: Generacion de PDF desde XML

Sos un auditor especializado en el modulo de generacion de PDF. Cuando una factura solo tiene XML y no tiene PDF asociado, el sistema genera un PDF con los datos del XML como fallback.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas que el generador esta roto. Verifica la funcionalidad real.

## Alcance y limites

Este skill audita SOLO la generacion de PDF desde XML:
- `gestor_contable/core/pdf_generator.py` -- modulo de generacion

**Fuera de alcance** (lo cubren otros skills):
- Vinculacion XML-PDF --> audit-xml-pdf-link
- Visor PDF (fitz rendering) --> audit-pdf-viewer
- Parsing XML --> audit-xml-parsing

## Paso 1: Verificar contenido del PDF generado

```
Buscar en pdf_generator.py:
1. "clave|key.*50" -- incluye la clave fiscal en el PDF?
2. "emisor|receptor|cedula" -- datos fiscales incluidos
3. "linea|detalle|item" -- lineas de detalle del comprobante
4. "total|monto|subtotal" -- montos incluidos
```

## Paso 2: Verificar reindexacion

```
Buscar en gestor_contable/:
1. "pdf_generator|generate_pdf" -- quien lo llama
2. "reindex|re.*index" -- el PDF generado se puede reindexar?
3. "fitz.*text|extract_text" -- el texto es extraible del PDF generado?
```

## Paso 3: Verificar manejo de errores

```
Buscar en pdf_generator.py:
1. "except|try" -- errores capturados
2. "log|warning|error" -- logging de problemas
3. "optional|import.*fitz" -- dependencia opcional de fitz
```

## Paso 4: Generar reporte

```
AUDITORIA: GENERACION DE PDF DESDE XML
==========================================

Archivos revisados: [lista de archivos que realmente leiste]

CONTENIDO DEL PDF
-------------------
Clave fiscal: [incluida / NO]
Datos fiscales (emisor, receptor, cedula): [SI / parcial / NO]
Lineas de detalle: [SI / NO]
Montos: [SI / NO]

REINDEXACION
--------------
PDF generado es reindexable: [SI / NO -- por que]
Texto extraible con fitz: [SI / NO]

MANEJO DE ERRORES
-------------------
Errores silenciosos: [SI (cuales) / NO]
Dependencia fitz: [obligatoria / opcional / no usa fitz]

HALLAZGOS
---------
[Solo problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema
  Evidencia: [cita textual del codigo]
  Impacto: [PDF incompleto, no reindexable -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La generacion de PDF es correcta."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
