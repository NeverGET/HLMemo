## Verdict (FIX-NEEDED)

Astra-low ve Sol-xhigh bulguları birleştirildi: gönderim ve guard tarafında açık HIGH kusurlar var. 160 hedefli test geçti; karşı örnekler ayrıca doğrulandı. 755/555 tam test, performans ve canlı kabul oranlarını yeniden doğrulayamadım. Dosyalar değiştirilmedi.

## Per-claim

1. **PARTIAL** — Eski credential örnekleri engelleniyor; Unicode isimli atama kaçıyor: `src/hlmemo/librarian/query_rewrite.py:111`.
2. **PARTIAL** — Eski regresyonlar ve aksansız TR/DE testleri geçti; yol, komut argümanı ve alıntı değişiklikleri hâlâ kabul ediliyor: `src/hlmemo/core/rewrite_guard.py:168,182,217,267`.
3. **OK** — Mock HTTP ile latency: primary→fallback **0.002 s**; background: primary iki deneme. Gerçek DB/per-task uçtan uca süre yeniden ölçülmedi: `src/hlmemo/librarian/provider.py:522,644`; `tests/integration/test_pivot_slice1.py:799`.
4. **OK** — AST/function kapsamı ve multiline regresyon testi geçti: `tests/unit/test_wf_task_fallbacks.py:392`.

## New findings

Komut örnekleri, dil algılayıcısının **TR** kabul ettiği sorgularda doğrulandı.

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH — D-102 | `src/hlmemo/librarian/query_rewrite.py:111` | `şirket=çilek kullanıyorum ama bağlantı kurulamıyor ve sürekli hata alıyorum, bunu nasıl düzeltebilirim?`: ham sorgu **1 mock HTTP çağrısına** ulaştı; DB izin/kota kontrolleri izinli stublandı. | Atama isimlerinde Unicode başlangıçlarını kapsa; sıfır-call regresyonu ekle. |
| HIGH — D-102/D-097 | `src/hlmemo/core/rewrite_guard.py:168,182` | `git diff /`→`git diff`; `git checkout --`→`git checkout` kabul ediliyor. Teknik noktalama tokenları kayboluyor. | `/`, `.`, `..`, `--` ve teknik ayraçları koru. |
| HIGH — D-102/D-097 | `src/hlmemo/core/rewrite_guard.py:207,217` | `git checkout üretim dalı`→`git checkout staging branch` kabul ediliyor; Türkçe argüman komut bağlamını bitiriyor. | Komut argümanını sözlükte bulunduğu için korumadan çıkarma. |
| HIGH — D-102/D-097 | `src/hlmemo/core/rewrite_guard.py:239,263,267` | `git branch`→`git branch release` kabul ediliyor. Ayrıca `git kelimesi ve log açıklaması neden farklı?`→`Why is the word different? git log` kabul: kopyalanan sözcükler komuta dönüşebiliyor. | Çıktının komut bağlamını da karşılaştır; sözcük-kopyalama istisnasının yeni argüman/komut oluşturmasını engelle. |
| MEDIUM — D-097 korunumu | `src/hlmemo/core/language.py:339`; `src/hlmemo/core/rewrite_guard.py:180` | `"a  b"`→`"a b"` kabul ediliyor; alıntılanmış içerik değişiyor. | Alıntı spanlarını boşlukları normalleştirmeden birebir doğrula. |

**Politika genişletmesi:** `p a r o l a hunter2`, `passw0rd hunter2`, `PIN 1234`, kart numarası ve çıplak `hunter2`, yeterli Türkçe bağlamda ham HTTP’ye ulaştı. Bunlar mevcut D-102 listesinin dışında; obfuscation, PIN/kart ve kısa sır kapsamı eklenmeli. `şıfre` ve `pwd` engelleniyor. E-posta/telefon filtreleri varsayılan kapalı; her base64 blobu mevcut entropy tanımına girmez (`config.py:239`; `redact.py:31,100`).

**Sözlük sınırı:** `worker`→`query` değişimi kabul ediliyor. Bu, D-102 tanıma politikasının sınırı; genel “identifier değişmez” garantisi verilemez. Anlamsal rol/negasyon tersine çevirmeleri de token sırası korunurken mümkün.

**Lisans:** Paketlemeyi engelleyen kusur görülmedi. README’deki atıf, kaynak/lisans bağlantıları ve değişiklik bildirimi dağıtımda korunmalı; uyarlanmış listeler CC-BY-SA kalmalı ve ek kısıt konmamalı. Bu, çevreleyen MIT kodunu otomatik olarak aynı lisansa taşımaz. [Kaynak lisansı](https://github.com/hermitdave/FrequencyWords#license), [CC-BY-SA koşulları](https://creativecommons.org/licenses/by-sa/4.0/).

Flag-off MCP byte-identity testi geçti. Diff incelemesinde lineage, kota, %20 pay, enqueue deadline ve demotion birleşiminde yeni regresyon görülmedi; DB sonuç eşitliği yeniden ölçülmedi.

## Ship-with-flag-ON (R3)

**No.** Mevcut D-102/D-097 kapsamındaki HIGH bulgular, D-099’un açık HIGH bulunmaması koşulunu ihlal ediyor.