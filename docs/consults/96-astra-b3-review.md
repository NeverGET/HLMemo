## Verdict

**NO-GO** — Astra/Sol incelemelerinde iki HIGH bulgu.

1. **HIGH — Özel sürümün içeriği `superseded_by.quote` üzerinden sızıyor.**  
   **Konum:** `src/hlmemo/db/read_queries.py:694`, `src/hlmemo/core/read_service.py:651`.  
   **Reproducer:** A, `device:A` kapsamlı bir `episode` içine `SECRET_SENTENCE` yazar. Ardından herkese açık bir taşıyıcıya `updates: [{item: özel_clue, old_span: "SECRET_SENTENCE", mode: "revise"}]` ekler. Historical dalı bunu herkese açık, part-scope link olarak kaydeder. A, hedef logical item’ı normal revision ile herkese açık, temizlenmiş bir sürüme dönüştürür. Eski özel sürümü okuyamayan B, yeni sürüme `memory.raw` çağırınca `superseded_by[].quote` içinde `SECRET_SENTENCE` alır.  
   **Neden:** Cross-item linklerde `dst_version_id` kontrolü atlanıyor; quote’un geldiği özel sürümün yetkisi doğrulanmıyor.  
   **Minimal fix:** Pinned hedef sürüm için authorization kontrolü ekleyin; linki okunan sürüme veya doğrulanmış survivor ilişkisine bağlayın. Query tarafında da yalnız logical ID üzerinden yanlış işaretlemeyi önleyin. Regresyon assertion’ı: B’nin cevabında `SECRET_SENTENCE` bulunmamalı. Bu senaryo kod üzerinden doğrulandı; DB reproducer’ı çalıştırılamadı.

2. **HIGH — Geçerli geri alma zinciri 16 adımda kesiliyor.**  
   **Konum:** `src/hlmemo/librarian/actor.py:844`, `:878`, `:922`.  
   **Reproducer:** E1 ile revise yapın. Ardından 16 kez güncel sürüme yeni revise uygulayıp hemen o yeni revise’ı geri alın. E1’in sonucu yeniden günceldir; ancak E1’i geri almak `E_VERSION_CONFLICT` üretir. `_live_version` ve `_live_link`, restore kopyalarını 16 iterasyondan fazla izlemiyor.  
   **Çalıştırılmış test:** Yalnız 17’nin current olduğu `1 → … → 17` restorasyon zincirinde `_live_version(...)`, beklenen `17` yerine **`None`** döndürdü; bağımsız olarak tekrarlandı.  
   **Minimal fix:** Her iki sabit sınırı cycle detection içeren traversal ile değiştirin. 16 revise/revert döngüsü ardından E1 revert ve replay eşitliği entegrasyon testi ekleyin.

Doğrulama: **60 birim testi geçti; G-SURF 2991/3000.** Write hedef yetkileri, sıralı kilitler, expected-version ve request-id yollarında ek hata bulunmadı. Yeni mutation’lar live/replay’de ortak uygulamayı kullanıyor; eski event’lerin byte-identical replay’i bu oturumda DB üzerinde doğrulanamadı.

Migration/tablo değişikliği gerekmiyor. Desteklenen `fda8fa0` rollback’i dağıtım öncesi dump’ı geri yükler ve sonraki yazıları kaybettirir. B3 event’lerini koruyarak yalnız kodu düşürmek güvenli değildir: eski replay `resolved.updates` işlemez. Docker erişimi olmadığından entegrasyon ve deploy/rollback provası çalıştırılmadı.