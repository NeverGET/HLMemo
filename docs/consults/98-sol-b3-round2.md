## Verdict

**GO-with-fixes** — hedef commit `057d411`. Doğrulanmış açık HIGH yok; yedi Round-1 bulgusu kod açısından kapalı. Yeni iki MEDIUM doğruluk riski düzeltilmeli veya owner tarafından residual risk olarak açıkça kabul edilmeli.

1. **INFO — Round-1 kapanışları**
   - Astra #1 **CLOSED** — `read_queries.py:670-816`, `librarian_queries.py:320-419`: pinned link yalnız yetkili pinned sürüme veya değişmemiş kopyasına uygulanıyor; version-keying bütün okuyucularda mevcut.
   - Astra #2 **CLOSED** — `librarian/actor.py:838-909`: revert traversal visited-set kullanıyor; sabit 16-hop sınırı yok.
   - Sol #1 **CLOSED** — `read_service.py:607-690`: raw `payload_item.updates` hedef yetkisine göre filtreleniyor.
   - Sol #2 **CLOSED** — `write_updates.py:137-197,338-360`: exactly-once/boundary tüm mode/kind’larda; revise için not-whole/replacement guard mevcut. D-118 dokümantasyon farkı #5’te.
   - Sol #3 **CLOSED** — `write_updates.py:189-190`: dar carrier’ın geniş hedefi kapatması `replacement_visibility` ile reddediliyor.
   - Sol #4 **CLOSED** — `read_queries.py:735-743`: unpinned linklerde iki interval’ın örtüşmesi zorunlu.
   - Sol #5 **CLOSED** — `brief/fetch.py:176-261`: body chunks’tan kuruluyor; self-link revised lesson’ı gizlemiyor.

2. **MEDIUM — Pinned PART link ilgisiz revision sonrasında düşebiliyor**
   - Konum: `read_queries.py:670-697,761-816`; `librarian_queries.py:320-419`; `research_service.py:743-752`.
   - Senaryo: v1=`TTL=60; owner=A`; pinned PART link `TTL=60`ı supersede eder; v2 yalnız owner’ı değiştirir. Full body değiştiğinden stale TTL query ve `memory.ask` etiketlerinde yeniden güncel görünebilir.
   - Prod etkisi: 252 explicit + 119 backfill linkin **371/371’i unpinned** (`explicit_links.py:91-105`, `backfill_links.py:262-275`); mevcut setten etkilenen **0/371**. Risk yeni B3 pinned linklerle başlar.
   - Minimal fix: cross-item PART linki, exact quote boundary-safe ve tekil kaldığı sürece kesintisiz descendant zincirinde taşı; pinned authorization’ı koru.

3. **MEDIUM — Brief fallback pinned sürümü kaybediyor**
   - Konum: `brief/fetch.py:252-269,331-345`.
   - Reproducer: current `v21/lid1021` için server `superseded_by=[]` döndürürken başka lesson’ın eski `v7/lid1021`e pinned linki `v21`i yine `superseded` diye dışladı.
   - Minimal fix: modern server’da authoritative incoming alanına güven; logical-ID fallback’i yalnız alanı sağlamayan eski server’larla sınırla veya `dst_version_id`yi koru.

4. **MEDIUM — Recursive pinned kontrolü için maliyet sınırı yok**
   - Konum: `read_queries.py:670-697,721-816`; `librarian_queries.py:349-419`.
   - En kötü maliyet yaklaşık `O(H×L×D)`; hop’lar version PK kullanıyor ve azalan ID ile sonlanıyor, fakat link/depth pre-limit yok. Çıktı limitleri 5/3 traversal sonrasında uygulanıyor; 10/20 saniyelik timeout yalnız hatayla sınır koyuyor.
   - Minimal fix: derin zincir/fan-in benchmarkı; gerekirse ancestry’yi version başına bir kez hesapla ve destination index’ini ölçümle doğrula.

5. **LOW — Supersede not-whole istisnası güvenlik açısından kabul edilebilir**
   - Konum: `write_updates.py:167-190`; `DECISIONS.md:282-290`.
   - Explicit mode, exact version, unique boundary match, authorization, carrier visibility ve revert korunuyor. Ancak kısa bir span bütün item’ı kapatabildiğinden bu semantik doğruluk garantisi değildir.
   - Minimal fix: istisnayı append-only ADR ile netleştir; kalan semantik riski owner kabul etsin.

6. **MEDIUM — Release forward-only**
   - Konum: `deploy/RUNBOOK.md:928-949`; `db/replay.py:249`; `librarian/reversal.py:117`.
   - `057d411`, prod `fda8fa0` üzerine çıkabilir; R4.2 önce birleşirse artifact mutlaka **R4.2+B3** olmalı. İlk B3 update’inden sonra eski image’ı aynı DB üzerinde çalıştırmak güvenli değil.
   - Rollback yalnız pre-upgrade dump + eski image ile destekleniyor ve deploy sonrası bütün yazıları kaybettiriyor; compensating revert eski replay uyumluluğunu geri getirmiyor.
   - Minimal fix: birleşik artifact üzerinde replay ve dump-restore provası; D-229 kaydını merge sırasında koru.

7. **INFO — Doğrulama**
   - `git diff --check` temiz; B3 hedef paketinde **127 test geçti**.
   - B3’ün R4.2 `44fde29` üzerine geçici temiz merge’inde **289 hedefli test geçti**.
   - İnceleme `057d411` commit bloblarına sabitlendi; sonradan beliren eşzamanlı WIP değişiklikleri kapsama alınmadı.
   - Kayıtlar: [Astra raporu](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a7fcbdd45d58e5e3d/docs/consults/98-astra-b3-review.md>) ve [Sol raporu](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a7fcbdd45d58e5e3d/docs/consults/98-sol-b3-review.md>). Tek kullanımlık test DB temizlendi.