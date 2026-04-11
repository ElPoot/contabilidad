---
name: audit-xml-parsing
description: Audita robustez del parser XML ante encoding corruption, malformed XML, y ParseError recovery. Usa esta skill cuando menciones XML parsing, encoding issues, ParseError, XML corruption, caracteres inválidos, Latin-1 fallback, UTF-8, FAPEMO XMLs, MensajeHacienda, o fallos de carga XML.
model: haiku
---

# Auditoría: Parsing XML y Recuperación de Encoding

Sos un auditor especializado en detectar cuándo el parser XML falla ante datos corruptos del emisor y cuándo debería tener fallbacks más robustos.

## Archivos del alcance

Leer directamente:
- `gestor_contable/core/xml_manager.py` — `flatten_xml_stream()`, `_safe_parse_xml_file()` (líneas ~100-300)
- `gestor_contable/core/xml_cache.py` — caching de XMLs (si existe)
- `gestor_contable/core/factura_index.py` — recopilación de `parse_errors` (líneas ~280-350)

## Paso 1: Buscar indicadores de falta de robustez

Ejecutar estos Grep patterns:

```
1. "except\s+(ET\.ParseError|ParseError)" — qué hace cuando falla
2. "ET\.parse|ET\.fromstring" — dónde ocurren parses
3. "encoding.*utf-8|latin-1|iso-8859" — estrategia de fallback
4. "\.decode\(.*'latin|\.encode\(" — conversiones de bytes
5. "BOM|bom|UTF-8-sig" — detección de BOM
```

## Paso 2: Validar estrategia de fallback

El parser DEBE intentar múltiples encodings EN ORDEN:

```
UTF-8 (default) → Latin-1 → ISO-8859-1 → si todo falla, log + error claro

Si archivo DECLARA UTF-8 pero es Latin-1 bytes (ej. FAPEMO):
  → Re-codificar: decode(latin-1).encode(utf-8).decode(utf-8)
```

## Paso 3: Generar reporte

```
AUDITORIA: PARSING XML Y ENCODING
==================================

Archivos revisados: [lista]

HALLAZGOS
---------
[CRITICO] xml_manager.py:linea X — Parser intenta UTF-8, si falla no hay fallback
  Evidencia: FAPEMO XMLs declaran UTF-8 pero contienen bytes Latin-1
  Impacto: ParseError no recuperable

[ALTO] xml_manager.py:linea Y — ParseError sin contexto de línea:columna
  Impacto: Debugging imposible

TAREAS DE CORRECCION
--------------------
1. xml_manager.py:_safe_parse_xml_file()
   → Agregar fallback a Latin-1 después de UTF-8
   → Loguear qué encoding funcionó

2. xml_manager.py:flatten_xml_stream()
   → Capturar ParseError con línea exacta
   → No silenciar sin logging

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```

## Relaciones

- **audit-silent-errors:** ParseError sin logging = debugging imposible
- **audit-test-coverage:** Fallbacks de encoding necesitan tests
- **audit-fiscal-keys:** XML parseado es base para clave
