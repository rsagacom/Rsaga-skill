-- 来源文档导入幂等：重试不得重复创建来源、分段和改编单元。
BEGIN;

ALTER TABLE source_documents
  ADD COLUMN IF NOT EXISTS idempotency_key text;

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_documents_project_idempotency
  ON source_documents(project_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMIT;
