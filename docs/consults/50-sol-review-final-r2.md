## Verdict — NO-GO

Statik incelemede OBSERVER sınırı sağlam görünüyor; ancak Sol-49’un promotion engeli tam çözülmemiş.

## Per-blocker

- **Cross-project apply role check:** Düzeltildi. Apply yolu güncel olarak dokunulan her projenin rolünü denetliyor; OBSERVER projeye dokunan action bütünüyle erteleniyor.
- **Promotion stranding:** **Düzeltilmedi.** [roles.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/roles.py:137) promotion sırasında yalnızca soruda kayıtlı projeleri seçiyor. Sonradan bir action’ın güncel kapsamına giren C projesi OBSERVER ise cevap erteleniyor; C’nin promotion’ı soruyu seçemediği için `accepted_pending` cevap staleness kontrolüne gönderilmiyor.
- **0008 ruling:** D-075’te belgelenmiş; yayımlanmamış 0008 yerinde düzenlenmiş.
- **Stale heartbeat:** Düzeltildi. Deploy kontrolü eksik veya üç aralıktan eski heartbeat’i bekleme süresi sonunda reddediyor.

## Residual risks

- R2’nin yapılandırılmış OBSERVER rolünde librarian worker ve `memory.answer`, kullanıcı item’ı, link’i, validity’si veya scope’unu değiştiremiyor. Ops ve import burst’ün tetiklediği librarian işleri de bu sınırda kalıyor; importun kendi yetkili yazması ayrı.
- Librarian veya provider arızası sorgu/yazma erişimini düşürmemeli; librarian işleri gecikir. Bu hüküm **yalnızca statik incelemeye** dayanıyor.