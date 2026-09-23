## Verdict: DO-NOT-MERGE

Otomatik W0a cutover ve iç hata recovery yolu büyük ölçüde düzelmiş. Ancak başarılı cutover sonrasındaki **belgelenmiş manuel rollback**, eski sürümü kayıt sırrını geri yüklemeden başlatıyor; eski sürümde sır yoksa kayıt herkese açılıyor. İnceleme yalnızca `ce64e79..6ddd2c8` deltası üzerinde statikti; test, Docker ve SSH çalıştırmadım.

## Per-finding status

- **#1 FIXED** — Hash, hazırlanmış immutable commit’in Compose dosyasından doğrulanıyor; eksik/yanlış hash durdurmadan önce reddediliyor ([remote-deploy.sh:202](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/remote-deploy.sh:202>)).
- **#2 PARTIAL** — Otomatik recovery eski modeli, image ID’sini ve env yedeklerini kullanıyor; başarılı cutover sonrası manuel rollback env yedeklerini geri yüklemiyor ([RUNBOOK.md:548](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:548>)).
- **#3 FIXED** — Writer’lar durduktan sonra ikinci dump alınıyor; bu adım başarısız olursa migration başlamadan eski stack’e dönülüyor. Paylaşılan fd 8 kilidinde belirgin deadlock görmedim ([remote-deploy.sh:310](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/remote-deploy.sh:310>)).
- **#4 PARTIAL** — Üç marker iç sağlık kontrolünden sonra yazılıyor, fakat üç ayrı `mv` tek bir atomik yayın değil ([remote-deploy.sh:348](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/remote-deploy.sh:348>)).
- **#5 FIXED** — G-W0-5, süresi dolmuş bearer için `E_AUTH` bekliyor ([PHASE2-4-ROADMAP.md:149](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/docs/decisions/PHASE2-4-ROADMAP.md:149>)).
- **#6 FIXED** — Public `/ready` yalnızca status veriyor ([app.py:283](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/server/app.py:283>)). Loopback kararı socket peer’ından alınıyor; `proxy_headers=False` olduğundan Caddy arkasında sahte `X-Forwarded-For` bunu değiştirmiyor.

## New findings

| Önem | Bulgu |
|---|---|
| **High** | Manuel rollback eski env sırlarını geri yüklemiyor; runbook yedeklerin silinmesini de söylüyor ([RUNBOOK.md:513](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:513>), [554](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/RUNBOOK.md:554>)). Eski sürümde kayıt sırrı isteğe bağlı olduğundan kayıt açılabilir. |
| **Medium** | Marker yazımlarından biri başarısız olursa rollback ref/dump çifti tutarsız kalabilir ([remote-deploy.sh:353](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/deploy/scripts/remote-deploy.sh:353>)). |
| **Medium** | `ops status`, `/ready` ayrıntılarını okumadan önce DB’ye bağlanıyor; DB arızasında vaat edilen tanıyı gösteremiyor ([cli.py:96](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/ops/cli.py:96>), [service.py:397](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6/src/hlmemo/ops/service.py:397>)). |

D-034/D-035’in stdin izolasyonu, detached runner ve yalnızca iç arızada otomatik DB rollback sınırında yeni bir regresyon bulmadım. Public kontrollerin marker’lardan sonra gelmesi, dış ağ arızasında çalışan yeni stack’i koruyan mevcut sınırla uyumlu.