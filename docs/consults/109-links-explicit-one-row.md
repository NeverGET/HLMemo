**GO-WITH-FIXES**

- **HIGH — `src/hlmemo/core/explicit_supersession.py:316–317`: Karar olmayan öğe yanlış hedef veya bildiren satır olabiliyor.** Evet; `load_docs()` tür filtresi uygulamıyor, `decision_rows()` ise kaynak kimliğini doğrulamıyor.
  Somut hedef girdisi: `docs/example.md#0` gövdesi `D-002 | date | EXAMPLE | Example threshold 999.`; gerçek karar öğesi `D-006 | date | KABUL | Supersedes D-002.\nD-007 | date | KABUL | Other.`.
  Önce öneri yok; sonra gerçek karar → örnek doküman için `part` öneriliyor.
  Ters örnek: doküman gövdesi `D-006 | date | EXAMPLE | Supersedes D-002.`, gerçek karar öğesi `D-002 | date | KABUL | Old.\nD-003 | date | KABUL | Other.`.
  Önce öneri yok; sonra örnek doküman → gerçek karar öneriliyor. `part` hedefi gizlemese de yanlış metni eskimiş işaretliyor.
  Düzeltme: tek satır kabulünü doğrulanmış karar/importer kimliğine bağlayın; aynı koşulu hem indekslemede hem bildiren satır seçiminde kullanın.

- **HIGH — `src/hlmemo/core/explicit_supersession.py:316–317`: Kopya satır, önceden bulunan doğru bağlantıyı sessizce kaybettiriyor.**
  Somut girdi: yukarıdaki iki gerçek, ikişer satırlı karar öğesine `docs/notes.md#0` gövdesi olarak `D-002 | date | KABUL | Old.` ekleyin.
  Önce `D-006 → D-002` öneriliyor; sonra doküman kopyası da indekse girdiğinden D-002 belirsizleşiyor ve sonuç boş oluyor.
  Düzeltme: karar olmayan kopyaları D-id indeksinden dışlayın; bu regresyonu test edin.

Üç önce/sonra senaryosunu çalıştırarak doğruladım. Türkçe ek değişikliğinde ayrıca bulgu yok. Pytest, `HLM_TEST_DSN` tanımlı olmadığı için çalışmadı.