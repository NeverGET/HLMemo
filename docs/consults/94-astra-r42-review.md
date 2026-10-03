## Verdict

**GO-with-fixes.** Aşağıdaki açıklar giderilmeli veya ikinci turun sonunda kalan risk açıkça kabul edilmeli. Gerçek Google harcamasının sıfırlandığı doğrulanmış bir HIGH bulgu yok.

1. **MEDIUM — `src/hlmemo/librarian/profiles.py:195`: bozuk politika kabul ediliyor.**  
   `_unbilled_errors("test", value)`, `false`, `0`, `{}` ve bunların JSON metinleri için hata yerine `()` döndürüyor; yeniden üretildi.  
   **Minimal düzeltme:** yalnızca eksik değer/boş listeyi kapalı politika say; diğer türleri reddet ve negatif test ekle.

2. **LOW — `src/hlmemo/librarian/provider.py:189`: yinelenen JSON anahtarı kullanım kanıtını silebiliyor.**  
   Aşağıdaki HTTP 503 gövdesi, mevcut Google politikasıyla **True → $0** üretiyor:
   ```json
   [{"error":{"usage":{"completion_tokens":100}},"error":{"code":503,"status":"UNAVAILABLE","message":"high demand"}}]
   ```
   `json.loads`, ilk `error` alanını kanıt taramasından önce kaybediyor.  
   **Minimal düzeltme:** `object_pairs_hook` ile her derinlikte yinelenen anahtarları reddet; eşleştirici ve settlement regresyonu ekle.  
   Sol bunu **HIGH/NO-GO** değerlendirdi. Google’ın bu sentetik biçimi gönderip ücretlendirdiğine dair kanıt bulunmadığından, verilen rubrik altında doğrulanmış HIGH saymıyorum.

3. **LOW — `src/hlmemo/config.py:214`, `deploy/scripts/llm_env_release.py:91`: yeni politika ortam değişkeni dağıtım kontrollerinde izlenmiyor.**  
   `HLM_UNBILLED_ERRORS` kabul ediliyor; fingerprint/manifest kapsamına alınmıyor. Diskteki ve çalışan süreçteki politika farklıyken provenance kontrolü geçebilir.  
   **Minimal düzeltme:** ortam değişkeni override’ını kaldır veya fingerprint ve manifest doğrulamasına ekle.

4. **Round-1 kapanışı:** opt-in/default-off kapalı (`profiles.py:88`); tam zarf ve kullanım kanıtı şartı **kısmen kapalı** (bulgu 2); D-017 kapalı (`provider.py:175`, Google profil dosyaları:23); yeni retry eklenmemiş (`provider.py:1219`); negatif testler genişletilmiş (`test_writer_unavailable.py:248`), fakat yukarıdaki durumlar eksik. Consult 93 dosyası bu worktree’de yok; eşleme verilen özetten yapıldı.

5. **Yanıt güvenliği:** `.post()` gövdeyi tamamen okuyor (`provider.py:1125`). SSE, kesik JSON, HTML 503, ek alanlar ve `usage: {}`/sıfır değerler en yüksek maliyette kalıyor; yarım okuma/timeout da aynı şekilde. Tam gzip yanıtı açıldıktan sonra eşleştiriliyor. Google’ın [faturalama belgesi](https://ai.google.dev/gemini-api/docs/billing#am-i-charged-for-failed-requests) “400 veya 500” hatalarını ücretsiz tanımlıyor; bunu 503’e uygulamak bir yorum, birebir 503 garantisi değil.

6. **Rezervasyon muhasebesi:** yeni $0 yolu mevcut tek settlement çağrısını kullanıyor (`provider.py:1232`). MemoryBudget `pop` (`budget.py:192`) ve transaction içindeki DB `DELETE` (`budget.py:134`) idempotent. Gönderim öncesi iptal sıfır, uçuş sırasındaki iptal/timeout/transport en yüksek maliyet; başarı gerçek maliyet; açık breaker rezervasyon oluşturmuyor. Yeni rezervasyon kaçağı bulunmadı. Settlement sonrasındaki ledger yazısının iptalde kaybolabilmesi (`provider.py:1233`) önceden mevcut.

7. **Release doğrulaması:** `fda8fa0..HEAD` şema/compose değişikliği içermiyor; **19 profil yüklendi, 69 birim testi geçti**. Mevcut `llm.env` değişikliği gerekmiyor. Eski image/env’e rollback eski settlement davranışını geri getirir; geçmiş harcamayı düzeltmez. Canlı env, DB entegrasyon testi, Docker deterministik gate ve rollback çalıştırılmadı; dolayısıyla dağıtım onayı verilmiş değil.

[Birleştirilmiş inceleme kaydı](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/docs/consults/94-codex-combined-review.md)