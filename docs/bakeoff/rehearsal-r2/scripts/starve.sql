\pset footer off
-- p3 jobs created during the import burst (concurrent writes) vs the p6 import backlog
WITH w AS (SELECT to_timestamp(1790216297.0) AS t0, to_timestamp(1790216600.0) AS t1),
p3 AS (SELECT j.* FROM jobs j, w WHERE j.kind='librarian_write' AND j.priority=3 AND j.created_at BETWEEN w.t0 AND w.t1)
SELECT p3.job_id, to_char(p3.created_at, 'HH24:MI:SS') AS created, to_char(p3.done_at, 'HH24:MI:SS') AS done, p3.status,
       round(extract(epoch FROM p3.done_at - p3.created_at)::numeric, 1) AS wait_s,
       (SELECT count(*) FROM jobs q WHERE q.kind='librarian_write' AND q.priority=6 AND q.done_at > p3.created_at AND q.done_at < p3.done_at) AS p6_done_meanwhile,
       (SELECT count(*) FROM jobs q WHERE q.kind='librarian_write' AND q.priority=6 AND q.created_at < p3.created_at AND (q.done_at IS NULL OR q.done_at > p3.done_at)) AS p6_still_waiting_at_p3_done
  FROM p3 ORDER BY p3.job_id;
SELECT priority, status, count(*), to_char(min(created_at),'HH24:MI:SS') AS first_created, to_char(max(done_at),'HH24:MI:SS') AS last_done
  FROM jobs WHERE kind='librarian_write' AND created_at > to_timestamp(1790216297.0) GROUP BY 1,2 ORDER BY 1,2;
SELECT kind, priority, count(*), to_char(max(done_at),'HH24:MI:SS') AS last_done, round(extract(epoch FROM max(done_at))::numeric - 1790216338.2, 1) AS drained_s_after_import_end
  FROM jobs WHERE created_at > to_timestamp(1790216297.0) AND status='done' GROUP BY 1,2 ORDER BY 1,2;
