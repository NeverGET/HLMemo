## Recommendation
Salt okunur inceleme; dosya değiştirilmedi. Aşağıdaki `librarian/` anchor’ları `src/hlmemo/` altında **a65a8f5** sürümüne aittir; main’de v2 henüz yoktur. Δ negatifse stale-first azalır; verilen sınırlar tahmin değil, teşhisin tavanlarıdır.
1. **Önce ölçülebilirlik:** `librarian/tasks/write_review.py:327` — bütün çiftlerin ham kararını, guard dönüşümlerini ve verifier sonucunu ayrı kaydet. A Δ **0**, precision riski yok; sonraki değişikliklerin önkoşulu.
2. **Zaman ve yön kanıtını düzelt:** `librarian/guards.py:441`, `librarian/prompts/relate_verify/v2.md:9`, `librarian/tasks/write_review.py:193` — tarih türünü taşı, A/B kronoloji telkinini kaldır; yön anlaşmasını gevşetme. En yüksek beklenen kazanç/risk oranı; erişilen **7 çift** fırsattır, kaçının gate’i düzelteceği bilinmiyor.
3. **Doğrulanmış whole-scope için owner-approved close:** `librarian/guards.py:354`, `:599`; `librarian/tasks/write_review.py:646`, `:651` — tam içerik/statement kapsamı korunmalı, tek-statement dahil otomatik close kaldırılmalı. 2+3’ün mevcut adaylarla ideal toplam tavanı **A 8→4 (Δ−4)**; garanti değil.
4. **Hedefli aday genişlet:** `librarian/candidates.py:36`, `:38`, `:45`, `:53`; `librarian/tasks/write_review.py:355` — önce aynı kaynak bölümü/karar kimliği için ayrı, sınırlı kota; sonra doc_chunk↔episode; en son 24/8 ablasyonu. Caps **+2**, tür düzeltmesi **+1 tartışmalı A çiftini** erişilebilir yapıyor; bunlar stale-first Δ değildir.
5. **Pull-up shipping v3’e girmesin:** `core/supersession.py:95@6a96ba1` korunmalı. Ek ideal fırsat yalnız **Δ−1** (close ile toplam 8→3); yanlış linkte sıralama riski daha yüksek.

## Guards
- **Zaman alanları:** `recorded_at` yalnız kayıt/replay sırasıdır; commit/mtime yalnız provenance’dır. Hiçbiri doğruluk yönünü belirlemez veya supersede’ı tek başına veto etmez. Import fallback `valid_from` da açık tarih kanıtından ayrılmalıdır.
- **Açık tarih:** aynı claim/scope’a bağlı, alıntıyla doğrulanmış etkinlik tarihi kullanılabilir; gün hassasiyeti saat sırası sayılmaz. Karşılaştırılabilir tarih aralıkları önerilen yönün tersini kesin gösteriyorsa supersede reddedilir; çelişen kanıt owner sorusudur.
- **Eşit/bilinmeyen zaman:** “new kazanır” yok. Alıntıyla desteklenen açık değiştirme/iptal ilişkisi ve bağımsız verifier anlaşması varsa yön kabul edilebilir; aksi halde yalnız contradiction/abstain. Phase 5’te ortak recorded_at bu nedenle engel değildir.
- **Verifier:** primary sonucunu görmeden aynı subject/scope, çatışma, yön ve whole/part kapsamını değerlendirsin; A/B eşlemesi açık ve sıralamadan bağımsız olsun. Şema, referans, alıntı ve sürüm guard’ları zorunlu; yön uyuşmazlığında supersede üretilmez, otomatik yön çevrilmez.
- **Close zamanı:** güvenilir etkinlik tarihi geçerli aralık içindeyse onu kullan; aksi halde owner’ın onayladığı “şimdi kapat” zamanını kaydet, tarihsel başlangıç uydurma. `:646` eşit-tarih engelini bu açık politika ile değiştir. Close+link atomik, sürüm kontrollü ve replay edilebilir olmalı; geri alma eski olayı silmeden telafi olayıyla yapılmalı.

