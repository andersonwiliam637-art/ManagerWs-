# FASE 9 — Integridad de aislamiento SaaS

## Cambios
- La suscripción se resuelve por `organization_id`, no por usuario individual.
- Panel administrativo y solicitudes de pago quedan limitados a la organización activa.
- Cambios de plan afectan la suscripción de la organización, no un flag personal del usuario.
- Se agregaron claves foráneas compuestas que obligan a que relaciones financieras y operativas pertenezcan a la misma organización.
- Usuarios asignados a clientes, créditos, cobradores, caja, solicitudes y suscripciones deben pertenecer a la organización mediante `membership`.
- Aplicaciones, créditos, cuotas, pagos, asignaciones, ledger y caja ya no pueden enlazarse a un registro de otra organización a nivel de PostgreSQL.

## Verificación
- `py_compile` de `app.py`: OK.
- Prueba de aislamiento PostgreSQL: intento de insertar un cliente de organización B usando un usuario de organización A fue rechazado por `fk_client_org_user`.
- Datos de prueba eliminados: 0 organizaciones de prueba restantes.

## Nota RLS
`managerws` actualmente no concede `USAGE` ni privilegios de tabla a `anon`/`authenticated`; el backend es el camino previsto. No se habilitó RLS automáticamente porque hacerlo sin políticas podría bloquear el backend. Antes de producción se debe decidir explícitamente si el acceso será exclusivamente backend o si habrá acceso directo desde clientes Supabase; si este último se usa, habrá que habilitar RLS con políticas por organización.
