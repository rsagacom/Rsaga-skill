-- Job 状态迁移的数据库级保护；应用层和直接 SQL 写入都必须遵守同一合同。
BEGIN;

CREATE OR REPLACE FUNCTION studio_validate_job_status_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IS NOT DISTINCT FROM NEW.status THEN
    RETURN NEW;
  END IF;
  IF (
    (OLD.status = 'pending' AND NEW.status IN ('queued', 'cancelled'))
    OR (OLD.status = 'queued' AND NEW.status IN ('running', 'cancelled'))
    OR (OLD.status = 'queued' AND NEW.status = 'failed' AND NEW.error = 'insufficient credits')
    OR (OLD.status = 'running' AND NEW.status IN ('review', 'completed', 'failed', 'cancelled'))
    OR (OLD.status = 'running' AND NEW.status = 'queued' AND NEW.error = 'worker lease expired; requeued')
    OR (OLD.status = 'review' AND NEW.status IN ('completed', 'failed', 'cancelled'))
    OR (OLD.status = 'failed' AND NEW.status IN ('queued', 'cancelled'))
  ) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'invalid job status transition: % -> %', OLD.status, NEW.status
    USING ERRCODE = 'check_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_jobs_validate_status ON jobs;
CREATE TRIGGER trg_jobs_validate_status
BEFORE UPDATE OF status ON jobs
FOR EACH ROW
EXECUTE FUNCTION studio_validate_job_status_transition();

COMMIT;
