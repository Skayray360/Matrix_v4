-- Creado por Aldo Garcia.
-- No inferir fechas/duraciones de operaciones historicas ya terminadas.
-- Los timestamps futuros preservan microsegundos; duraciones en milisegundos.
ALTER TABLE chat_operations MODIFY COLUMN created_at DATETIME(6) NOT NULL;
ALTER TABLE chat_operations MODIFY COLUMN started_at DATETIME(6) NULL;
ALTER TABLE chat_operations ADD COLUMN finished_at DATETIME(6) NULL;
ALTER TABLE chat_operations ADD COLUMN queue_ms BIGINT NULL;
ALTER TABLE chat_operations ADD COLUMN execution_ms BIGINT NULL;
ALTER TABLE chat_operations ADD COLUMN total_ms BIGINT NULL;
CREATE INDEX ix_chat_operations_finished ON chat_operations (finished_at);
