# ManagerWS — estado de migraciones

**Estado actual: BLOQUEADO para una migración automática de base de datos.** Esta carpeta todavía no contiene el entorno Alembic (`env.py`, `script.py.mako`, `alembic.ini`) ni una revisión versionada completa. Por eso, no asumir que `flask --app app db upgrade` puede crear o actualizar el esquema en esta entrega.

## Antes de habilitar migraciones

1. Obtener un respaldo consistente de la base PostgreSQL existente.
2. Restaurar ese respaldo en un entorno de staging aislado.
3. Confirmar las tablas y columnas reales del esquema privado `managerws` contra los modelos ORM de `app.py`.
4. Inicializar Flask-Migrate/Alembic en una rama de trabajo y generar una revisión base revisada manualmente. No ejecutar `db migrate` a ciegas contra producción.
5. Asegurar que Alembic opere en el esquema `managerws`, que no cree tablas paralelas en `public`, y que no elimine objetos existentes por diferencias de introspección.
6. Probar `upgrade` desde una copia de staging y documentar el plan de reversión. Las migraciones de datos o conversiones numéricas requieren validación específica.
7. Versionar los archivos Alembic resultantes y repetir el ensayo en una base limpia antes de desplegar.

## SQL históricos

Los archivos `migrations_phase*.sql` son scripts históricos de distintas fases, no una cadena Alembic verificada. Algunos scripts antiguos usan nombres de tabla sin calificar el esquema; no deben ejecutarse hasta confirmar explícitamente el `search_path` y el estado actual de la base. Las fases 9, 13 y 14 incluyen referencias explícitas a `managerws`, pero eso no demuestra por sí solo que todos los scripts sean compatibles ni que falten cero cambios.

## Regla de seguridad

- Mantener `AUTO_CREATE_DB=false` en Railway.
- No ejecutar `db.create_all()` contra Supabase.
- No ejecutar scripts SQL históricos sobre la base existente sin backup, comparación de esquema y aprobación del plan.
- No activar cobros reales hasta completar migraciones y pruebas de aceptación.
