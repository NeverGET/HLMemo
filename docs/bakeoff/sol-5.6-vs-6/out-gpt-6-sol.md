## Verdict

**DO-NOT-MERGE.** Kod incelemesi, geçersizleşmiş bir token ile LLM’ye veri gönderilebildiğini ve varsayılan açık davranış için zorunlu W‑E değerlendirmesinin yapılmadığını gösteriyor.

## Findings

| Severity | file:line | Defect | Concrete trigger | Fix |
|---|---|---|---|---|
| High | `src/hlmemo/librarian/privacy.py:93` | LLM öncesi kontrol token generation’ı karşılaştırmıyor. | Token döndürme generation’ı artırıp cihazı `trusted` bırakıyor (`db/auth_queries.py:203`). Döndürme sonrası retry veya fallback eski isteğin alıntılarını sağlayıcıya gönderebilir; son kontrol veriyi ancak gönderildikten sonra reddeder. | İstek generation’ını her sağlayıcı denemesinden önce kilit altında doğrula. |
| High | `src/hlmemo/cli/preflight.py:178` | Sentez sorular için varsayılan açık; gereken corpus A ∪ B W‑E kapısı yok. | Yayınlanan PASS yalnızca uygulayıcının hazırladığı public `syn-docs` zayıf alt kümesini ölçüyor (`test_w2e_glive_d.py:83`); kategori ve stale-claim sınırlarını ölçmüyor. Roadmap §W‑E bu kanıt olmadan varsayılan açmayı yasaklıyor. | Varsayılanı kapat; A ∪ B kapısını çalıştırıp sonuçlarına göre aç. |
| Medium | `src/hlmemo/librarian/tasks/synthesis.py:293` | Altı saniyelik süre maliyet kapatma ve ledger yazımını da kesebiliyor. | HTTP denemesi 5,5 saniye sürebiliyor (`provider.py:544`); kalan sürede DB kapatma iptal edilirse rezervasyon 10 dakikalık worst-case sweep’e kalır ve çağrı kaydı eksik olabilir. | Temizlik için süre ayır ve kapatmayı iptale dayanıklı yap. |
| Medium | `src/hlmemo/cli/hlm.py:994` | İstemci süresi sunucunun yalnızca sentez sınırına eşit: 6 saniye. | Retrieval ve ağ süresi eklendiğinde istemci, sunucunun `synthesis_unavailable` yanıtından önce timeout alır; preflight sorguyu tekrar deneyebilir. | Retrieval ve ağ payını içeren daha uzun istemci süresi kullan. |
| Medium | `tests/integration/test_w2e_glive_d.py:96` | “Fast path” karşılaştırması ilk 3 clue’yu drill ediyor; gerçek preflight ilk 5’i öneriyor (`cli/preflight.py:63`). | Yanıt 4. veya 5. hit’teyse sentez kazanımı olarak sayılır. | Aynı top‑5 baseline ile yeniden ölç. |
| Medium | `tests/integration/_synthesis_fixtures.py:65` | Puanlayıcı herhangi **bir** anahtarın geçmesini tam yanıt sayıyor. | Test sorusu `q168` iki timestamp istiyor (`questions.json:1816`); yalnızca `t_invalid` yazan yanıt da doğru sayılır. | Alternatif yazımları, birlikte gerekli olgulardan ayırıp tam yanıtı puanla. |
| Low | `tests/integration/test_w2e_glive_d.py:184` | Bildirilen p95, hata ve timeout çağrılarını dışlıyor. | DeepSeek kaydında 7 schema failure ve 3 timeout var; yayımlanan gecikme yalnız başarılı çağrılardan hesaplanıyor. | Tüm isteklerin uçtan uca gecikmesini raporla. |
| Low | `tests/integration/test_w2e_glive_d.py:91` | Negatifler split filtresinden önce ekleniyor. | “Held-out” koşu kalibrasyon split’indeki negatifleri de false-answer ölçümüne katıyor. | Negatiflere de test split filtresi uygula. |