## Read side
- **Hayır:** `APPLIED + scope=whole` koşulu yanlış linkte nötrlük sağlamaz; C’yi S’nin üstüne eklemek S’yi veya başka bir gold’u top-3/token bütçesinden çıkarabilir.
- Güvenli deney biçimi: C’yi **ayrı ek-kanıt alanında**, temel hit sırasına ve bütçesine dokunmadan göster; güncel erişim, query scope/as-of, sürüm, aktif link ve C’nin geçerliliğini yeniden kontrol et; tek hop, dedupe ve döngü sınırı uygula.
- Yanlış linkte **ranking nötrlüğünün önkoşulu**, temel hitlerin sıra/içerik/bütçesinin değişmemesidir; bu biçimin stale-first kazancı **0**’dır. Owner onayı veya query-span eşleşmesi tek başına semantik nötrlük garantisi değildir.
- Whole-scope doğrulanınca kalıcı çözüm owner-approved **validity close** olsun; demotion bunu gizlice taklit etmesin. Observer’da kabul `accepted_pending` kalır; partial ve contradicts-only whole-item close üretmez; widen_scope daima owner-applied kalır.

## Measurement
- Her aday/çift için: kimlik+sürüm, seçim kaynağı/rank/skor, elenme nedeni; temporal değer+tür+hassasiyet+alıntı; primary ham yapılandırılmış çıktı; guard öncesi/sonrası; verifier istendi/atlanma nedeni, cevabı/anlaşması; final proposal, approval/apply/close durumu ve ret nedeni.
- `judged_none`, eksik sonuç, schema/budget/timeout, `time_rejected`, `direction_disputed`, `verifier_rejected` ayrı terminal sonuçlar olsun; prompt/schema/profile/code/corpus hash’leri ve cassette anahtarları export edilsin, sırlar maskelensin.
- Query bazında baseline/final rank, top-3, bütçe kesilmesi, kaldırılan/eklenen hit ve neden; proposal türü/scope bazında kör strict precision, false-close ve güven aralıkları; çağrı/token/maliyet, p50/p95 ve jobs/min ölçülsün.
- Ablasyon: instrumentation → temporal/verifier → close → hedefli aday → 24/8. Pinlenmiş legacy eval ile D-072’ye uygun import replay’i ayrı raporla; tarih düzeltmesiyle eski baseline’ı sessizce değiştirme, tartışmalı gold’u bağımsız adjudicate et.
- Gates: **A stale-first ≤4/15; temporal L2 ≥.400; G-E-W2b ≥+3 puan, hiçbir kategori <−3 puan, stale-claim artışı yok**; multi-fact must-not-regress, observer sıfır mutasyon, owner-close, erişim ve replay testleri geçmeli. D-076 ve mevcut promotion precision/false-invalidation eşikleri aynen kalmalı.

## Risks
- **Maliyet:** `write_review.py:61/:462` sekizli seri çağrı yapıyor; dolu listelerde 16→37 aday, relate **2→5 çağrı/subject**; yalnız yerel listelerde 11→32, **2→4**. Verifier ve token yükü de artar; kesin dolar/gecikme çarpanı ölçülmeden verilemez.
- Aynı path veya başlık kanıt değil aday sinyalidir; büyük dosya ve genel başlıklar patlama yaratır. Bölüm/claim kimliği, ayrı kota ve dedupe kullan; mevcut adayları yerinden etme.
- Guard düzeltmesi tek başına kazanç yaratmaz: erişim, doğru yön, whole kapsam ve uygulanmış close zincirinin tamamı gerekir; levers’ın tavanları toplanamaz.
- Close geri alınabilir ama yanlış tarih geçmiş sorguları bozar; eşit import tarihleri için yalnız karşılaştırmayı gevşetmek yeterli değildir.
- Sağlanan teşhis ve kod incelendi; yeni hold-out veya performans koşusu yapılmadı.