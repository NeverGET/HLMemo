## Verdict

**NO-GO**

1. **HIGH — `memory.raw.payload_item.updates` gizli hedefi ve alıntıyı sızdırıyor.**  
   `src/hlmemo/core/read_service.py:570-602,624-630`  
   `_filter_item_links` yalnız `links` alanını yetkilendiriyor; `updates` aynen dönüyor.  
   **Reproducer:** A, `device:A` hedefini güncelleyen `device_scope=all` carrier yazar; B carrier’ı `memory.raw` ile okuyunca `{"item":"v123","old_span":"PRIVATE TOKEN"}` değerini görür. Gerçek helper çağrısında bu çıktı doğrulandı.  
   **Minimal fix:** Her update hedefini pinned-version/logical-id endpoint yetkisiyle filtrele; yetkisiz veya çözülemeyen update girdisini bütünüyle kaldır. İki cihazlı integration testi ekle.

2. **HIGH — `supersede` ve historical link-only yolları belirsiz `old_span` kabul ediyor.**  
   `src/hlmemo/core/write_updates.py:313-335`; `docs/decisions/DECISIONS.md:282-289`  
   Bu yollar yalnız `span_occurs()` çalıştırarak D-118’in exactly-once, word-boundary ve not-whole kurallarını atlıyor.  
   **Reproducer:** hedef `"The cache is Redis. The cache is monitored."`, `old_span:"cache"`, `mode:"supersede"`; `span_occurs == True` ve tüm hedef kapatılıyor. Beklenen `span_not_unique`.  
   **Minimal fix:** Ortak `old_span` guard’larını mode/kind ayrımından önce çalıştır; historical `revise` için replacement/new-body guard’larını da koru. Her iki yol için regresyon testi ekle.

3. **HIGH — dar kapsamlı carrier daha geniş kapsamlı hedefi global kapatabiliyor.**  
   `src/hlmemo/core/write_updates.py:313-318,409-444`; `src/hlmemo/librarian/revise.py:121-130`  
   `replacement_visibility` revise’a uygulanıyor fakat mutating supersede’a uygulanmıyor.  
   **Reproducer:** `device_scope=all` hedef + A’ya özel carrier + exact-span supersede. Güncelleme uygulanıp global hedef kapanıyor; C eski hedefi artık current okuyamıyor, özel carrier/link’i de göremiyor. Aynı sorun MAIN+OTHER hedefi MAIN-only carrier ile oluşuyor.  
   **Minimal fix:** Close yapan supersede için carrier görünürlüğünün hedef görünürlüğünü kapsamasını zorunlu kıl; aksi halde `replacement_visibility` ile reddet veya açıkça link-only davran.

4. **MEDIUM — `memory.raw.superseded_by` ayrık historical aralıkları eşleştirebiliyor.**  
   `src/hlmemo/db/read_queries.py:694-700`  
   Yalnız `link.valid_to > version.valid_from` denetleniyor; finite version’ın üst sınırı yok. Ocak–Şubat sürümü, Nisan’da başlayan normal supersedes link’iyle işaretlenebilir.  
   **Minimal fix:** Non-self linklerde eksik `link.valid_from < version.valid_to` sınırını ekle; revision self-link boundary istisnasını koru. Ayrık ve sınır-eşitliği testleri ekle.

5. **MEDIUM — revise/reopen ile üretilen lesson brief’e boş gövdeyle girebiliyor.**  
   `src/hlmemo/core/read_service.py:542-552,624-632`; `src/hlmemo/brief/fetch.py:178-196`  
   `_locate` mutation sürümlerini bulamıyor; `payload_item={}` oluyor. Brief ise `chunks` alanını kullanmadığından item’ı `verified/current` fakat `body=""` kabul ediyor.  
   **Minimal fix:** Brief’te `char_start/char_end` ile chunks’tan gövdeyi güvenli biçimde yeniden kur veya mutation provenance desteği ekle; revise→raw→brief ve revert→brief testleri ekle.

### Kontrol özeti

- Hedef auth/grant kontrolleri, sorted logical locks, head/expected-version denetimi, tek transaction, request-id replay ve iki yazarın serileştirilmesi yapısal olarak doğru.
- Yeni mutation’lar event içinde tutulup replay’de uygulanıyor; eski eventlerde `resolved.updates` yok ve `keep_source` varsayılanı eski davranışı koruyor.
- Revert yolu compensating event, yetki, lock, head bağımlılığı, link sonlandırma ve reopen işlemlerini kapsıyor; ek eksik bulunmadı.
- Doğrudan query/raw `superseded_by` sorguları gizli superseder’ı yeniden yetkilendiriyor; bulgu 1 ayrı olarak verbatim `payload_item.updates` kanalında.
- Eski istek/ack şekilleri korunuyor; yeni read alanları additive. `$schema` kaldırılması için somut kırılma bulunmadı, fakat gerçek eski-client matrisi çalıştırılmadı.
- Migration/table değişikliği yok. Resmî `deploy.sh --rollback` pre-upgrade dump’ı geri yükleyerek güvenli döner fakat deploy sonrası yazıları kaybeder; yeni eventleri tutarak yalnız eski image’a dönmek güvenli değildir.
- `git diff --check` temiz; G-SURF **2991/3000**. Hedefli koşuda **60 test geçti**; 45 vaka eksik `models/.../tokenizer.json` nedeniyle assertion aşamasına ulaşamadı. Worktree değişmedi.