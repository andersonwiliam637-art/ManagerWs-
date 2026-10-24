# ManagerWS — checklist de publicación

> Estado: candidato corregido; no desplegado ni certificado para cobros reales. La configuración PayPal Live queda apagada por defecto.

## 1. Repositorio

- El repositorio compartido `andersonwiliam637-art/ManagerWs-` existe, pero actualmente está vacío.
- Extraer este ZIP y subir todos sus archivos y carpetas a la raíz de ese repositorio. No subir únicamente el ZIP ni agregar una carpeta contenedora adicional.
- Mantener el repositorio privado y confirmar que `.env`, bases SQLite, volcados, credenciales y claves privadas no estén en Git.
- Activar MFA en GitHub y restringir colaboradores.

## 2. Servicio Railway

- Crear un proyecto/servicio nuevo llamado `ManagerWS`; no reutilizar proyectos de otras aplicaciones.
- Conectar el repositorio privado y usar el `Dockerfile` de la raíz.
- Configurar dominio HTTPS público y `PUBLIC_BASE_URL` con ese mismo origen.
- Confirmar `GET /health` (proceso vivo) y `GET /ready` (conectividad SQL).

## 3. Variables obligatorias del backend

Configurar secretos directamente en Railway; no ponerlos en GitHub ni enviarlos por chat:

- `FLASK_ENV=production`
- `AUTO_CREATE_DB=false`
- `SECRET_KEY` — valor aleatorio fuerte y exclusivo
- `DATABASE_URL` — conexión **PostgreSQL directa** al proyecto correcto de Supabase; no usar service-role key ni URL REST
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`
- `REDIS_URL` — Redis gestionado para límites de solicitudes compartidos entre réplicas
- `PUBLIC_BASE_URL` — origen HTTPS público de ManagerWS

Si se necesita bootstrap de administrador, configurar `ADMIN_USERNAME`, `ADMIN_EMAIL` y `ADMIN_PASSWORD` antes del primer arranque y retirar `ADMIN_PASSWORD` después de confirmar la cuenta.

## 4. Esquema y migraciones

- Antes de desplegar, comparar modelos con tablas/columnas, claves, índices y constraints existentes bajo `managerws`.
- Hacer backup y revisar cada SQL antes de ejecutar. No usar `db.create_all()` en producción.
- `migrations/` aún no tiene `env.py` ni revisiones Alembic versionadas; no asumir que `flask db upgrade` migrará la base real.
- Confirmar que el usuario de PostgreSQL del backend solo tenga los permisos necesarios sobre el esquema privado `managerws`.

## 5. PayPal Live

Seguir `PAYPAL_LIVE_HANDOFF.md`. Configurar `PAYPAL_MODE=live`, moneda soportada y planes mensuales activos. Primero mantener `PAYPAL_ENABLED=false`. El checkout solo se debe habilitar cuando el backend y los webhooks estén listos.

La aplicación valida por API que el plan seleccionado está activo y que sus importes, moneda y periodicidad coinciden con la configuración. Eso no sustituye una transacción real de aceptación. Si se realiza esa comprobación en Live, será un cargo real, potencialmente con comisión.

## 6. Criterio de salida

No declarar producción lista hasta verificar en la instancia desplegada: registro/verificación/recuperación por SMTP, aislamiento entre dos organizaciones, operaciones financieras clave, límites de solicitud con Redis, backup/restauración, conexión al esquema correcto y un ciclo de suscripción y cancelación mediante webhooks firmados. Recurrente queda fuera de este release.
