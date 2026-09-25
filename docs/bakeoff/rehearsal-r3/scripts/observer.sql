-- Observer safety (D-058/D-074, D-099 "observer = 0 librarian mutations") since event :since.
-- Usage: { echo '\set since <event_id>'; cat observer.sql; } | ./vmsql.sh
-- PASS = every row below is 0 except signal_upsert (placement is the only thing an observer applies).
\pset footer off
SELECT :since AS since_event_id, (SELECT max(event_id) FROM events) AS max_event_id,
       (SELECT count(*) FROM events WHERE kind = 'librarian' AND event_id > :since) AS librarian_events;
-- mutation ops recorded by librarian events (payload.resolved.mutations[].op), by role
SELECT coalesce(m->>'op', '<none>') AS op, e.payload->'resolved'->>'role' AS role, count(*) AS n
  FROM events e
  LEFT JOIN LATERAL jsonb_array_elements(coalesce(e.payload->'resolved'->'mutations', '[]'::jsonb)) m ON true
 WHERE e.kind = 'librarian' AND e.event_id > :since
 GROUP BY 1, 2 ORDER BY 1, 2;
-- rows written BY a librarian event (must be 0: links, versions; version_signals are allowed)
SELECT (SELECT count(*) FROM links l JOIN events e ON e.event_id = l.source_event_id
         WHERE e.kind = 'librarian' AND e.event_id > :since) AS links_by_librarian,
       (SELECT count(*) FROM memory_versions v JOIN events e ON e.event_id = v.source_event_id
         WHERE e.kind = 'librarian' AND e.event_id > :since) AS versions_by_librarian,
       (SELECT count(*) FROM version_signals s JOIN events e ON e.event_id = s.source_event_id
         WHERE e.kind = 'librarian' AND e.event_id > :since) AS signals_by_librarian_allowed;
-- closes / supersessions anywhere (compare with the pre-run snapshot: the delta must be 0)
SELECT count(*) FILTER (WHERE superseded_at <> 'infinity') AS versions_superseded,
       count(*) FILTER (WHERE valid_to <> 'infinity') AS versions_valid_closed
  FROM memory_versions;
SELECT count(*) AS links_total,
       count(*) FILTER (WHERE superseded_at <> 'infinity' OR valid_to <> 'infinity') AS links_closed
  FROM links;
-- proposals: none decided or applied in observer mode
SELECT count(*) FILTER (WHERE applied_at IS NOT NULL OR status IN ('decided', 'applied')) AS batches_decided_or_applied,
       count(*) AS batches_total
  FROM librarian_batches;
SELECT status, count(*) AS questions FROM librarian_questions GROUP BY 1 ORDER BY 1;
