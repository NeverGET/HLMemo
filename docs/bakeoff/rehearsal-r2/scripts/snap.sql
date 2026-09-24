\pset footer off
SELECT now() AS at, (SELECT version_num FROM alembic_version) AS alembic;
SELECT count(*) AS links_total,
       count(*) FILTER (WHERE superseded_at <> 'infinity') AS links_superseded,
       count(*) FILTER (WHERE valid_to <> 'infinity') AS links_valid_closed
  FROM links;
SELECT count(*) AS versions_total,
       count(*) FILTER (WHERE superseded_at <> 'infinity') AS versions_superseded,
       count(*) FILTER (WHERE valid_to <> 'infinity') AS versions_valid_closed
  FROM memory_versions;
SELECT kind, count(*) FROM events GROUP BY kind ORDER BY kind;
SELECT kind AS job_kind, status, priority, count(*) FROM jobs GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT count(*) AS version_signals FROM version_signals;
SELECT status, kind, count(*) FROM librarian_questions GROUP BY 1,2 ORDER BY 1,2;
SELECT count(*) AS batches, count(*) FILTER (WHERE applied_at IS NOT NULL) AS batches_applied FROM librarian_batches;
SELECT count(*) AS llm_calls, round(coalesce(sum(cost_usd),0)::numeric, 4) AS cost_usd FROM llm_calls;
