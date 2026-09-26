## Verdict (FIX-NEEDED)

## Findings

| Threat | Severity | file:line | Reproducing scenario | Fix |
|---|---|---|---|---|
| T1 | HIGH | `src/hlmemo/core/memory_map.py:473` | A+B okunabilir; B’de `librarian_cross_project=exclude`; öğe `[A,B]` projelerine ait. `ask(A)` öğeyi haritaya/provider’a kabul ediyor. Mock doğrulamasında D-083 reddederken `load_view` kabul etti. | D-083 kontrolünü harita, özet üyeliği, her gönderim ve son yetki kontrolüne uygula. |
| T2 | HIGH | `src/hlmemo/librarian/tasks/research.py:326`; `src/hlmemo/librarian/tasks/map_summary.py:269` | Soru/öğe metni `password = "SuperSecret123456"` → JSON kaçışları → redaktör parolayı kaçırıyor. Her iki prompt yolunda çalıştırılarak doğrulandı. | JSON serileştirmeden önce bütün metin değerlerini redakte et; son gönderim kontrolünü koru. |
| T4 | HIGH | `src/hlmemo/core/research_service.py:454`; `src/hlmemo/librarian/provider.py:591` | İlk yanıt şema hatalı, ücretli tekrar başarılı. Mock transport ile iki gönderim: soru limiti **$0.0088**, gerçekleşen harcama **$0.016**. Kontrol yalnızca mantıksal çağrı öncesinde çalışıyor. | Her retry/fallback girişiminden önce ortak soru bütçesinden rezervasyon yap. |
| T5 | MEDIUM | `deploy/scripts/check_librarian.py:78`; `deploy/scripts/llm_env_release.py:85` | Disk ve çalışan servislerde research/map-summary anahtarları farklı olabilir; manifest bunları karşılaştırmıyor. Zıt anahtar değerlerinin aynı fingerprint ürettiği doğrulandı. | Yeni anahtarları ve soru limitlerini manifest, runtime raporu ve fingerprint kapsamına al. |

## Safe-to-ship

**Hayır.** Scope, redaksiyon ve harcama sınırı açıkları giderilmeli.

42 saf birim testi geçti; canlı DB/migration testi çalıştırılmadı. T3/T6 veya migration DDL’sinde ek engel bulunmadı. İstenen D-140–D-146 kayıtları exportta yok. Dual review tek turda tamamlandı; dosyalar değiştirilmedi.