# ManagerWS — primer paso para publicar

El repositorio compartido `andersonwiliam637-art/ManagerWs-` existe, pero GitHub lo reporta vacío. Este ZIP es el candidato corregido; el repositorio no contiene aún este código y Railway no debe conectarse a él hasta que se suban los archivos.

## Subida correcta

1. Descarga `ManagerWS_RELEASE_CANDIDATE_LIVE.zip` y extráelo en una carpeta.
2. Sube **todo el contenido extraído** a la raíz de `https://github.com/andersonwiliam637-art/ManagerWs-` (incluidos `app.py`, `financial.py`, `Dockerfile`, `railway.json`, carpetas `templates/`, `static/`, `tests/`, `.github/` y archivos de configuración). No subas solo el ZIP como un archivo.
3. Mantén el repositorio privado. No subas `.env`, secretos, respaldos ni bases de datos con datos reales. `.env.example` es una plantilla sin credenciales.
4. Espera que el workflow `ManagerWS checks` termine; debe instalar dependencias, compilar el código y ejecutar la suite de pruebas.
5. Después conecta este repositorio a un servicio Railway nuevo de ManagerWS y configura PostgreSQL directo de Supabase, Redis, SMTP y el dominio HTTPS.

## PayPal Live

Mantén `PAYPAL_ENABLED=false`. Cuando Railway tenga un dominio HTTPS estable, crea el webhook Live con la URL `https://TU-DOMINIO/webhooks/paypal`. Introduce Client Secret y el resto de secretos directamente en Railway, nunca en GitHub ni en este chat.

Este candidato no está desplegado y no se ha probado una transacción Live. Las pruebas de pago en Live generan cargos reales.
