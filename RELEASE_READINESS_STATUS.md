# ManagerWS — estado de preparación de release

**Dictamen: CANDIDATO DE STAGING; no desplegado y no certificado para cobros reales.**

El repositorio proporcionado `andersonwiliam637-art/ManagerWs-` existe pero está vacío en GitHub al último intento de lectura. El contenido de este paquete aún debe subirse a su raíz antes de conectar Railway.

## Cambios hechos en este candidato corregido

- Corregido el mapeo ORM `Subscription.provider_plan_id`.
- Añadidas comprobaciones `/health` y `/ready`.
- Checkout PayPal apagado por defecto (`PAYPAL_ENABLED=false`).
- Separados precio y moneda PayPal de los precios localizados de la ficha.
- Validación server-to-server del plan PayPal antes del checkout: estado `ACTIVE`, frecuencia mensual, moneda e importe exactos contra las variables configuradas.
- Bloqueo preventivo de checkouts consecutivos pendientes de la misma organización y prevención de crear otra suscripción PayPal cuando ya hay una activa/en cobro.
- Tratamiento más explícito de duplicados en webhooks y control de eventos de suscripción repetidos.
- Actualizados `.env.example`, checklist, instrucciones Railway y `PAYPAL_LIVE_HANDOFF.md`.

## Verificaciones realizadas localmente

- `python -m py_compile app.py financial.py`: correcto.
- `python -m pytest -q`: 39 pruebas aprobadas y 9 subtests aprobados en el entorno disponible.
- Lectura de solo lectura al esquema Supabase: existen 24 tablas en `managerws`, incluidas las tablas de facturación y las columnas de `subscription` esperadas por el checkout (`provider_plan_id`, `trial_end`, `grace_period_end`, etc.). Esto no sustituye una conexión ejecutada desde Flask ni una comparación completa de todo el ORM.
- No se pudo instalar dependencias adicionales por falta de acceso de red desde el entorno (Flask no está instalado aquí); por ello, no se probó import/arranque completo ni integración HTTP de Flask.

## Bloqueos que siguen requiriendo acceso/configuración externa

1. El repositorio GitHub proporcionado está vacío y no se ha sincronizado con el ZIP.
2. No se desplegó el servicio en Railway.
3. No se conectó Flask a PostgreSQL real ni se compararon por completo los modelos con el esquema actual.
4. `migrations/` no tiene `env.py` ni revisiones Alembic versionadas; no ejecutar `flask db upgrade` esperando una migración existente.
5. SMTP y Redis deben configurarse y probarse en Railway.
6. No se comprobaron las credenciales, los planes ni el webhook PayPal Live de la cuenta del usuario.
7. No se realizó transacción real de aceptación. Sin Sandbox, cualquier prueba de cargo en Live será real.
8. Recurrente no está conectado al flujo de facturación Flask y no debe activarse con las Edge Functions actuales.

## Puerta de publicación

- Subir el contenido del candidato al repositorio privado existente (actualmente vacío) y conectar ese repositorio a Railway.
- Comparar esquema y migraciones con Supabase antes de cambiar la BD; backup verificado.
- Configurar Railway con PostgreSQL directo, SMTP, Redis y HTTPS.
- Crear app, producto, planes mensuales y webhook en PayPal Live; introducir secretos directamente en Railway.
- Mantener `PAYPAL_ENABLED=false` hasta que se complete una ventana controlada de verificación.
- Habilitar cobros solo después de revisar que el plan activo de PayPal coincide con el precio mostrado y confirmar el flujo de webhook real.
