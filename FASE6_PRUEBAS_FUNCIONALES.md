# ManagerW.S — Fase 6: pruebas funcionales del motor de prestamistas

## Objetivo
Validar el comportamiento financiero completo del motor a nivel de reglas de negocio y realizar una revisión estática de las rutas Flask críticas, antes de pasar a pruebas reales contra PostgreSQL/HTTP.

## Cambios realizados
- Liquidación corregida: ahora descuenta las moras ya pagadas; antes podía inflar el saldo de liquidación.
- Mora/cobranza: la clasificación `overdue` respeta `grace_days` de forma consistente.
- Vistas GET de crédito, liquidación, reportes y cobranza: dejaron de ejecutar `db.session.commit()`; la consulta no persiste cambios financieros por accidente.
- Asignación de pagos: el estado de cuotas usa también el período de gracia al decidir `overdue`.
- Idempotencia de pagos: además del `UNIQUE (organization_id, idempotency_key)`, se maneja la colisión de unicidad con `IntegrityError` y se recupera el recibo existente tras el rollback.
- README alineado con la implementación: las mensualidades usan calendario real y la mora automática sí está implementada en el motor actual.

## Pruebas ejecutadas
Se ejecutaron **30 pruebas, 30 OK**.

Cobertura principal:
- amortización y préstamo de interés simple/flat;
- interés cero;
- semanal, quincenal y mensual;
- meses con 28/29/30/31 días y cambio de año;
- redondeo a centavos y reconciliación exacta del principal;
- mora fija, diaria porcentual, por cuota y con tope;
- período de gracia;
- pago parcial;
- FIFO entre múltiples cuotas;
- orden de aplicación mora → interés → principal;
- rechazo de sobrepago;
- liquidación después de pagos parciales;
- liquidación correcta después de pagar mora parcialmente;
- escenario de refinanciamiento con nuevo calendario;
- reversión de saldo en el escenario de prueba;
- ausencia de `db.Float` en campos financieros;
- operaciones financieras críticas restringidas a POST;
- comprobación estática de alcance por organización en pagos;
- ciclo solicitud → aprobación/rechazo → desembolso protegido por estados;
- rutas GET financieras sin `commit`.

## Verificación técnica adicional
- `py_compile` de `app.py` y `financial.py`: **OK**.
- Revisión estática de rutas financieras críticas: **OK**.
- No se detectaron credenciales de administrador por defecto conocidas en el código revisado.

## Limitación actual
Esta fase **no constituye todavía una prueba de integración real con PostgreSQL ni de HTTP/Flask en ejecución**. En el entorno usado para la revisión no están instaladas todas las dependencias de Flask de la aplicación y no hay un servidor PostgreSQL disponible para ejecutar migraciones, concurrencia real, transacciones y bloqueo `FOR UPDATE`.

Por tanto, los resultados demuestran que las reglas financieras puras y las protecciones estáticas verificadas pasan, pero todavía falta el ensayo sobre una base PostgreSQL real antes de afirmar que el motor está listo para producción.

## Siguiente etapa técnica
La siguiente etapa debe ser la **integración real con PostgreSQL + pruebas transaccionales**, incluyendo concurrencia de pagos, idempotencia simultánea, reversión de efectivo, migraciones, índices, aislamiento y recorrido HTTP completo de prestamista.
