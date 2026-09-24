## Verdict (DO-NOT-MERGE)

D-077’nin hard prerequisite’i karşılanmıyor; ayrıca concurrency replay’i bozabiliyor. İnceleme yalnızca statikti; test veya değişiklik yapılmadı.

## Stranding status (PROMOTION-READY no)

QUEUED/RUNNING ayrımı ve replay kaydı doğru; fakat scope değişimi hâlâ `accepted_pending` cevabı kalıcı olarak strand edebilir.

## Findings

| Önem | Bulgu |
|---|---|
| **High** | **Scope-churn stranding:** Action observer C’ye genişledikten sonra deployment promotion bekler. Normal revision C’yi kaldırırsa action stale olur ve kalan projeler zaten assistant’tır. C’nin sonraki promotion’ı, artık current touched set’te olmadığı için soruyu seçmez; başka tetikleyici kalmaz (`roles.py:141-164`, `actor.py:91-110`). |
| **High** | **Concurrent batch replay bozulabilir:** `event_id`, item/batch kilitlerinden önce ayrılıyor. Düşük-ID job beklerken yüksek-ID job batch B’yi yaratabilir; düşük-ID job sonra B’yi ready yapıp C’yi açabilir. Replay düşük ID’den başlayınca B update’i kaybolur ve iki open batch unique constraint’i patlar (`worker.py:779-784,451,511-514`; `replay.py:72-89`; `actor.py:568-592`). |
| **High** | **Partial supersession geçerli içeriği sonuçtan düşürebilir:** Tüm multi-fact item, sorgunun hangi statement’ı eşleştirdiğine bakılmadan superseder’ın arkasına taşınıyor; bu işlem `n_fetch` öncesi olduğundan ilgisiz hâlâ-geçerli fact top-K’dan kaybolabilir. Multi-hop zincirler de logical-ID sırasıyla tek geçişte işleniyor ve önceki ordering constraint’ini yeniden bozabiliyor (`read_service.py:242-258`; `supersession.py:57-69`). |
| **Medium** | D-062’nin literal “job başına tek librarian event” koşulu sağlanmıyor: her defer/backoff ayrı event, başarı da ikinci terminal event üretiyor (`worker.py:381-421,871-892`). |
| **Medium** | N=3, üç job connection’ı + lease renewals + privacy prechecks + heartbeat/bookkeeping ile 8-connection zarfını aşabilir; ayrı pool altıya kadar büyüyor. 495 ms G-L3 yük sonucu varken N=3 için VPS ölçümü yok (`worker.py:294-314,915-917,1046-1054`). |
| **Medium** | v1-close tehlikeli mutation’ı engelliyor; fakat cross-project admin re-plan yalnızca home-project capability üretip yabancı subject’i privacy gate’te kaybedebilir (`questions.py:208-212,350-390`). |

Lease fencing, atomic spend/lineage guards ve kilit sıralamasında yeni deadlock bulmadım. Cross-scope sızıntı veya D-074 observer user-data mutation yolu da bulmadım.