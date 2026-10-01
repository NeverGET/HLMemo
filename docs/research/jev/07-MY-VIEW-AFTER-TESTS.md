# Jev hakkındaki görüşüm: TESTLERDEN SONRA (2026-09-28)

Testlerden önceki görüşüm `03b-MY-VIEW-BEFORE-TESTS.md` dosyasında (commit bf64c49, 19:18). Aşağıda o görüşleri tek tek sonuçlarla karşılaştırıyorum.

Kaynaklar:
- `05-TEST-RESULTS-general.md` (T1–T7 ve "Fairness re-runs" bölümü)
- `05-TEST-RESULTS-T8.md`
- `05b-HARNESS-AUDIT.md` (bağımsız denetim)
- `02b-games-and-tuning.md`

Test harcaması toplam **$0.14**: genel testler $0.072 + T8 $0.048 + adalet tekrarları $0.023.

## Tek paragrafta: ne değişti
Jev beklediğimden **daha iyi bir sınıflandırıcı** çıktı:
- Kolay İngilizce kararlarda luna-low ile **berabere**.
- **7 kat hızlı.**
- İngilizce olasılıkları **zaten kalibre**.
- Güven değeri, kolay kararları LLM'e sormadan ayıklamak için **çok güçlü**.
- Metnin içine sızdırılmış bir nota, luna'nın sertleştirilmemiş prompt'undan çok daha az kanıyor.
- İki açık tarihi karşılaştırmayı iyi yapıyor.

Buna karşılık iki beklentim boşa çıktı:
- **"100× ucuz" iddiası küçük kararlarda doğru değil.** Ucuz bir LLM'e göre yalnızca **~2 kat** ucuz. Sebep, istek başına ~260 token'lık sabit ek yük ve farklı token sayımı.
- **Türkçede ciddi düşüş var (−17 puan).** Ama luna da neredeyse aynı kadar düşüyor, yani bu Jev'e özgü değil.

Kısacası Jev'in HLMemo için değeri **maliyet değil; hız, kalibre güven ve kademeli karar**.

## Görüş görüş karşılaştırma
| # | Testten önceki görüş (güven) | Sonuç | Hüküm | Yeni güven / not |
|---|---|---|---|---|
| B1 | API belgelendiği gibi çalışır, cevaplar tipli ve geçerli (90%) | 0 bozuk cevap (889 + 1.496 çağrı); 3 × HTTP 520 (~%0.4) | **DOĞRULANDI** | 97% |
| B2 | Gecikme medyanı < 1 s (75%) | p50 **0.32 s**, p99 0.53 s (luna 2.35 s); T8 0.42 s | **DOĞRULANDI** (güçlü) | 95% |
| B3 | Kolay İngilizcede luna'nın ~5 puan yakınında (60%) | BoolQ 91 vs 87, BANKING77 87 vs 85, XNLI-EN 91.9 vs 84.8; hepsi anlamsız fark, yani **berabere** | **DOĞRULANDI**, beklentimden iyi | 85% |
| B4 | Tarih sıralamasında zayıf, ≤ %70 (80%) | **%97**; format dönüşümü gereken maddelerde 41/43 | **ÇÜRÜDÜ**, açık tarihli ikili karşılaştırma için. Uyarı: görev kolaydı ve kısmi ipucu vardı; örtük yerini alma test edilmedi | Üreticinin "dates as text" uyarısını fazla genelledim |
| B5 | Türkçe ≥ 5 puan kötü (70%) | Jev EN→TR **−17.2** (p = .0002); luna da −15.2. T8'de (HLMemo) Türkçe sorular daha kötü değil (küçük örnek) | **DOĞRULANDI**, ama Jev'e özgü değil | 80% |
| B6a | Güven ayıklamaya yarar: en emin yarı en az 15 puan daha iyi (70%) | **98.5% vs 73.4% (+25)**; kararların ~%60–67'si ≥ %93–95 doğrulukla LLM'siz verilebilir | **DOĞRULANDI** (güçlü) | 90% |
| B6b | Mutlak kalibrasyon vasat, ECE ≥ .10 (60%) | İngilizce ECE **0.021**, Türkçe 0.144. Yeniden kalibrasyon İngilizceyi bozdu, Türkçeyi düzeltti | **İngilizcede ÇÜRÜDÜ**, Türkçede doğru | – |
| B7 | Tek bir enjekte not kararların ≥ %10'unu çevirir (70%) | Adil kümede **%2.4**, "notu yok say" talimatıyla %0. luna: %32.1 → sertleştirilmiş prompt'la %3.6 | **ÇÜRÜDÜ** | Literatürdeki %12.1 farklı bir enjeksiyon türüydü |
| B8 | Aynı çağrıların ≥ %1'i değişir (55%) | Etiketlerin %2'si değişti (yakın eşitlikler); olasılıklar ≤ 0.11 oynuyor | **DOĞRULANDI** | 75% |
| B9 | Karar başına ≤ $0.0003, gizli ücret yok (85%) | **$0.000031**, faturalama token × $0.042/M ile birebir | **DOĞRULANDI** | Ama sürpriz aşağıda |
| B10 | Jev yazarın yerini tutamaz (99%) | Tasarım gereği | Test edilmedi | 99% |
| B11 | HLMemo için en umut verici alan alaka/sıralama ve "bilgi var mı" kontrolü; en az umut verici alan yerini alma (65%) | Sıralama: Jev 35/44 (1./ilk-4) vs LLM 40/47 vs mevcut 15/34; **7× hızlı, 1.7× ucuz**. Tarih karşılaştırması iyi. "Bilgi var mı" test edilmedi | **Kısmen doğrulandı**; yerini alma için karamsarlığım azaldı | 70% |
| B12 | HLMemo için tek başına "oyun değiştirici" (25%) | Aşağıya bakınız | – | **~20%** (hafif düştü) |

