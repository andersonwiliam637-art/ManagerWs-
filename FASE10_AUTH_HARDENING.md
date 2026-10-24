# FASE 10 — Autenticación, sesiones y abuso

Cambios:
- Producción exige `REDIS_URL` para que el rate limiting sea compartido entre instancias.
- `login_required` valida que el usuario siga activo en cada petición protegida.
- Se elimina el rol de la sesión; el rol efectivo se obtiene desde `Membership` de la organización seleccionada.
- Login realiza verificación de hash incluso para usuarios inexistentes para reducir enumeración por timing.
- Al iniciar sesión se limpia la sesión anterior antes de establecer la identidad.
- Solo queda utilizable el último token de verificación de correo.
- Solo queda utilizable el último token de recuperación de contraseña.
- Al cambiar contraseña se revocan todos los tokens de recuperación pendientes.
- `require_permission()` deja de confiar en `session['role']` y consulta permisos del membership actual.
- Se mantienen cookies HttpOnly/SameSite y Secure en producción, expiración de sesión de 4 horas y CSRF.

Pendiente para fases posteriores:
- Integración con un almacén de sesión server-side si se requiere revocación inmediata de sesiones individuales.
- MFA/2FA para propietarios y administradores antes de producción, si el flujo de negocio lo requiere.
