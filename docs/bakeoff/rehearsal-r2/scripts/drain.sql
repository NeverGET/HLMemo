SELECT label, kind, count(*) AS jobs,
       round(extract(epoch FROM max(done_at))::numeric - w_end, 1) AS drained_s_after_last_write,
       round(avg(extract(epoch FROM done_at - created_at))::numeric, 1) AS avg_wait_s,
       round(max(extract(epoch FROM done_at - created_at))::numeric, 1) AS max_wait_s
FROM (VALUES ('gl3-neutral', 1790214626.75::numeric, 1790214648.128::numeric), ('gl3-identifier', 1790215231.423, 1790215249.751)) w(label, w_start, w_end)
JOIN jobs j ON extract(epoch FROM j.created_at) BETWEEN w_start AND w_end + 1
GROUP BY label, kind, w_end ORDER BY label, kind;
