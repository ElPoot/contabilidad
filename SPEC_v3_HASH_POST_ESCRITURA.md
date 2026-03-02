# Especificación v3: Validación Hash Post-Escritura para Integridad Fiscal

**Fecha:** 2026-03-02
**Status:** Planificado para próxima versión (v3)
**Prioridad:** MEDIA (mejora de auditoría fiscal, no crítico)
**Riesgo de implementación:** MEDIO (toca lógica de clasificación)

---

## 📋 Resumen Ejecutivo

Actualmente, la clasificación de archivos usa `shutil.move()` + persistencia en BD sin validación explícita de integridad post-movimiento.

Para **auditoría fiscal fuerte** en redes SMB/corporativas, necesitamos implementar validación hash post-escritura igual a lo documentado en CLAUDE.md pero no implementado actualmente.

---

## 🔴 Problema Actual

### Brecha Documentación vs Implementación

**CLAUDE.md especifica:**
```
1. Compute SHA256 of original PDF
2. Create destination folder
3. Copy with shutil.copy2()
4. Compute SHA256 of copy
5. If mismatch → delete copy + raise error (original stays intact)
6. If match → only then delete original
7. Record in clasificacion.sqlite with original SHA256
```

**Implementación actual (classifier.py:classify_record):**
```python
shutil.move(origen, destino)  # Atómico en same volume, pero...
actualizar_BD(estado="clasificado")  # Sin validación post-move
# Si falla entre move y BD update, inconsistencia
```

### Riesgo Específico en Entorno Costa Rica

- ✅ Z: drive probablemente en **SMB/network corporativo**
- ⚠️ SMB no es tan seguro como filesystem local (timeouts, corrupción de caché, etc.)
- 🔴 **Sin hash post-escritura:** No hay evidencia de integridad si Hacienda audita
- 📝 Regulaciones fiscales CR → requieren trazabilidad de documentos

---

## ✅ Solución Propuesta

### 1. Extender Modelo FacturaRecord

```python
# app3/core/models.py
@dataclass
class FacturaRecord:
    # ... campos existentes ...

    # NUEVOS CAMPOS PARA VALIDACIÓN
    hash_origen: str = None         # SHA256 del PDF original (antes de mover)
    hash_destino: str = None        # SHA256 del PDF en carpeta clasificada
    integridad_verificada: bool = False  # Flag de validación post-escritura
```

### 2. Refactorizar classify_record()

```python
def classify_record(
    record: FacturaRecord,
    session_folder: Path,
    db: ClassificationDB,
    categoria: str,
    subtipo: str,
    nombre_cuenta: str,
    proveedor_cedula: str,
) -> bool:
    """
    Clasificación SEGURA con validación hash post-escritura.

    Protocolo:
    1. Calcular hash_origen del PDF
    2. Crear carpeta destino
    3. Copy con shutil.copy2() (preserva metadata)
    4. Calcular hash_destino del PDF copiado
    5. Si mismatch → borrar copia, raise error, original intacto
    6. Si match → borrar original
    7. Guardar en BD con ambos hashes + flag verificado

    Returns:
        True si clasificación fue exitosa y verificada
        False si falló validación de integridad
    """

    try:
        # 1. Hash origen
        hash_origen = _compute_sha256(record.pdf_path)
        logger.info(f"Hash origen [{record.clave}]: {hash_origen}")

        # 2. Preparar destino
        dest_folder = build_dest_folder(...)
        dest_folder.mkdir(parents=True, exist_ok=True)
        dest_path = dest_folder / record.pdf_path.name

        # 3. Copy (no move aún)
        shutil.copy2(str(record.pdf_path), str(dest_path))
        logger.info(f"Copiado a: {dest_path}")

        # 4. Hash destino
        hash_destino = _compute_sha256(dest_path)
        logger.info(f"Hash destino [{record.clave}]: {hash_destino}")

        # 5. Validar integridad
        if hash_origen != hash_destino:
            dest_path.unlink()  # Borrar copia corrupta
            logger.error(f"Hash mismatch [{record.clave}]: {hash_origen} != {hash_destino}")
            raise RuntimeError(
                f"Validación de integridad falló: {hash_origen} != {hash_destino}"
            )

        # 6. Delete original (ahora seguro)
        record.pdf_path.unlink()
        logger.info(f"Borrado original: {record.pdf_path}")

        # 7. Guardar en BD con metadatos de validación
        db.update_record(
            clave=record.clave,
            estado="clasificado",
            ruta_destino=str(dest_path),
            hash_origen=hash_origen,
            hash_destino=hash_destino,
            integridad_verificada=True,
            fecha_clasificacion=datetime.now(),
        )

        logger.info(f"Clasificación verificada [{record.clave}]")
        return True

    except Exception as e:
        # Limpieza en caso de error
        if dest_path.exists():
            dest_path.unlink()
        logger.error(f"Error en clasificación [{record.clave}]: {e}")
        raise
```

