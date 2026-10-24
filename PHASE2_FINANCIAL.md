# ManagerW.S — Fase 2: Motor financiero

## Objetivo

Convertir el registro simple de créditos en una cartera con contrato financiero, calendario de cuotas, aplicación FIFO de pagos, reversión auditable y ledger.

## Implementado

- `Credit` ahora conserva principal, total contractual, tasa anual, método (`simple` o `amortized`), número de cuotas, frecuencia y fechas.
- `LoanInstallment` guarda cada cuota individual.
- `PaymentAllocation` indica exactamente qué cuota recibió cada pago.
- Los pagos se aplican FIFO a las cuotas más antiguas.
- `LedgerEntry` deja una huella financiera de desembolso, pago y reversión.
- Los pagos no se borran: se revierten.
- PostgreSQL usa `SELECT ... FOR UPDATE` para evitar doble aplicación concurrente sobre el mismo crédito/cuotas.
- Los saldos monetarios usan `NUMERIC(14,2)`.
- Las cuotas vencidas se marcan al consultar la cartera.
- Roles limitan quién puede registrar/revertir operaciones.
- Se agregó detalle del crédito y su calendario.

## Convenciones actuales

- Tasa ingresada como porcentaje anual. Ejemplo: `24` = 24% anual.
- `simple`: interés prorrateado por períodos y capital dividido en cuotas.
- `amortized`: cuota amortizada con tasa periódica.
- `monthly` usa períodos contables de 30 días en esta fase. Un motor de fechas calendario real se puede introducir después sin cambiar el modelo.
- No se aplican cargos de mora automáticamente todavía; existe el campo para incorporarlos en la siguiente iteración financiera.

## Importante

Esto todavía no es un sistema contable completo ni sustituye las obligaciones legales/fiscales de cada país. Antes de cobrar dinero real se deben completar pruebas, conciliación, reglas de mora, permisos, facturación y cumplimiento.
