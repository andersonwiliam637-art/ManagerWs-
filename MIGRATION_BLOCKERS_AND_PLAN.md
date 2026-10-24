# ManagerWS — revisión previa a staging: migraciones

Fecha de revisión: 2026-10-20

## Hallazgo bloqueante para cambios de esquema

El paquete declara Flask-Migrate, pero `migrations/` contiene únicamente `README.md` y no una estructura Alembic operativa con `env.py` y revisiones versionadas. No hay una revisión base ejecutable en este paquete.

Una lectura de solo lectura del proyecto Supabase asociado encontró 24 tablas en `managerws` y las tablas/columnas principales de facturación esperadas, incluida `subscription.provider_plan_id`. No se ejecutó la aplicación Flask contra esa base ni se ha completado una comparación de cada columna, tipo, índice, secuencia y restricción del ORM.

**Impacto:** no uses `flask db upgrade` ni `db.create_all()` como atajo. Primero debe terminarse la comparación integral y documentarse cómo versionar las modificaciones futuras del esquema.

## Medidas de contención presentes

- En producción, `AUTO_CREATE_DB=true` detiene el arranque.
- El arranque no llama a `db.create_all()` en producción.
- La documentación indica que el esquema `managerws` es privado y requiere una conexión PostgreSQL de backend.

Estas medidas evitan la creación automática imprudente, pero no sustituyen una migración versionada.

## Trabajo requerido antes de desplegar

1. Exportar el esquema actual de `managerws` sin datos sensibles y comparar tablas/columnas/índices/constraints con los modelos de `app.py`.
2. Crear la estructura Alembic en una rama y definir el tratamiento explícito del esquema `managerws`.
3. Generar y revisar la revisión inicial contra una copia de staging; no inferirla desde producción ni aplicar `create_all()`.
4. Verificar migraciones históricas fase por fase, ya que algunas usan nombres no calificados y pueden depender del `search_path`.
5. Ensayar upgrade y restauración del backup; registrar resultados y diferencias.

## Estado de verificación

- Pruebas locales incluidas: `39 passed, 9 subtests passed` en el entorno disponible.
- Compilación sintáctica de `app.py` y `financial.py`: correcta en el entorno de pruebas.
- Escaneo simple del ZIP en busca de `.env`, bases SQLite y nombres de archivos de credenciales: no encontró esos archivos.
- Se inspeccionó el catálogo de Supabase en modo de solo lectura; no se verificó una conexión de ejecución desde Flask a Supabase.
- No se ejecutó ninguna migración contra la base de datos.
- No se probó el flujo real de pagos ni el despliegue en Railway.
