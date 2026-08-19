-- Job 状态时间线：用于 Worker/BullMQ 故障恢复和用户侧进度审计。

BEGIN;

CREATE TABLE IF NOT EXISTS job_events (
  id text PRIMARY KEY,
  job_id text NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_status text,
  to_status text NOT NULL,
  event_type text NOT NULL,
  message text NOT NULL DEFAULT '',
  attempt integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, created_at DESC);

CREATE OR REPLACE FUNCTION studio_record_job_event() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO job_events(id, job_id, user_id, from_status, to_status, event_type, message, attempt, created_at)
    VALUES ('job_event_' || md5(random()::text || clock_timestamp()::text), NEW.id, NEW.user_id, NULL, NEW.status, 'created', COALESCE(NEW.error, ''), NEW.attempts, NEW.created_at);
  ELSIF OLD.status IS DISTINCT FROM NEW.status THEN
    INSERT INTO job_events(id, job_id, user_id, from_status, to_status, event_type, message, attempt, created_at)
    VALUES ('job_event_' || md5(random()::text || clock_timestamp()::text), NEW.id, NEW.user_id, OLD.status, NEW.status, 'status_change', COALESCE(NEW.error, ''), NEW.attempts, NEW.updated_at);
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_jobs_record_event ON jobs;
CREATE TRIGGER trg_jobs_record_event
AFTER INSERT OR UPDATE OF status ON jobs
FOR EACH ROW EXECUTE FUNCTION studio_record_job_event();

COMMIT;
