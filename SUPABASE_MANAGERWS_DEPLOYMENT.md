# ManagerWS — conexión al esquema privado de Supabase

## Base de código

Este release se construye exclusivamente desde `managerws_phase14_billing_core.zip` (fase 14). El repositorio demo no forma parte de esta entrega.

## Configuración de producción

Variables obligatorias:

- `DATABASE_URL`: URI PostgreSQL del proyecto Supabase correspondiente a `xsxtcyibobbbekmllxjs`. Use el connection string del proyecto, preferentemente el pooler recomendado por Supabase para el entorno de despliegue.
- `FLASK_ENV=production` (o la variable de producción equivalente que utiliza el despliegue).
- `AUTO_CREATE_DB=false`.
- `SECRET_KEY`: secreto aleatorio fuerte.
- `SMTP_HOST` y las credenciales SMTP requeridas para verificación y recuperación de cuenta.
- `REDIS_URL`: Redis compartido para rate limiting entre instancias.

El motor PostgreSQL configura `search_path=managerws,public`, por lo que los modelos ORM que usan nombres de tabla sin calificar consultan primero el esquema privado `managerws`. No configure una URL de otra base de datos ni exponga el esquema privado al cliente.

## Migraciones

No ejecutar `db.create_all()` contra producción. Las migraciones existentes deben revisarse y aplicarse en orden controlado al esquema `managerws`. No aplicar migraciones automáticamente al arrancar la aplicación. Antes de migrar, hacer backup y verificar el SQL exacto contra el esquema del proyecto.

## Seguridad de acceso

- Mantener `managerws` privado y sin acceso directo para `anon` y `authenticated` mientras la aplicación use el backend Flask con credenciales de PostgreSQL.
- Nunca incluir `DATABASE_URL`, contraseña PostgreSQL ni `SECRET_KEY` en el repositorio, ZIP público, frontend, logs o conversación.
- Probar permisos y aislamiento por organización con dos organizaciones de prueba; no usar datos reales de clientes para las pruebas.
- Habilitar protección de contraseñas comprometidas en Supabase Auth si Supabase Auth se utiliza en el proyecto. La aplicación Flask mantiene su propio flujo de autenticación; no asumir que configurar una opción de Supabase Auth protege automáticamente las contraseñas de Flask.

## Verificación realizada para este paquete

- `python -m py_compile app.py financial.py`: aprobado.
- `python -m pytest -q`: 30 pruebas aprobadas y 9 subtests aprobados.
- Estas pruebas son locales. No equivalen a una prueba de conexión real a Supabase ni a una prueba de extremo a extremo de pagos.

## Puerta de despliegue pendiente

Antes de producción: confirmar la URL de conexión en el entorno privado del despliegue, ejecutar una consulta de solo lectura que confirme `current_schema()`/`current_schemas(true)`, comprobar el mapeo de columnas de todos los modelos, y ejecutar pruebas CRUD y tenant-isolation sobre un entorno de staging con dos organizaciones. PayPal y Recurrente quedan para la etapa final de pruebas de pago.