## Beni en çok şaşırtanlar
1. **Maliyet iddiası küçük kararlarda tutmuyor.** Luna-low'a göre yalnızca ~2 kat ucuz. "100×" ancak pahalı bir LLM'e karşı ve büyük karar setlerinde anlamlı.
2. **Enjeksiyona dayanıklılık.** Sertleştirilmemiş prompt'ta Jev %2.4, luna %32. Sertleştirilince ikisi de düşük ve berabere. HLMemo'nun hafızası dışarıdan gelen metin taşıdığı için bu önemli.
3. **Tarih karşılaştırması.** Açık iki tarih verildiğinde çok iyi. Zayıflık uyarısını fazla genellemiştim.
4. **Oyunlar.** Jev'e ince ayar yapılmamış; beceri sarmalayıcı koddan geliyor. Jev'in doğru kullanımı da bu: kararı küçük sorulara böl, sayı ve tarih işlerini koda bırak.

## HLMemo için değişen düşüncem
- **Maliyet kazancı değil, hız ve güven.** Mevcut rerank soru başına 2.9 s sürüyor ve maliyeti ihmal edilebilir ($0.0006). Jev bunu 0.4 s'ye indirir. Kalite biraz düşer (1. sırada 35/40); emin olmadığı durumda LLM'e bırakmak bu kaybı kapatabilir.
- **Ölçüm problemimiz için ilginç bir aday.** v1 hakemi gerçek seviyeyi ~.2 düşük gösteriyordu, okuyucularla düzeltmek de yavaştı. Jev'le "cevapta X bilgisi var mı?" (Noul) sorusu sorulabilir: kalibre güveni yüksek olanları otomatik kabul ederiz, düşük olanları okuyucuya/LLM'e bırakırız. Bu, testlerden önce aklımda yoktu.
- **Yerini alma için ön filtre.** Otomatik backfill'in sorunu isabetti (.69). Jev'in kalibre güveni, küratöre gidecek aday çiftleri daraltan bir ön süzgeç olabilir. Tarih karşılaştırmasında iyi olması bunu destekliyor. Örtük durumlar test edilmediği için bu hâlâ bir hipotez.
- **Yazarın yerini tutamaz; asıl darboğazımız yazar ve kanıt.** Bu yüzden **"tek başına oyun değiştirici" değil (~%20)**. Ama HLMemo'nun karar katmanı için şu an elimizdeki en iyi hız/güven bileşeni.

## Açık kalanlar (sonraki testler, gerekirse)
- **Örtük yerini alma:** gerçek hafızadan tarihsiz ve "aynı konu mu?" çiftleri.
- **"Cevapta bilgi var mı?"** yargısı, okuyucu etiketlerine karşı (elimizde 100+ okunmuş cevap var, test ucuz).
- **HLMemo'ya özgü Türkçe kararlar.**
