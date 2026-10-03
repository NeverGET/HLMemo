## Verdict

**NO-GO** — Astra low ve Sol xhigh incelemeleri aynı iki bulguda birleşti.

1. **HIGH — Araç agent’lar tarafından çağrılabiliyor.**  
   `src/hlmemo/server/tools/__init__.py:78`; `src/hlmemo/librarian/questions.py:665`  
   `CLIENT_TOOLS` yalnızca ilanı gizliyor. Normal bir agent bearer’ı ve proje `READ` yetkisi, doğrudan `tools/call` için yeterli; owner-client kontrolü yok.  
   **Reproducer:** `AuthContext(9,"ci",False,1,{1:Role.READ},client="codex/1")` ile gerçek `on_call_tool → hlm_questions → review_list` zincirini çağırdım. Yalnızca DB yanıtları ve token ölçümü stub’landı. `{"name":"hlm.questions","arguments":{"project":"demo"}}` çağrısı **`is_error=False`** döndürdü.  
   **Minimal fix:** Agent kimlik bilgilerinden ayrılmış, sunucunun doğruladığı owner capability gerektir; `User-Agent`/`x-hlm-client` kontrolü yeterli değildir. Normal agent çağrısının reddedildiğini wire testiyle doğrula.

2. **MEDIUM — Değişen açık-soru kümesinde sayfalama soru atlıyor.**  
   `src/hlmemo/librarian/questions.py:692–695`  
   İlk sayfa `[1,2]` döndükten sonra soru 1 kapanır veya süresi dolarsa, `offset=2` ikinci sayfayı `[4]` ile başlatır; hâlâ açık soru 3 atlanır. Bu SQL davranışını bellek içi reproducer ile doğruladım.  
   **Minimal fix:** Devam sayfalarında `(created_at, question_id)` cursor’ı kullan; proje/kind filtrelerine bağla ve son **döndürülen** kayıttan devam et.

Görünürlük incelemesinde ek bir sızıntı gösterilemedi: iki subject için device/class scope ve okunabilir proje kontrolü mevcut; tarihsel sürümler `raw` üzerinden okunabiliyor. Gerçek DB üzerinde tam yetki matrisi çalıştırılmadı.

Deterministik request ID, aynı payload ile retry ve dry-run kontrolleri geçti. Toplam 7 mevcut test fonksiyonu ve ek retry kontrolü doğrudan çalıştırıldı; pytest geçici dosya oluşturma kısıtına takıldı.

G-SURF’un ilan edilen araç listesi değişmiyor; çağrılabilir yüzey genişliyor. Handler event/access-event yazmıyor; mevcut HTTP middleware’i `devices.last_seen_at` güncelleyebildiğinden uçtan uca “hiç state yazmaz” iddiası doğru değil.