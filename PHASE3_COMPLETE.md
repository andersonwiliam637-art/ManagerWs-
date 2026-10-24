# ManagerW.S — Motor completo de prestamistas (Fase 3 completada)

## Incluido
- Calendario determinista semanal, quincenal y mensual.
- Interés simple/flat y amortización sobre saldo.
- Redondeo monetario con Decimal a centavos.
- Mora configurable por crédito: ninguna, fija, porcentaje diario o porcentaje sobre cuota, con días de gracia y tope.
- Asignación de pagos por orden configurable: mora → interés → capital por defecto.
- Pagos parciales y liquidación total mediante el mismo motor.
- Idempotencia de pagos y recibo único.
- Reversión auditable sin borrar el pago original.
- Bloqueo de crédito/cuotas para reducir riesgo de doble aplicación concurrente.
- Caja por cobrador: apertura, movimientos de efectivo y cierre con diferencia.
- Refinanciamiento como nuevo crédito vinculado al anterior; el historial viejo permanece.
- Reporte básico de cartera y consulta de liquidación.
- Aislamiento por organización y permisos existentes.
- Migración PostgreSQL en `migrations_phase3_complete.sql`.
- Pruebas unitarias del motor financiero: 7 casos.

## Decisiones predeterminadas
- Moneda de la organización: la configurada en su organización (Guatemala: GTQ/Q por defecto).
- Frecuencias: semanal, quincenal y mensual.
- Método disponible: simple/flat o amortizado.
- Orden de aplicación: mora, interés, capital.
- Mora predeterminada: ninguna hasta que el producto de crédito la configure. Esto evita cobrar cargos no autorizados.
- Caja: disponible para cobradores; los pagos en efectivo exigen caja abierta.
- Refinanciamiento: conserva el crédito anterior como `restructured` y crea un crédito nuevo.

## Importante antes de producción
1. Ejecutar la migración en PostgreSQL.
2. Ejecutar pruebas de integración contra PostgreSQL real, incluyendo concurrencia.
3. Validar contratos de tasas, mora, recibos y cobranza con las reglas comerciales y legales aplicables al negocio.
4. No usar `AUTO_CREATE_DB=true` en producción; usar migraciones controladas.
5. Configurar secretos y credenciales fuera del código.
