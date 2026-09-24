## Recommendation

- **Karar:** Önce ≤1 günlük atomik-L1 tavan deneyi; geçerse **A → C → B**. D yalnız kalan ranking açığı için; üretim bu sırada E/observer kalır.
- **Sıralama:** kazanç `A > B > D > C > E`; düşük risk `E > C > A > B > D`; araştırma uyumu `A > B > D > C > E`.
- **0 — Shadow pilot, 0.5–1 gün:** doğru atomlar ve oracle-close ile beklenen tavan **8→0–4**; gerçekleşmezse A’yı durdur.
- **1 — A/atomik L1, 7–12 gün:** mevcut judgement zinciriyle temkinli beklenti **8→5–7 (Δ−1…−3)**; yön kanıtı düzelirse hedef 8→≤4.
- **2 — C/residual clue, 2–3 gün:** raw stale-first **Δ0**; doğru applied link bulunan vakalarda served-error için ek **Δ−1…−2**. Bugünkü hedef sekiz çiftte link olmadığından tek başına kazancı sıfırdır.
- **3 — B/owner-approved reconsolidation, 4–7 gün:** bölünemeyen episode/procedure kalıntılarında yaklaşık **Δ−1…−2**; otomatik whole-item rewrite yapılmamalı.
- **4 — D/targeted retrieval, 3–5 gün:** yalnız A’nın oracle tavanı >4 ise; beklenen **Δ−1…−3**, fakat nötrlük riski en yüksek seçenektir.
- Doğru granülerlik cümle değil, bağımsız doğruluk değeri taşıyan **claim block**’tur; bullet ancak bağımsızsa atomdur. Koşul, olumsuzluk, istisna, birim ve gerekçe korunur.
- Deterministik parser karar satırı/başlık/bağımsız kuralı böler; D‑082 tarzı client LLM `claim + verbatim span + stable_id` önerir; sunucu span/identifier/negation doğrular. Librarian yalnız legacy backfill önerir.
- Reversible migration: orijinal parent yetkili provenance/L0 olarak kalır; çocuklar pinned `derived_from` ve splitter sürümü taşır. Coverage audit sonrası parent L1 retrieval’dan bayrakla çıkar; rollback çocukları kapatıp parent’ı geri açar.
- Aynı token bütçesi korunur. Kısa atomlar faydalı, fakat sibling crowding ve index/job sayısı büyür; hard source-cap varsayılmaz. G3 `.980`’dan >1 puan düşmemeli, G4/G‑L3 p95 ≤500 ms kalmalı.

## Why not the others

- **B:** Bir claim değişince tüm belgenin validity’sini yeniler; hâlâ geçerli claim’leri tarihsizleştirir ve LLM rewrite kayıp/uydurma riski taşır.
- **C:** Applied statement-level link olmadan hiçbir şey yapmaz; gate’i kozmetik olarak kolaylaştırmamalı.
- **D:** Zaman kanıtı kıtken recency doğruluk değildir; rewrite L2’yi `.40→.60` yükseltip stale-first’i hiç değiştirmedi.
- **E:** Güvenli üretim duruşudur, fakat “memory keeps itself current” vaadini veya assistant terfisini karşılamaz.

## Gate

- Mevcut **raw stale-first ≤4/15** aynen kalmalı; bu index hijyenini ölçüyor. C için ayrıca owner-onaylı `served stale error ≤4/15` eklenmeli.
- Aynı 15 sorgu, aynı top-3 ve aynı 3000-token serialized payload kullanılır.
- Hata: current değer yoksa, stale span claim-specific `superseded` etiketi taşımıyorsa veya kör cevap hakemi güncel cevabı çıkaramıyorsa.
- Raw exposure 8/15’i aşamaz; tarihsel sorguya bugünkü clue eklemek hatadır; false-current-clue ≤.02 ve %95 üst sınır ≤.05 olmalıdır.

## Falsification experiment

- Splitter, hold-out soruları görülmeden dondurulur; parent metni verbatim korunur.
- Disposable A/B üzerinde `baseline`, `atomic+v3` ve `atomic+oracle-close` çalıştırılır.
- Oracle yalnız etiketli stale child’ı kapatır; bu sonuç kalite iddiası değil mimari tavandır.
- Oracle A’da ≤4’e inmezse A falsified; doğrudan D denenir.
- Oracle geçer ama v3 ≥2 vaka düzeltmez veya 0 doğru close üretirse full rollout yapılmaz: darboğaz granülerlik değil direction/judgement’tır.
- Kör split auditinde uydurma/negation kaybı veya G3 >1 puan düşüşü ya da G4/G‑L3 >500 ms varsa pilot başarısızdır.

## Phase 5 impact

- Mechanical import öncesine `atomicize --plan` diff’i ve reversible shadow projection eklenir; önce HLMemo, sonra corpus A.
- Observer split/link/reconsolidation önerir; atomicize ve close autonomous sınıfa alınmaz.
- Split precision/coverage ayrı kohortta ölçülür; mevcut `.175` precision ve raw gate kapanmadan assistant terfisi yoktur.
- W3a atomları özetler, W3b child düzeyinde çalışır; W3c parent provenance ve canlı `derived_from` zincirini arşivleyemez.