## Verdict

**NO-GO.** Astra low + Sol xhigh incelemeleri birleştirildi. Yeni, yeniden üretilmiş bir HIGH var.

1. **HIGH — `src/hlmemo/librarian/tasks/research.py:2088`, `:2112` — Kabuk sözdizimi yanlış kanıta dönüşüyor.**  
   Kaynak: ``Run `cat /etc/passwd|wc` to count the password file.``  
   Yanıt: ``Read `/etc/wc`.``  
   `validate_prose`, kurallar kapalıyken yanıtı düşürüyor; `notation,url_literals` açıkken `how="expansion"`, yüksek güven ve kaynak atfıyla kabul ediyor. Kaynakta `/etc/wc` yok: `|wc` bir pipeline. Bağımsız regex örneği de aynı kusuru doğruluyor: ``The regex is `file[.txt]`.`` → ``The regex is `file.txt`.``  
   **Asgari düzeltme:** Release şablonunda `notation`ı kapatın. Yeniden açmadan önce yalnızca açıkça tanımlanmış notasyonları genişletin; pipeline ve regex örneklerini regresyon testlerine ekleyin.

2. **MEDIUM — `src/hlmemo/librarian/tasks/research.py:1879` — Eşdeğer kök URL reddediliyor.**  
   Kaynak: `Docs live at https://example.com today.`  
   Yanıt: `Docs live at https://example.com/.`  
   Kurallar kapalıyken tutuluyor; ship set açıkken `https://example.com/` desteklenmiyor sayılarak düşürülüyor. HTTPS adresinde boş path ile `/` aynı kök kaynağı belirtir; bu, sunulan auditte bulunmayan destekli bir iddia reddidir.  
   **Asgari düzeltme:** HTTP(S) kök adreslerinde boş path–`/` eşdeğerliğini dar kapsamlı normalleştirin; query/fragment ve diğer path farklarını koruyun.

3. **LOW — `eval/active/al_e4.py:551` — Verification satırının yalnızca semptom kısmı yeterli sayılıyor.**  
   Verification kanıtı: `Before the fix the service crashed; after the fix all tests passed.`  
   Lesson yalnızca `Before the fix the service crashed` alıntıladığında `check_lesson` yine `grounded_det=True`, `grounding_flags=[]` döndürüyor. `_quote_in` tam satır yerine alt dize kabul ediyor.  
   **Asgari düzeltme:** Verification kontrolünde tam satır eşitliği kullanın ve bu örneği test edin. Etki offline harness ile sınırlı.

Kontroller: 51 odaklı test geçti (`--noconftest`, DB kullanılmadan); yukarıdaki örnekler doğrudan çalıştırıldı. Diff incelemesinde migration, prompt/schema değişikliği, aktif cut-rule dalı veya ek scope/spend kusuru bulunmadı. Boş/eksik env kuralları kapatıyor; bilinmeyen ve kaldırılmış adlar hata veriyor. Template/fingerprint ve R4.2 template rollback akışı anahtarı taşıyor/kaldırıyor.

Sınır: Özel 287 trace replay’i ve Docker deploy/rollback provası bu arşivde yeniden çalıştırılmadı.