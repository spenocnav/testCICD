# Sidecar de seguimiento de etiquetas

Este directorio contiene la integración versionada del worker recibido en
`seguimiento_etiquetas_produccion.zip`.

El ZIP y los datos generados no forman parte del código fuente. El sidecar debe:

- mantener su runtime en un volumen privado y persistente;
- consultar CloudFleet únicamente desde el worker;
- conservar el ledger por `(work_order_number, tracking_key)`;
- entregar al ingestor solo el contrato normalizado y acotado;
- no publicar `label_dashboard.json`, el ledger ni el catálogo al navegador;
- ejecutar el bootstrap histórico de forma separada y reanudable.

El adaptador de la aplicación persiste las órdenes en
`cloudfleet_work_orders` y las observaciones en
`cloudfleet_tracking_events`. Las consultas de negocio salen exclusivamente
por el API autenticado de Portal Clientes.
