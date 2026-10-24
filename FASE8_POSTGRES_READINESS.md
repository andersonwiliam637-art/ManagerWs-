# Fase 8 — PostgreSQL readiness / hardening

## Objetivo

La Fase 8 prepara el motor para la primera prueba real contra PostgreSQL y protege tres fuentes que deben ser históricas: `ledger_entry`, `payment_allocation` y `audit_log`.

## Cambios

- índices de consulta por organización y fecha;
- registros financieros/auditoría append-only mediante triggers PostgreSQL;
- las correcciones financieras deben hacerse mediante reversión/ajuste, no editando el historial;
- se conserva el aislamiento por `organization_id` de la aplicación;
- se mantiene idempotencia de pagos y bloqueo de crédito en la aplicación.

## Respaldo

Antes de modificar esta fase se creó una copia de la Fase 7:
`managerws_phase7_multitenancy.BACKUP_2026-09-19.zip`

Su SHA-256 está en `phase7_backup.sha256`.

## Verificación realizada en este entorno

- Suite existente: **30 pruebas + 9 subtests: OK**.
- `py_compile app.py financial.py`: **OK**.
- Revisión estática de transacciones, aislamiento y rutas financieras: realizada.

## Limitación crítica

Este entorno no tiene Flask/SQLAlchemy/psycopg2 instalados y no dispone de un servidor PostgreSQL accesible. Por tanto, **no se afirma que una transacción PostgreSQL real haya sido ejecutada todavía**.

La siguiente validación debe ejecutarse en staging con PostgreSQL real y debe comprobar:

1. dos pagos simultáneos sobre el mismo crédito;
2. dos solicitudes con la misma `idempotency_key`;
3. rollback completo cuando falla la caja después de crear el pago;
4. reversión concurrente de un mismo pago;
5. aislamiento entre dos organizaciones;
6. aplicación de todas las migraciones desde una base limpia;
7. restauración desde backup;
8. funcionamiento de los triggers de inmutabilidad.

No se considera producción hasta superar esas pruebas.
