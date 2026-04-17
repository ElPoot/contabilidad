---
name: audit-fiscal-keys
description: Audita integridad de claves Hacienda (50 digitos exactos) y clasificacion de tipo documental por root.tag del XML. Usa esta skill cuando menciones clave fiscal, 50 digitos, tipo de documento, FacturaElectronica, NotaCreditoElectronica, NC vs FE, multiples claves en PDF, clasificacion por filename, o falsos positivos de tipo documental.
---

# Auditoria: Integridad Fiscal de Claves y Tipologia Documental

Sos un auditor especializado en garantizar que NUNCA se clasifique un documento por filename y que tipo_documento SIEMPRE venga del root.tag del XML.

## Regla fundamental

Reporta UNICAMENTE lo que encuentres leyendo el codigo actual. No asumas bugs. Si la extraccion de claves y la tipologia funcionan correctamente, reporta "SIN PROBLEMAS". No inventes hallazgos para llenar el reporte.

## Alcance y limites

Este skill audita SOLO la extraccion de claves fiscales y la determinacion de tipo documental:
- `gestor_contable/core/xml_manager.py` -- `flatten_xml_stream()`, extraccion de clave desde XML
- `gestor_contable/core/factura_index.py` -- `_extract_clave_from_pdf()`, regla de multiples claves
- `gestor_contable/core/models.py` -- clase `FacturaRecord`, campos `clave` y `tipo_documento`

**Fuera de alcance** (lo cubren otros skills):
- Estrategia completa de vinculacion PDF-XML (5 pasos en cascada) --> audit-xml-pdf-link
- Robustez del parser XML ante encoding --> audit-xml-parsing
- Movimiento de archivos clasificados --> audit-safe-move

## Paso 1: Verificar determinacion de tipo documental

Leer `flatten_xml_stream()` en `xml_manager.py` y verificar:

```
Buscar:
1. "root\.tag|FacturaElectronica|NotaCreditoElectronica|MensajeHacienda" -- si lee el tag raiz
2. "tipo_documento|tipo_doc" -- como se asigna el tipo
3. "_NC\.|_respuesta\.|_firmado\." -- si el filename influye en la clasificacion (PROHIBIDO)
```

Regla critica: el tipo documental (01=factura, 03=NC, etc.) DEBE determinarse por el root.tag del XML, NUNCA por el nombre del archivo.

## Paso 2: Verificar extraccion de clave de 50 digitos

Leer `_extract_clave_from_pdf()` en `factura_index.py` y verificar:

```
Buscar:
1. "\d{50}|50.*digit|clave.*len" -- patron de extraccion de 50 digitos
2. "claves.*\[|findall|re\." -- si busca multiples claves
3. "claves\[-1\]|last|ultima" -- si usa la ULTIMA clave cuando hay multiples
```

Regla critica para PDFs de NC: si un PDF contiene dos claves (la factura original + la NC actual), SIEMPRE debe usarse la ULTIMA encontrada.

## Paso 3: Verificar validacion de longitud

```
Buscar en xml_manager.py y factura_index.py:
1. "len.*clave.*==.*50|clave.*len.*50" -- validacion de exactamente 50 digitos
2. "isdigit|\.match.*\d" -- validacion de que son solo digitos
```

## Paso 4: Generar reporte

```
AUDITORIA: INTEGRIDAD FISCAL DE CLAVES
=======================================

Archivos revisados: [lista de archivos que realmente leiste]

TIPO DOCUMENTAL
----------------
Fuente de tipo_documento: [root.tag del XML linea:N / filename / otro]
Tags reconocidos: [lista de tags que el codigo reconoce]
Filename influye en tipo: [NO (correcto) / SI (violacion) -- evidencia]

EXTRACCION DE CLAVE
--------------------
Patron de busqueda: [regex o metodo usado -- linea:N]
Validacion de 50 digitos: [SI linea:N / NO]
Multiples claves en PDF: [USA ULTIMA linea:N / USA PRIMERA (error) / NO MANEJA]

HALLAZGOS
---------
[Solo si encontraste problemas reales. Formato:]
[SEVERIDAD] archivo.py:linea N -- descripcion del problema real encontrado
  Evidencia: [cita textual del codigo]
  Impacto: [consecuencia concreta]

[Si no hay problemas: "Ningun hallazgo. Claves y tipologia funcionan correctamente."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
