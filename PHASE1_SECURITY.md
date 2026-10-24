# Fase 1 — Checklist de seguridad y arquitectura

## Corregido en esta versión

- [x] Acciones administrativas de cambio de estado pasan a POST + CSRF.
- [x] No existe contraseña administrativa de fallback en producción.
- [x] `SECRET_KEY` y `DATABASE_URL` son obligatorias en producción.
- [x] SMTP es obligatorio en producción para no crear cuentas imposibles de verificar/recuperar.
- [x] Passwords nuevas usan PBKDF2-SHA256 con 600.000 iteraciones.
- [x] Recuperación de contraseña con token firmado, hash almacenado, expiración y un solo uso.
- [x] Verificación de correo con token firmado, hash almacenado, expiración y un solo uso.
- [x] Respuesta de recuperación no permite enumerar cuentas.
- [x] Dinero usa `NUMERIC(14,2)`.
- [x] Pagos no tienen endpoint de borrado; existe estado de reversión.
- [x] Auditoría de login, registro, cambios de plan, pagos y administración.
- [x] Organización + membresías aíslan los datos por tenant.
- [x] Control de acceso administrativo no confunde `owner` de cliente con `admin` del sistema.
- [x] CSP/HSTS/Permissions-Policy/nosniff/Referrer-Policy en producción.
- [x] Páginas privadas no se cachean mediante el Service Worker.
- [x] País detectado no se utiliza como mecanismo de autorización.
- [x] Registro de pago utiliza lock de fila en PostgreSQL.

## Pendiente deliberadamente para fases siguientes

- [ ] Alembic/Flask-Migrate con migraciones versionadas generadas contra el esquema inicial.
- [ ] Billing recurrente real + webhooks del proveedor.
- [ ] Ledger financiero completo y motor de cuotas.
- [ ] RBAC aplicado a cada operación de negocio, no solo catálogo de roles.
- [ ] 2FA para administradores.
- [ ] Gestión de sesiones/dispositivos y revocación.
- [ ] WAF/Reverse proxy/TLS operativo.
- [ ] SAST/dependency scanning/secret scanning en CI.
- [ ] Pruebas automatizadas de autorización multi-tenant.
- [ ] Pruebas de concurrencia para pagos.
- [ ] Pruebas de penetración antes de producción.
