# FASE 13 — Cartera y cobranza

Estado: **CERRADA**.

## Entregado
- Cartera activa por organización y priorización por días de mora.
- Vista PostgreSQL `managerws.v_portfolio` para consultas de cartera.
- Historial append-only de gestiones de cobranza.
- Asignación de créditos a gestores con historial de transferencias/desasignaciones.
- Unicidad real de una sola asignación activa por crédito mediante índice parcial PostgreSQL.
- Política de envejecimiento por organización (`collection_policy`) en lugar de umbrales rígidos en la interfaz.
- Filtros de cartera por gestor y créditos sin asignar.
- Permiso separado `collections.assign` para owner/admin/manager.
- CSRF y POST para mutaciones de asignación/desasignación.
- Auditoría de asignaciones y desasignaciones.
- Aislamiento por `organization_id` y FKs compuestas para impedir cruces entre organizaciones.
- Acceso `anon`/`authenticated` revocado sobre el esquema privado `managerws`.

## Regla importante
La tabla de asignaciones conserva histórico. Las filas inactivas pueden acumularse; solamente puede existir una fila `active=true` por `(organization_id, credit_id)`.

## No incluido todavía
- Automatización real de WhatsApp/SMS.
- Motor automático de promesas y recuperación.
- Rutas geográficas de cobradores.
- App Android del cobrador.
- Integración con proveedores de mensajería.

Estos elementos quedan para fases posteriores y no deben mezclarse con el cierre de esta fase.
