## Recommendation

1. **B’nin dar kapsamlı deneyi → A’nın kalıcı L1 modeli; C yardımcı seçenek.** D-103 sonrasında yeni bir pair-level v4 önermiyorum. Aşağıdaki kazançlar ölçüm değil, yön kanıtı veya açık owner kararı bulunmasına bağlı planlama hipotezleridir.
2. **Önce B:** serbest yeniden yazım yerine eski/yeni kaynak span’larına bağlı, owner-onaylı ifade değişikliği. A stale-first **8→4–6**, uygulama **2–4 gün**, ön deney **≤1 gün**. Değişmeyen ifadeler birebir korunmalı; revision, geçerlilik ve bağlantı atomik uygulanmalı; eski sürüm tarihsel erişimde kalmalı.
3. **Sonra A:** B deneyinin ardından claim temsili için ayrı ölçüm ve geçiş. B üzerine **0–2 ek vaka azalma**, **5–10 gün**; yalnız atomikleştirmeden garantili kazanç **0**. Kapsam sorunu çözülür, yön belirsizliği çözülmez.
4. **Granülerlik:** bağımsız doğrulanabilen ve ayrı geçerlilik taşıyan *iddia*; cümle/bullet yalnız sınır adayıdır. Özne, kapsam, koşul, olumsuzluk ve gerekli bağlam birlikte tutulmalı; prosedürler körlemesine parçalanmamalı.
5. **Kim böler:** deterministik parser kaynak sınırlarını çıkarır; client LLM yazarken claim önerir, librarian eksikleri asenkron tamamlar. D-082 burada örüntüdür, atomikleştirme için mevcut yetki değildir; kaynak desteği, kapsam ve anlam korunumu ayrıca doğrulanmalı.
6. **Reversible migration:** değişmez L0 parent + sürüme sabitlenmiş `derived_from`/span + kararlı claim kimlikleriyle eklemeli shadow backfill; mevcut geçmiş yeniden yazılmaz. Kapsama denetimi sonrası proje bazlı retrieval geçişi; geri dönüş eski indeksi seçer.
7. Parent ve children aynı kanıta çift ranking oyu vermemeli; parent provenance/drilldown’da kalmalı. Tarihler claim’e gerçekten uygulanıyorsa taşınmalı; import zamanı güncellik kanıtı olmaz. Bilinmeyen geçmiş yerine owner-onaylı “şimdi” kaydedilir.
8. **Bütçe:** 3000 token sabit; claim sayısı, embedding/yazma yükü ve bağlam kaybı ölçülür. Gold eşlemesi kaynak span’ları üzerinden korunur; G3/g3b/g3c gerilemez, G4 mevcut sınırda, G-L3 p95 ≤500 ms olmalıdır.
9. **Sıralama:** kısa vadeli kazanç/risk/emek dengesi **B > A > D > C > E**; araştırma uyumu **A > B > D > C > E**. A doğrudan §D.1’dir; B, §D.5’e uygundur fakat W3b’nin duplicate-merge kapsamını genişleten yeni karar gerektirir.

## Why not the others

- **A’yı hemen topluca:** yön kanıtı üretmez; retrieval granülerliği ve veri geçişi riskini faydayı görmeden büyütür.
- **B’yi tek kalıcı model:** mevcut sorunu hızla sınar, fakat çok ifadeli öğelerde farklı geçerlilik zamanlarını yönetme borcu bırakır.
- **C tek başına:** ham stale-first deltası **0**, yaklaşık **2–3 gün**; doğru clue cevabı iyileştirebilir, fakat APPLIED bağlantı ve sorguyla ilgili claim eşleşmesi gerekir.
- **D önce:** tahmini **0–3 vaka azalma**, **3–5 gün**; kanıtlı zaman kıtlığı ve topic benzerliğinin aynı iddia anlamına gelmemesi nedeniyle neutrality riski yüksek. W3a özetleri/W3c decay güncellik kanıtı değildir.
- **E kalıcı çözüm:** delta **0**, yaklaşık **0,5 gün** politika/raporlama; observer güvenli bekleme durumudur, “bellek kendini güncel tutar” hedefini karşılamaz.

## Gate

**G-E-TEMP ≤4/15 aynen kalsın.** Clue varsa ham stale-first cevap kalitesini tek başına anlatmaz; yine de eski kanıtın retrieval tarafından sunulduğunu doğru ölçer.
C için owner onayına sunulacak **ek** gate: aynı 15 soruda, yalnız gerçek top-3 payload ve clue’lar dahil aynı 3000-token bütçeyle, **≥11/15 eksiksiz güncel ve kaynakla destekli cevap**; yanlış eski iddia sayısı baseline’dan yüksek olmayacak.
Abstention, eksik cevap, yanlış/ilgisiz clue başarısız sayılır; baseline aynı cevaplayıcı ve kör değerlendirmeyle ölçülür. Clue ücretsiz ek bağlam değildir.
G-E-W2b **≥+3 puan**, kategori kaybı **≤3 puan**, stale-claim artmaması ve D-076 promotion koşulları korunur; doğru cevabı getirmeden eski öğeyi gizlemek yeterli başarı değildir.

## Falsification experiment

1. **≤1 gün:** sabit commit/config, prod-rule import, aynı rewrite durumu ve bütçeyle disposable A/B-dev kopyalarında B pilotu; sealed B açılmaz.
2. Soruları/gold’u görmeyen üretici, sabit aday kuralıyla partial-update adaylarını çıkarır; yalnız önceden APPLIED olanlarla sınırlamaz.
3. Kaynakları gören bağımsız değerlendirici span patch’lerini ve yönü denetler; onaylı doğru patch kolu yalnız **üst sınır**, gerçek öneri kolu ayrıca raporlanır.
4. Değişmeyen span’lar, tarihsel `valid_at/known_at`, replay ve reversal kontrol edilir; bilinmeyen yön abstain olur.
5. Baseline/B karşılaştırmasında mevcut 15 soru, tüm A/B-dev kategorileri, güncel kanıt erişimi ve ek cevap gate’i ölçülür.
6. **Üst sınır kolu bile >4/15 kalırsa B-first stratejisi çürür**; gold silinmesi, yeni stale hata veya kategori kaybı >3 puan da durdurur.
7. Üst sınır geçip gerçek öneriler kalırsa darboğaz temsil değil öneri/yön kalitesidir; tam migration başlatılmaz. Deney geçmesi release onayı değildir.

## Phase 5 impact

- Sıra değişmez: HLMemo → YouTube-Automation; her projede import öncesi truth set, shadow claim/patch denetimi ve rollback kontrolü eklenir.
- **Observer** önerir ve etiket toplar; owner kabulü observer içinde mutation yapmaz.
- **Assistant** ancak G-E-TEMP/G-E-W2b ve mevcut ≥2 proje/≥150 öneri, precision/false-invalidation eşikleri ile açık owner terfisi sonrası; patch’ler batch onaylıdır.
- **Autonomous** ayrı owner kararı ve mevcut ≥3 ek proje/≥300 öneri koşullarıyla; statement revision sınıfı ayrıca yeterlilik kazanmalıdır. Faz 3 geliştirmesi sürer, başarısız migration sonraki projeye ilerlemez.