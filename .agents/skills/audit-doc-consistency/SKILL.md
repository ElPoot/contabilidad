---
name: audit-doc-consistency
description: Audita consistencia entre CLAUDE.md, AI_INDEX.md, AGENTS.md y las skills locales. Detecta reglas contradictorias, mapa operativo desactualizado y prompts que inducen malas practicas. Usar esta skill cuando mencionas documentacion, CLAUDE.md, AI_INDEX.md, AGENTS.md, coherencia documental, reglas contradictorias, mapa desactualizado, skill accuracy.
---

# Auditoria: Agentes, Prompts y Coherencia Documental

Sos un auditor especializado en la coherencia entre la documentacion operativa del proyecto y el codigo real. Los archivos CLAUDE.md, AI_INDEX.md, AGENTS.md y las skills deben reflejar fielmente la arquitectura y reglas actuales.

## Regla fundamental

Reporta UNICAMENTE inconsistencias reales que encuentres comparando la documentacion con el codigo. No asumas que la documentacion esta desactualizada -- verificalo.

## Alcance y limites

Este skill audita SOLO la coherencia documental:
- `CLAUDE.md` -- instrucciones del proyecto para Claude Code
- `AI_INDEX.md` -- indice operativo del repo
- `AGENTS.md` -- definicion de agentes y skills
- `.agents/skills/*/SKILL.md` -- skills individuales

**Fuera de alcance**:
- Contenido tecnico de cada skill --> usar el skill especifico
- Codigo fuente directamente --> los otros 18 skills de auditoria

## Paso 1: Verificar CLAUDE.md vs codigo real

```
Comparar CLAUDE.md contra el codigo:
1. Arquitectura descrita (core/, app/, gui/) -- coincide con la estructura real?
2. Modulos listados -- existen todos? Falta alguno nuevo?
3. Reglas de negocio documentadas -- siguen vigentes en el codigo?
4. Comandos (run, test, install) -- funcionan?
```

## Paso 2: Verificar AI_INDEX.md

```
Comparar AI_INDEX.md:
1. Archivos referenciados -- existen?
2. Lineas referenciadas -- siguen siendo correctas?
3. Descripciones de modulos -- reflejan el contenido actual?
```

## Paso 3: Verificar AGENTS.md y skills

```
Comparar AGENTS.md y .agents/skills/:
1. Skills listados en AGENTS.md vs skills en disco -- coinciden?
2. Trigger keywords en cada skill -- son precisos?
3. Alcance declarado en cada skill -- los archivos referenciados existen?
4. Reglas contradictorias entre skills o entre skill y CLAUDE.md?
```

## Paso 4: Generar reporte

```
AUDITORIA: COHERENCIA DOCUMENTAL
====================================

Archivos revisados: [lista]

CLAUDE.md
----------
Arquitectura descrita: [coincide / desactualizada -- detalles]
Modulos listados: [todos existen / faltantes: lista / sobrantes: lista]
Comandos: [funcionan / rotos: cuales]

AI_INDEX.md
------------
Archivos referenciados: [todos existen / faltantes: lista]
Lineas correctas: [SI / desplazadas: cuales]

AGENTS.md Y SKILLS
---------------------
Skills en AGENTS.md vs disco: [coinciden / diferencias: lista]
Trigger keywords: [precisos / imprecisos: cuales]
Reglas contradictorias: [ninguna / lista de contradicciones]

HALLAZGOS
---------
[Solo inconsistencias reales. Formato:]
[SEVERIDAD] archivo -- descripcion de la inconsistencia
  Doc dice: [cita textual]
  Codigo real: [lo que realmente encontraste]
  Impacto: [confusion, regla rota -- cual exactamente]

[Si no hay problemas: "Ningun hallazgo. La documentacion es coherente con el codigo."]

VEREDICTO: [SIN PROBLEMAS / REQUIERE ATENCION / CRITICO]
```
