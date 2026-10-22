-- Creado por Aldo Garcia.
-- Consultas de catalogo y generaciones activas: filtrar primero el alcance ACL.
-- No cambia documentos ni generaciones; conserva las migraciones anteriores.
CREATE INDEX ix_documents_corporate_rag
    ON documents (scope, category, status, deleted_at, index_fingerprint);
CREATE INDEX ix_documents_private_rag
    ON documents (scope, owner_user_id, conversation_id, status, deleted_at, index_fingerprint);
