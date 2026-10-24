# ManagerW.S — Fase 7: aislamiento multiempresa y transacciones de lectura

## Objetivo
Endurecer el límite entre organizaciones antes de la integración real con PostgreSQL.

## Cambios
- Se agregó selección explícita de organización en sesión mediante POST.
- La organización seleccionada solo se acepta si existe una membresía activa del usuario.
- Una organización inválida o antigua en sesión se descarta; nunca se usa para autorizar acceso.
- Las consultas financieras críticas mantienen filtro por `organization_id`.
- Las vistas GET de cobranza, crédito, liquidación y reportes no modifican ni hacen `commit` de objetos financieros.
- Se conservaron las restricciones UNIQUE por organización para idempotencia de pagos y vínculo solicitud→crédito.

## Pruebas
- 8 pruebas estáticas de aislamiento/rutas: OK.
- Suite financiera de Fase 6: 30/30 OK.
- `py_compile app.py financial.py`: OK.

## Pendiente antes de producción
No se puede declarar integración completa sin PostgreSQL real. Falta ejecutar:
- migraciones contra PostgreSQL;
- pruebas HTTP con Flask;
- concurrencia real de dos pagos sobre el mismo crédito;
- colisión simultánea de la misma idempotency key;
- validación de aislamiento de transacciones y `FOR UPDATE`;
- prueba de recuperación ante rollback/crash;
- pruebas de índices y planes de consulta.