### 3. Helper Utility

```python
# app3/core/classifier.py
def _compute_sha256(file_path: Path) -> str:
    """Calcular SHA256 de un archivo."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()
```

### 4. Migración de BD

```python
# app3/core/classifier.py - función nueva
def migrate_db_add_hash_columns():
    """
    Agregar columnas hash_origen, hash_destino, integridad_verificada
    a registros existentes que ya están clasificados.

    Lógica:
    - Para registros clasificados existentes:
      Si el archivo destino aún existe → calcular hash
      Si no existe → marcar integridad_verificada=False (auditoría manual)
    """
    pass
```

---

## 🔄 Cambios Requeridos

### Archivos a Modificar

| Archivo | Cambios | Complejidad |
|---------|---------|-------------|
| `app3/core/models.py` | +3 campos a FacturaRecord | 🟢 LOW |
| `app3/core/classifier.py` | Refactorizar `classify_record()` + helper | 🟠 MEDIUM |
| `app3/core/classifier.py` | Migración BD + inicializar nuevos campos | 🟠 MEDIUM |
| `tests/test_classifier.py` | Tests de validación hash | 🟠 MEDIUM |
| `CLAUDE.md` | Actualizar sec. "Safe File Move" | 🟢 LOW |

### Estimación de Trabajo

- **Implementación:** ~4 horas
- **Testing + edge cases:** ~2 horas
- **Migración de datos históricos:** ~1 hora
- **Total:** ~7 horas (1 día de trabajo concentrado)

### Riesgo de Regresión

- ⚠️ **MEDIO** — toca clasificación crítica
- Requiere:
  - Tests exhaustivos (happy path + edge cases)
  - Validación en staging antes de prod
  - Plan de rollback (revertir a `shutil.move()` si falla)

---

## 📊 Casos de Uso a Testear

### Happy Path
```
1. Clasificar PDF en carpeta correcta
2. Verificar hash_origen == hash_destino
3. Verificar original borrado
4. Verificar BD actualizada con metadatos
```

### Edge Cases
```
1. Hash mismatch (archivo corrupto en tránsito)
   → Borrar copia, original intacto, error claro

2. Falla borrado original (permisos, archivo abierto)
   → Copia verificada existe, original intacto, error
   → Usuario puede hacer retry (idempotente)

3. Falla BD update después de copy+verify
   → Copia existe en destino, metadata incompleta
   → Script de recovery puede rellenar hashes

4. Archivo destino ya existe (mismo nombre)
   → Usar sufijo automático (001, 002, etc.)
   → Guardar ruta real en BD

5. Carpeta destino no tiene permisos
   → Fail early, original intacto
```

---

## 🔐 Seguridad

### Garantías Post-Implementación

✅ **Integridad fiscal demostrable:**
- Hash origen vs hash destino prueba que no se corrompió
- BD registra ambos hashes + timestamp
- Original se borra SOLO si hash coincide

✅ **Auditoría:**
- Hacienda puede verificar: "¿Este PDF de Contabilidades/ es original?"
- Respuesta: "Sí, hash_origen == hash_destino desde [fecha]"

✅ **Recovery:**
- Si BD se corrompe, PDFs en Contabilidades/ siguen seguros
- Script puede recalcular hashes y rellenar BD

---

## 📝 Notas de Implementación

### No es "defensive programming"

Este cambio **NO es paranoia**. Para auditoría fiscal Costa Rica:
- SMB networks tienen latencies
- Filesystems remotos pueden corromper caché
- Hash post-escritura es **evidence requirement**, no "nice to have"

### Compatibilidad con versiones existentes

- v2 (actual) usa `shutil.move()` sin hash ← sigue funcionando
- v3 agrega hash post-escritura ← mejor evidencia
- Migración retroactiva: rellenar hashes donde sea posible

### Performance

- Agregará ~100-200ms por archivo (lectura doble = 2x SHA256)
- Aceptable: clasificación manual no es operación de alta frecuencia
- Batch de 50 PDFs: ~10 segundos en lugar de ~2 segundos (mínimo impacto)

---

## 🚀 Siguiente Paso

Cuando sea tiempo de v3:
1. Crear rama `feature/hash-post-escritura`
2. Implementar cambios (7 horas)
3. Tests exhaustivos
4. Validar en staging antes de deploy
5. Migración retroactiva de registros históricos

---

**Owner:** Security & Integrity Review
**Reviewer:** Codex (análisis estático)
**Status:** 🟡 PLANNED — próxima versión
