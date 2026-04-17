# Gestor Contable

Sistema de clasificación y organización de facturas electrónicas para firmas contables en Costa Rica.
Desarrollado en Python 3.10+ utilizando CustomTkinter.

## Descripción del Proyecto

Este sistema se encarga de:
1. **Carga masiva:** Escanear archivos XML y PDF desde unidades de red compartidas (OneDrive / Z:).
2. **Vinculación inteligente:** Asociar PDFs con su respectivo XML utilizando la Clave de Hacienda de 50 dígitos, extrayendo datos mediante PyMuPDF.
3. **Validación Fiscal:** Sincronizar y consultar estatus de aceptación mediante la API del Ministerio de Hacienda de Costa Rica.
4. **Clasificación Contable:** Proveer una interfaz rápida y oscura (dark theme) para categorizar documentos en cuentas (Compras, Gastos, Activos, etc.).
5. **Gestión Documental Atómica:** Trasladar los archivos físicos a las carpetas contables definitivas asegurando que no existan corrupciones mediante la verificación de hashes criptográficos SHA256.
6. **Reportes:** Exportar cortes mensuales estructurados en formato Excel (.xlsx).

## Requisitos y Configuración

- Python 3.10 o superior.
- Entorno Windows (Recomendado debido a integraciones de red y OneDrive).

### Instalación

```bash
# Clonar el repositorio
git clone <url-del-repositorio>
cd contabilidad

# Instalar dependencias
pip install -r requirements.txt
```

## Uso

Para ejecutar la aplicación localmente:

```bash
python gestor_contable/main.py
```

### Compilación (Build)

Para empaquetar el proyecto como un ejecutable independiente para Windows:

```bash
# Sube versión Patch (1.0.0 -> 1.0.1) y compila
python build.py

# Para subir la versión menor (1.0.1 -> 1.1.0)
python build.py minor
```
Los ejecutables se guardarán en la carpeta `dist/` y `releases/`.

## Arquitectura y Para Desarrolladores (Agentes de IA)

Este proyecto cuenta con reglas de desarrollo muy estrictas orientadas a la **Integridad Fiscal** y la **Concurrencia**. 

Si eres un desarrollador o un asistente de Inteligencia Artificial colaborando en este repositorio, **POR FAVOR LEE:**
- [AI_INDEX.md](AI_INDEX.md): Mapa operativo rápido del proyecto.
- [CLAUDE.md](CLAUDE.md): Directrices exhaustivas de desarrollo, reglas de negocio y estado del refactor arquitectónico.

*(Las directrices indicadas en la documentación técnica interna tienen precedencia para garantizar la seguridad operativa y de concurrencia).*