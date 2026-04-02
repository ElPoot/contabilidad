---
name: analizar-reporte-errores
description: Analiza archivos de error del Gestor Contable (errores_*.txt) leyendo XMLs REALES de la red para diagnosticar si los fallos son datos corruptos del emisor o bugs que arreglar en el código. DISPÁRATE SIEMPRE que el usuario mencione reportes de errores, archivos errores_*.txt, ParseError, XMLs fallidos, problemas de carga, encoding inválido, o necesite diagnosticar qué acción tomar (omitir vs arreglar). Lee archivos sin suposiciones. Da veredictos claros con evidencia.
model: haiku
compatibility: []
---

# Analizar Reporte de Errores — Gestor Contable

Cuando el Gestor Contable carga XMLs para un cliente, genera un archivo `errores_*.txt` que reporta incidencias agrupadas por tipo. Tu tarea es investigar esos errores para dar un **veredicto claro**: ¿es un problema del dato (XML roto) o un bug que debemos arreglar?

## Flujo de investigación

### 1. Leer el reporte y extraer metadata

Del archivo `errores_*.txt`:
- Extrae `CARPETA_XML` y `CARPETA_PDF`
- Extrae la lista de errores agrupados por categoría
- Identifica qué categorías tienen errores (xml_failed, respuesta_failed, total_mismatch, etc.)

### 2. Investigar por categoría, en este orden de prioridad

#### **xml_failed (ParseError)** — MÁXIMA PRIORIDAD
Estos XMLs NO se cargaron. Cada línea dice:
```
archivo.xml: ParseError: [tipo de error]: line [N], column [M]
```

**Investigación:**
1. Lee el archivo XML indicado (con `offset` + `limit` mínimo para ir a la línea exacta)
2. Ve a línea:columna del error
3. Identifica el carácter problemático:
   - `\x00`–`\x1F` (carácter de control): archivo dañado
   - `&` sin escape o entidad inválida: XML malformado del emisor
   - `<` dentro de texto: tag no cerrado correctamente
   - "no element found" (final de archivo inesperado): XML cortado o corrupto
   - Encoding issues (BOM Latin-1, UTF-8 con acento en línea 1, col baja): error de encoding del emisor

**Veredicto:**
- Si el problema es contenido del XML (carácter inválido, estructura rota): → **OMITIR**. El XML del emisor está corrupto. No hay nada que arreglar en nuestro código.
- Si el error podría manejarse mejor (ej. "nuestro parser podría intentar Latin-1 como fallback"): → **INVESTIGAR**. Revisa `gestor_contable/core/xml_manager.py:flatten_xml_stream()` para ver si ya intenta fallbacks de encoding. Si no → **ARREGLAR**.

**Agrupación:** Si ves 5+ XMLs del mismo emisor (primeros 8 dígitos del nombre del archivo = cédula) con el mismo tipo de ParseError (ej. todos "invalid token: line 21, column 79"), reporta UNA sola explicación consolidada para todo ese grupo.

#### **respuesta_failed** — ALTA PRIORIDAD  
El archivo `*_respuesta.xml` de Hacienda no es legible. El XML principal SÍ se cargó.

**Investigación:**
1. Lee el `_respuesta.xml` indicado (primeros 500 bytes)
2. Es XML inválido o encoding inválido?
3. Referencia: commit `5bfc170` ("Fix: Recuperar MensajeHacienda con encoding Latin-1") ya manejó esto

**Veredicto:**
- Si es encoding Latin-1: → Ya debería estar arreglado. Si no, → **ARREGLAR**.
- Si es XML roto: → **OMITIR** (no es nuestro problema).

#### **total_mismatch / iva_mismatch** — BAJA PRIORIDAD
Advertencias matemáticas. Facturas SÍ se cargaron.

**Veredicto:** → **OMITIR**. Son redondeos o descuentos no separados del emisor. La factura ya está en el sistema.

#### **xml_duplicate**
Dos XMLs con la misma clave de 50 dígitos. Se conservó el primero.

**Veredicto:** → **OMITIR** salvo que el usuario pregunte por discrepancias de montos.

#### **pdf_duplicate**
Dos PDFs apuntaron a la misma clave. Sistema conservó el más pesado.

**Veredicto:** → **OMITIR**. Está manejado automáticamente.

### 3. Formato de respuesta: Reporte consolidado

Para cada categoría con errores, da UNA respuesta consolidada (no listar todos los archivos si el patrón ya es claro tras los primeros 3-5):

```
## [CATEGORÍA]

**Archivos afectados:** N total
  - Emisor A (cédula): 15 archivos
  - Emisor B (cédula): 8 archivos

**Causa raíz:**
[Explicación clara de qué tiene el XML/dato]

**¿Es bug nuestro?** 
[Sí / No] + justificación concisa

**Acción recomendada:**
- Si Sí: Arreglar en `archivo.py:función()` — [descripción breve]
- Si No: Omitir. El dato viene roto del emisor.
```

## Reglas clave

- **NO leas todos los XMLs uno por uno** si el patrón ya es claro tras revisar 3-5 ejemplos
- **Agrupa por emisor** (primeros 8 dígitos del nombre = cédula)
- **Prioriza xml_failed** (son facturas no cargadas)
- **Revisa xml_manager.py** si sospechas bug: `flatten_xml_stream()`, `load_xml_folder()`, intentos de encoding fallback
- **Usa Read con offset + limit mínimo** para ir directo a la línea del error
- **Sé directo:** "OMITIR: XML del emisor está corrupto" o "ARREGLAR: Parser no intenta Latin-1"

## Recursos del proyecto

**Ubicación de archivos:**
- `gestor_contable/core/xml_manager.py` — parsing, `flatten_xml_stream()`, `load_xml_folder()`
- `gestor_contable/core/factura_index.py:283` — recopilación de `parse_errors`
- `gestor_contable/gui/main_window.py:4000-4122` — generación del reporte .txt

**Rutas de red (típicas):**
- XML: `Z:/DATA/PF-{año}/CLIENTES/{cliente}/XML/`
- PDF: `Z:/DATA/PF-{año}/CLIENTES/{cliente}/PDF/`
