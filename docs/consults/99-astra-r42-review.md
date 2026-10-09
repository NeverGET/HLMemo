## Verdict

**NO-GO** — Yeni F1 authority expansion regresyonu çalıştırılarak doğrulandı. Round-1 düzeltmelerinin
diğerleri kapanmış; B korunuyor.

1. **HIGH — F1 URL authority’sini path gibi genişletiyor — `src/hlmemo/librarian/tasks/research.py:2082,2099`.**
   Excerpt ``Use `https://a|b/path`.``; cevap ``Use `https://b/path`.`` → `_prose_keep` **kept**.
   `notation_hay=lambda x:x` ile **dropped**. `https://host[-evil]/a` da hostu genişletiyor.
   Minimal fix: scheme/authority değişmez; yalnız gerçek path genişlesin veya authority notation reddedilsin.

2. **LOW — Nested/unbalanced token reddi alternation’a uygulanmıyor — `research.py:2085,2099`.**
   `/foo[bar[baz]]/a|b` ve `/foo[bar/a|b` ikişer expansion döndürüyor.
   Minimal fix: bracket kontrolünü tüm expansion’dan önce uygula.

3. **Round-1:** query/fragment exact örneği CLOSED ama F1 yeni #1 yüzünden PARTIAL; arr/index ve eski nested
   identifier üretimi CLOSED; restate CLOSED by removal; bağımsız `This API` CLOSED; preview CLOSED;
   citation kapsamı CLOSED; MAIN_REPAIR manifest bulgusu obsolete/CLOSED; merge-before-cap CLOSED;
   prompt/schema audit sürümü CLOSED (main ile aynı).

4. **TR/DE ve listeler:** hedefli answer-quality testleri geçti; `.0→.1` reopen/cap ve 3000 rastgele
   collapse karşılaştırması geçti. `Bu çalışır.` / `Diese funktioniert.` mevcut semantik validator
   kapsamının dışında kalıyor; yeni main→R4.2 kabul yolu değil.

5. **Change-set B korunmuş.** `git diff a8ec1b7..HEAD` ilgili provider/profile/test dosyalarında boş;
   exact envelope, duplicate-key reddi, file-only policy, settle-once ve startup policy kontrolü mevcut.

6. **Prod `fda8fa0`:** migration yok; profiller image içinde; yeni env key gerekmiyor; rollback önceki image
   ve env snapshot’ını kullanıyor. Prod başlangıcı/rollback çalıştırılmadı.

7. **Main zayıflıkları:** belirtilen hyphen/nested/digit-less-URL davranışları regresyon değil; tek başına
   bu release’i bloklamaz. Yeni authority kabulü main’de reddedildiği için bu istisnaya girmez.

Kontroller: HEAD temiz; iki diff incelendi; `git diff --check` geçti; hedefli test ve reproducerlar çalıştı.
