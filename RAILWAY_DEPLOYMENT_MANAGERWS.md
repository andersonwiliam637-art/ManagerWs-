# ManagerWS — despliegue de staging en Railway

## Estado
Configuración preparada; **no desplegada**. No conectar cobros reales ni datos reales hasta completar la puerta de validación.

## Requisitos previos
- Repositorio privado `andersonwiliam637-art/ManagerWs-` con el contenido de este paquete en la raíz y conectado a la cuenta Railway. El repositorio estaba vacío al inspeccionarlo; Railway no debe desplegarse todavía.
- Proyecto Railway nuevo llamado `ManagerWS` (no reutilizar `callbox-ai` ni `saas-predictor-conversion`).
- Proyecto Supabase existente `xsxtcyibobbbekmllxjs`; usar su conexión PostgreSQL privada/pooler apropiado.
- Servicio Redis accesible para el rate limiter distribuido.
- Proveedor SMTP y credenciales para verificación y recuperación de cuentas.

## Variables en Railway → Variables
Configurar valores en el panel de Railway, no en el repositorio ni en el chat:
- `FLASK_ENV=production`
- `AUTO_CREATE_DB=false`
- `DATABASE_URL` = conexión PostgreSQL de Supabase al proyecto correcto; no usar service-role key como contraseña ni URL REST.
- `SECRET_KEY` = secreto aleatorio largo generado para este entorno.
- `REDIS_URL` = URL privada del Redis de staging.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, y `SMTP_FROM` según el proveedor.
- `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` solo si se necesita bootstrap inicial; configurar antes del primer arranque y retirar `ADMIN_PASSWORD` después de confirmar el alta del administrador.

PayPal queda apagado por defecto. Para el flujo Live controlado, sigue `PAYPAL_LIVE_HANDOFF.md`. No expongas el checkout mientras `PAYPAL_ENABLED=false`; cualquier prueba transaccional en Live supone un cargo real.

## Puerta de despliegue
1. Revisar/aplicar las migraciones SQL de forma controlada; no activar `AUTO_CREATE_DB` en PostgreSQL.
2. Confirmar en logs que el proceso inicia sin errores.
3. Abrir `/health`; solo confirma que el proceso HTTP responde, no que la base de datos esté lista.
4. Ejecutar desde un procedimiento privado una comprobación de solo lectura de `current_schema()` y `current_schemas(true)` y confirmar que los modelos resuelven a `managerws`.
5. Ejecutar pruebas de autenticación, aislamiento entre dos organizaciones de prueba, operaciones financieras y recuperación de contraseña.
6. Revisar los webhooks PayPal/Recurrente: validar firma antes de confiar en el evento, idempotencia, reintentos de eventos fallidos y errores de persistencia. No activar pagos reales hasta verificarlo.

## Costos y seguridad
No se crea ningún proyecto, Redis ni recurso facturable automáticamente con este archivo. Verificar precios del plan y recursos antes de crear servicios. Mantener el repositorio privado y no subir `.env`, dumps de producción, contraseñas, claves API ni tokens.
