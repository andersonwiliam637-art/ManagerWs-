# Fase 14 — facturación recurrente

## Integración seleccionada

- Backend único de cobros: Flask en Railway.
- Base de datos: conexión PostgreSQL privada de Supabase al esquema `managerws`.
- Proveedor: PayPal Subscriptions API y webhook firmado.
- Las Edge Functions antiguas no forman parte del flujo de facturación de esta versión.

## Controles incorporados

- Checkout cerrado por defecto (`PAYPAL_ENABLED=false`).
- No se derivan moneda/precio PayPal de país ni del precio local en la pantalla.
- Plan ID, moneda e importe se configuran por plan, y se muestran explícitamente al cliente.
- Antes de crear suscripción, el backend consulta PayPal y exige plan activo, ciclo regular mensual, moneda coincidente e importe coincidente.
- Evita un segundo checkout cuando la organización ya tiene una suscripción PayPal activa/en estado de cobro, y bloquea intentos pendientes consecutivos durante 15 minutos.
- El retorno del navegador no activa la cuenta; la activación depende del webhook verificado.
- Webhooks tienen registro de eventos y tratamiento de duplicados; no se almacenan datos de tarjetas.

## Variables de configuración

- `PAYPAL_MODE=live`
- `PAYPAL_ENABLED=false` hasta el momento de la verificación controlada
- `PAYPAL_CURRENCY=USD` para la primera activación
- `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`
- `PAYPAL_PLAN_PRO_USD`, `PAYPAL_PLAN_BUSINESS_USD`, `PAYPAL_PLAN_ENTERPRISE_USD`
- `PAYPAL_PRICE_PRO_USD`, `PAYPAL_PRICE_BUSINESS_USD`, `PAYPAL_PRICE_ENTERPRISE_USD`
- `BILLING_GRACE_DAYS=3`

No compartir Client Secret, claves DB ni otros secretos en el chat o en Git. La moneda y los importes deben coincidir con los planes reales de PayPal. La lista oficial de monedas no incluye GTQ, HNL, NIO ni CRC; no crear variables de plan en esas monedas esperando que PayPal Subscription las acepte.

## Alcance no certificado

Las pruebas locales no demuestran conectividad PostgreSQL remota, despliegue, ni cobros Live. No se dispone de Sandbox para este usuario; cualquier comprobación transaccional posterior en Live será un cargo real. La estructura de migraciones Alembic tampoco está finalizada. Recurrente no está conectado al flujo Flask en este release.
