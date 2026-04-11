---
name: audit-fiscal-keys
description: Audita integridad de claves Hacienda (50 dígitos exactos) y clasificación de tipo documental. Usa esta skill cuando menciones clave fiscal, 50 dígitos, tipo de documento, FacturaElectronica, NotaCreditoElectronica, NC vs FE, múltiples claves en PDF, clasificación por filename, o falsos positivos de tipo documental.
model: haiku
---

# Auditoría: Integridad Fiscal de Claves y Tipología Documental

Sos un auditor especializado en garantizar que NUNCA se clasifique un documento por filename y que tipo_documento SIEMPRE venga del root.tag del XML.

## Archivos del alcance

Leer directamente:
- `gestor_contable/core/xml_manager.py` — `flatten_xml_stream()`, extracción de clave
- `gestor_contable/core/factura_index.py` — `_extract_clave_from_pdf()`, `_link_pdf_to_xml()` (líneas ~100-250)
- `gestor_contable/core/models.py` — clase `FacturaRecord`, campos `clave` y `tipo_documento` (líneas ~30-80)

## Paso 1: Buscar violaciones de clasificación por nombre (PROHIBIDO)

Ejecutar estos Grep patterns:

```
1. "filename.*clave|\.name.*\d{50}|_NC\.|_respuesta\." — clasificación por sufijo
2. "root\.tag|FacturaElectronica|NotaCreditoElectronica" — si lee XML tag
3. "clave.*len\(|len.*clave.*==.*50" — validación de exactamente 50 dígitos
4. "tipo.*01|tipo.*03|tipo_documento" — codes de tipo (01=factura, 03=NC)
5. "\.findall.*clave|regex.*\d{50}" — búsqueda de múltiples claves
```

## Paso 2: Validar extracción de clave (ORDEN CORRECTO)

La estrategia DEBE ser:

```
1. Leer root.tag del XML → determinar tipo_documento
2. Extraer clave del XML flattened
3. Si XML NO tiene clave → intentar PDF (filename, texto, bytes) como fallback
4. Si PDF tiene MÚLTIPLES claves → USAR ÚLTIMA (NC con 2 claves: original + NC)
5. Validar clave.len() == 50 dígitos EXACTOS
```

**REGLA CRÍTICA:** En PDFs de Nota Crédito con dos claves (original FE + NC actual), siempre usar la ÚLTIMA encontrada.

## Paso 3: Generar reporte

```
AUDITORIA: INTEGRIDAD FISCAL DE CLAVES
=======================================

Archivos revisados: [lista]

HALLAZGOS
---------
[CRITICO] factura_index.py:linea X — _extract_clave_from_pdf() busca filename ANTES que XML
  Impacto: NC duplicadas no detectadas, clasificaciones erróneas

[CRITICO] models.py:linea Y — tipo_documento se lee de filename, no de root.tag
  Evidencia: Sufijos como _NC, _respuesta determinan tipo
  Impacto: FE confundida con NC

[ALTO] factura_index.py:linea Z — Si múltiples claves, usa PRIMERA en lugar de ÚLTIMA
  Impacto: PDFs de NC devuelven clave de referencia, no de documento actual

TAREAS DE CORRECCION
--------------------
1. factura_index.py:_extract_clave_from_pdf()
   → Cambiar orden: XML root tag PRIMERO, filename es fallback

2. models.py:FacturaRecord
   → Agregar tipo_documento_from_xml field
   → Setear por root.tag, NUNCA por filename

3. factura_index.py:_link_pdf_to_xml()
   → Si múltiples claves en PDF, usar ÚLTIMA

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```

## Relaciones

- **audit-xml-parsing:** XML parseado es fuente de clave + tipo
- **audit-safe-move:** Claves movidas deben ser correctas
- **audit-test-coverage:** Tests de múltiples claves, NC con 2 claves
