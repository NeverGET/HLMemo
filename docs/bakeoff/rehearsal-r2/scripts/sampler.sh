#!/usr/bin/env bash
# sampler.sh OUT : every ~2 s on the VM: loadavg, cpu (from /proc/stat delta), docker stats (mem/cpu per service), job backlog.
S=/Users/cemalkurt/Projects/HLMemo/deploy/.local/127.0.0.1-2223
exec /usr/bin/ssh -F $S/ssh_config hlm-deploy 'db=$(docker ps -q --filter label=com.docker.compose.service=db)
prev=$(head -1 /proc/stat)
while true; do
  now=$(date +%s.%N); cur=$(head -1 /proc/stat)
  cpu=$(python3 -c "import sys; a=list(map(int,sys.argv[1].split()[1:])); b=list(map(int,sys.argv[2].split()[1:])); d=[y-x for x,y in zip(a,b)]; t=sum(d) or 1; print(f\"busy={100*(t-d[3]-d[4])/t:.0f}% iowait={100*d[4]/t:.0f}%\")" "$prev" "$cur"); prev=$cur
  la=$(cut -d" " -f1-3 /proc/loadavg)
  st=$(docker stats --no-stream --format "{{.Name}}={{.MemUsage}}|{{.CPUPerc}}" | sed "s/hlmemo-prod-//; s/-1=/=/; s/ //g" | paste -sd" " -)
  jobs=$(docker exec -i "$db" sh -c "psql -X -At -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\"" < /tmp/r2-backlog.sql)
  echo "$now load=$la $cpu | $st | backlog: ${jobs:-none}"
  sleep 1
done' </dev/null >> "$1" 2>&1
