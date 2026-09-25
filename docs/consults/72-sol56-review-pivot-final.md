## Verdict (DO-NOT-MERGE)

İki bağımsız D-085 incelemesi aynı kritik mask/restore, release-gate ve rollback kusurlarını doğruladı; D-099 kriter 3 sağlanmıyor. Mevcut 2.500 property ve 29 karşı-örnek testi geçse de aşağıdaki girdileri kapsamıyor. Bu exportta `tests/unit` sonucu 757 passed/20 skipped ve 777 collected; iddia edilen 778 yeniden üretilemedi. Docker/live metrikleri yeniden koşulmadı.

## Per-claim

1. **OPEN** — Apostrof kuyruğu ve çok satırlı/kapanmamış tırnaklar korumalı içeriği payload’a çıkarıyor; restore sınır ve tekrar garantisi sağlamıyor (`rewrite_guard.py:69-72,136-152,366-390`).
2. **OPEN** — Normal vakalar engelleniyor; ancak zero-width/RTL/homoglyph ve birleşik spaced+leet obfuscation sıfır-call kapısını aşıyor (`query_rewrite.py:115-128,166-199`).
3. **PARTIAL** — Erken admission ve varsayılan concurrency=4 doğru; pozitif fakat nedensel olmayan `queue_wait_s`, gerçek provider timeout’unu breaker’dan saklıyor (`query_rewrite.py:435-490`, `config.py:258`, `provider.py:739-747`).
4. **OK** — Varsayılan ağırlık 1.0 ve bu durumda eski collapse davranışı korunuyor (`config.py:259`, `retrieval.py:228-243`).
5. **PARTIAL** — Örnek/installer değerleri doğru; eksik `llm.env` rewrite kapalıyken PASS alıyor ve rollback R2 ortamını geri yüklemiyor (`check_librarian.py:217-225,283-314`, `rollback.sh:111-115,194-207`).
6. **OK** — Kota, %20 bütçe, lineage, enqueue deadline, demotion union, schema fallthrough ve flag-off kimliği kaynakta korunmuş (`query_rewrite.py:202-208,340-348,445-490,511-543,645-660`; `read_service.py:300-303`; `provider.py:665-671`; `test_pivot_surface.py:39-46`).

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH · in-scope | `rewrite_guard.py:145-148` | `hata'correcthorse`: sözlük prefix’i keyfî non-dictionary/secret kuyruğunu görünür bırakıyor; aynı açık output doğrulamasında da var | Yalnız tanımlı dilbilgisel ekleri kabul et; aksi halde token’ın tamamını maskele/reddet |
| HIGH · in-scope | `rewrite_guard.py:69-72` | Çok satırlı veya kapanmamış quote içinde sözlük kelimeleri payload’a giriyor | Stateful quote tarayıcı kullan; kapanmamış span’i EOF’a kadar maskele |
| HIGH · in-scope | `rewrite_guard.py:366-390` | `⟦P1⟧delete`, bitişik placeholder’lar veya `worker ⟦P1⟧` kabul edilip rol değiştirme/tekrar üretiyor | Placeholder’ları bağımsız token yap; korumalı span token’larının maskesiz output tekrarını reddet |
| HIGH · in-scope | `query_rewrite.py:125-128,166-173` | `pa<U+200B>rola merhaba`, Cyrillic homoglyph veya `p @ s s w 0 r d` send gate’i aşabiliyor | Default-ignorable/RTL temizliği, confusable politikası ve spacing+leet sabit-nokta normalizasyonu ekle |
| MEDIUM · in-scope | `provider.py:746-747` | `queue_wait_s=1e-9`, deadline-kısılmış gerçek provider timeout’unu breaker failure saymıyor | Yalnız gerçek queue wait nedensel olarak minimum deneme bütçesini düşürdüyse muaf tut |
| HIGH · in-scope | `check_librarian.py:283-309` | R3 cutover’da `llm.env` yok, rewrite false; kontrol yine PASS | R3 release marker/version ile env varlığını ve rewrite=true değerini zorunlu doğrula |
| HIGH · in-scope | `rollback.sh:111-115,194-207` | R2 compose güncel R3 `llm.env` ve R3-only profillerle başlatılıyor | `llm.env`yi release state’e dahil et; rollback öncesi R2 snapshot’ını atomik geri yükle |

## Ship-with-flag-ON (R3)

**No.** Payload gizliliği, restore bütünlüğü, breaker doğruluğu ve cutover/rollback güvenliği release-gating seviyesinde açık.