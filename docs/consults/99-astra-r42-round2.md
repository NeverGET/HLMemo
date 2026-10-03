## Verdict

**NO-GO** — Astra-low ve Sol-xhigh incelemeleri birleştirildi. İki F1 HIGH bulgusu ve iki MEDIUM açıklık kaldı.

1. **HIGH — Query başlangıcı genişletmeden önce siliniyor — `src/hlmemo/librarian/tasks/research.py:2115`.**  
   Kaynak: ``Use `?filter=/a|b` exactly.``  
   Cevap: ``Use `filter=/b`.`` → `_prose_keep`: **kept**; F1 genişletmesi kapalıyken **dropped**.  
   `_NOTATION_EDGE` baştaki `?` işaretini kaldırarak query içeriğini path saydırıyor.  
   **Minimal fix:** query/fragment sınırını kırpmadan önce belirle; baştaki `?`/`#` işaretlerini koru.

2. **HIGH — Nested/unbalanced bracket koruması alternation yolunda yok — `src/hlmemo/librarian/tasks/research.py:2084`, `:2095`.**  
   Kaynak: ``Use `/foo[bar[baz]]/a|b` exactly.``  
   Cevap: ``Use `/foo[bar[baz]]/b`.`` → **kept**; F1 kapalıyken **dropped**. Dengesiz parantezli biçim de aynı korumayı aşabiliyor.  
   **Minimal fix:** `_brackets_flat(token)` başarısızsa bütün genişletme yollarından önce `[]` döndür.

3. **MEDIUM — Ortak fiil listesi Türkçe isimleri yanlış sınıflandırıyor — `src/hlmemo/librarian/tasks/research.py:2555`, `:2604`.**  
   Kaynak `Hat devre dışı kalmalıdır.`; düşürülen bir cümlenin ardından `Bu hat devre dışı kalmalıdır.` da düşüyor: Türkçe **hat**, Almanca yardımcı fiil **hat** ile eşleşiyor. Karşıörnek çalıştırıldı.  
   Ters yönde `Bu çalışır.`, `O silindi.`, `Diese funktionieren zuverlässig.` gibi dangling başlangıçlar kaçıyor.  
   **Minimal fix:** başlangıç sözcüğüne/dile özgü fiil kümeleri kullan; EN/DE fiillerini `bu/şu/o` sonrasında uygulama.

4. **MEDIUM — Preview filtresi belgelenmiş provenance biçiminden geniş — `src/hlmemo/librarian/tasks/research.py:886`.**  
   `VERIFIED 2026-01-01 backups are corrupt: do not restore.` satırı siliniyor. Tarihten sonra herhangi 80 karaktere izin veriliyor.  
   **Minimal fix:** yalnız `VERIFIED <date>:` ve belgelenmiş parantezli modifier biçimini kabul et.

**Round-1 kapanışları** — A=Astra, S=Sol:

| Bulgular | Durum | Kanıt |
|---|---|---|
| A1, A2; S1 — F1 | **PARTIAL** | Eski örnekler düzeldi; yukarıdaki #1–2 açık (`research.py:2084,2115`). |
| A3; S2 — restate | **CLOSED** | Restate kaldırıldı; job kayıtları `research.py:115,129`. |
| S3 — MAIN_REPAIR manifest | **CLOSED** | Ayar ve release-env işlemleri kaldırıldı; `deploy/` main ile aynı. |
| A4; S6 — dangling | **PARTIAL** | Verbatim koruması var (`research.py:2726`); #3 kaldı. |
| A5; S7 — preview | **PARTIAL** | Bare `VERIFIED` düzeldi; #4 kaldı. |
| A6; S4, S5 — pencere/cap/citation | **CLOSED** | `research_service.py:389–400`: yalnız 0→1 genişletmesi, cap öncesinde; citation chunk 1’in kendi ±1 penceresi. |
| S8 — prompt/schema | **CLOSED** | Prompt/schema dosyaları main ile byte-identical. |

**B korunmuş:** provider, profiles, errors, app, ilgili testler ve Gemini profilleri `a8ec1b7` ile aynı. Research tarafındaki writer/503 kodunda da değişiklik yok; strict policy, settle-once ve startup doğrulaması korunuyor.

**Prod `fda8fa0`:** migration, Compose, Dockerfile veya deploy-script değişikliği yok. Yeni policy image içindeki profillerden okunuyor; mevcut `llm.env` için yeni anahtar gerekmiyor. Rollback önceki image/env çiftine ve önceki worst-case 5xx muhasebesine döner. Bunlar statik olarak doğrulandı; prod başlangıcı ve rollback tatbikatı çalıştırılmadı.

**Main’in bilinen literal zayıflıkları**, değişmeden kaldıkları için tek başına R4.2’yi engellememeli. Buradaki HIGH’lar yeni F1 genişletme yolundan geliyor.

**Doğrulama:** 44 R4.2 birim testi geçti; karşıörnekler ayrıca çalıştırıldı; `git diff --check` temiz. Geniş koşuda 204 test geçti, 114 setup hatası kaldı; Docker/tmp kısıtları nedeniyle tam gate doğrulanmadı. Dosyalar değiştirilmedi. İkinci ve son tur tamamlandı; kalan risk kararı owner’a ait.