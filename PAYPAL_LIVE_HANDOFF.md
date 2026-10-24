# ManagerWS — conexión de PayPal Live

## Estado y decisión de diseño

ManagerWS usa **Flask en Railway → conexión PostgreSQL privada de Supabase → API de suscripciones PayPal**. La facturación de este paquete no depende de Supabase Edge Functions ni de permisos PostgREST para el esquema `managerws`.

El checkout PayPal queda **deshabilitado por defecto** (`PAYPAL_ENABLED=false`). El precio de PayPal se configura aparte del precio local que aparece en la ficha del plan y se muestra explícitamente al usuario. No se convierte automáticamente un precio de moneda local a USD.

La documentación oficial de monedas PayPal no enumera GTQ, HNL, NIO ni CRC como monedas REST admitidas. Para la primera activación se recomienda usar USD en las suscripciones de PayPal. PayPal exige un producto y un plan de facturación por moneda; cada plan seleccionado debe ser mensual y coincidir exactamente con el precio configurado. Fuente: https://developer.paypal.com/api/codes/currency/ y https://developer.paypal.com/platforms/subscriptions/integrate/.

## Lo que debes crear en PayPal

Haz estos pasos en **PayPal Developer Dashboard → Live**, no en Sandbox:

1. Crea una aplicación REST para la cuenta Business que recibirá los pagos. Copia el **Client ID** y el **Secret**.
2. Crea un producto de servicio para ManagerWS.
3. Crea tres planes activos de cobro recurrente mensual en **USD**: Pro, Business y Enterprise. Decide el precio comercial definitivo antes de activarlos. El importe de cada plan debe coincidir con las variables `PAYPAL_PRICE_*_USD` y con lo que ve el cliente en la web.
4. Cuando tengas el dominio público de Railway, agrega un webhook a esa aplicación con esta URL exacta: `https://TU-DOMINIO-PUBLICO/webhooks/paypal`.
5. Suscríbelo a estos eventos:
   - `BILLING.SUBSCRIPTION.ACTIVATED`
   - `BILLING.SUBSCRIPTION.UPDATED`
   - `BILLING.SUBSCRIPTION.PAYMENT.FAILED`
   - `BILLING.SUBSCRIPTION.SUSPENDED`
   - `BILLING.SUBSCRIPTION.CANCELLED`
   - `BILLING.SUBSCRIPTION.EXPIRED`
   - `PAYMENT.SALE.COMPLETED`
   - `PAYMENT.SALE.REFUNDED`
   - `PAYMENT.SALE.REVERSED`
6. Guarda el **Webhook ID** que entrega PayPal. La URL del webhook debe apuntar al backend público y HTTPS, no a una ruta local.

## Datos que se pueden compartir y datos secretos

Puedes compartir en esta conversación los **tres Plan IDs**, el **Webhook ID**, la moneda y los precios elegidos. No compartas aquí el PayPal Client Secret, la contraseña del usuario, `DATABASE_URL`, `SECRET_KEY`, ni ninguna contraseña o token.

Introduce directamente en Railway → servicio ManagerWS → Variables:

- `PAYPAL_MODE=live`
- `PAYPAL_ENABLED=false` al preparar la configuración; se cambia a `true` solo en el momento acordado de verificación controlada
- `PAYPAL_CURRENCY=USD`
- `PAYPAL_CLIENT_ID` = Client ID de la app Live
- `PAYPAL_CLIENT_SECRET` = Secret de la app Live (solo en Railway)
- `PAYPAL_WEBHOOK_ID` = Webhook ID Live
- `PAYPAL_PLAN_PRO_USD`, `PAYPAL_PLAN_BUSINESS_USD`, `PAYPAL_PLAN_ENTERPRISE_USD` = Plan IDs Live
- `PAYPAL_PRICE_PRO_USD`, `PAYPAL_PRICE_BUSINESS_USD`, `PAYPAL_PRICE_ENTERPRISE_USD` = importes mensuales exactos que configuraste en esos planes

No cargues credenciales Sandbox en Live ni IDs de planes de otro entorno. PayPal separa sus recursos entre entornos.

## Verificación Live sin Sandbox

Como solo tienes Live, no hay forma de declarar una prueba de pagos sin transacciones reales. Primero deja `PAYPAL_ENABLED=false`, despliega y comprueba `/health` y `/ready`, autenticación, email, conexión a DB y esquema. Después introduce los datos de PayPal y confirma que la aplicación valida los planes activos, moneda y precio esperado sin habilitar el checkout al público.

Cuando estés listo, activa `PAYPAL_ENABLED=true` durante una ventana controlada. Haz una única suscripción de importe bajo con un medio de pago legítimo que controles; será un cargo real y PayPal puede aplicar comisiones. Confirma: redirección de aprobación, llegada de webhook firmado, fila de suscripción activa, transacción de pago registrada y acceso al plan correcto. Después cancela desde PayPal y verifica que la cancelación llegue por webhook. No uses tarjetas falsas, pagos no autorizados ni datos de terceros.

Si no puedes asumir un cargo real, no habilites el checkout para clientes todavía; la integración no debe declararse probada solo por compilar o por abrir la página.

## Condiciones previas no resueltas en este ZIP

- El repositorio que compartiste (`andersonwiliam637-art/ManagerWs-`) existe, pero GitHub lo reporta vacío. Antes de desplegar, sube el contenido extraído de este ZIP a la raíz de ese repositorio; no basta con subir el ZIP como un único archivo.
- La conexión PostgreSQL directa de Railway debe comprobarse contra el esquema actual `managerws`. No usar `service_role` como contraseña PostgreSQL ni la URL REST de Supabase.
- `migrations/` todavía no contiene revisiones Alembic versionadas. No ejecutar `flask db upgrade` esperando una migración que no existe, y no activar `AUTO_CREATE_DB` en producción.
- SMTP y Redis son requisitos de producción para esta app.
- Recurrente no está conectado al flujo de login y cobro de Flask en este release. Mantenerlo deshabilitado para no mezclar dos backends de facturación.

El código intenta fallar cerrado: un checkout no inicia si PayPal está apagado, faltan IDs/precios/credenciales/webhook, el plan no está `ACTIVE`, o la moneda/importe/frecuencia no coincide con lo configurado.
