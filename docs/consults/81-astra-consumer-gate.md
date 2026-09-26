**Karar: FIX-NEEDED.** D-158 tüketiciye yaklaşmış, ancak aynı kişinin uygulayıcı, kapı tasarımcısı ve değerlendirici olması hâlâ sonucu esnetmeye açık.

**1. Geçiş kapısının aşılabilirliği**
- **Rater hoşgörüsü:** Yeni Claude oturumu ve gizlenmiş sistem adları yararlı; fakat gold’u gören rater eksikleri zihninde tamamlayabilir. Gold’u görmeyen tüketici yanıtla görevi denesin; ayrı değerlendirici doğruluğu gold ile kontrol etsin. Rubrik/model sabitlensin, sınır vakaları bağımsız incelensin.
- **Hayalî “tek drill”:** Kaynağın kalan cevabı içerdiğini varsayarak U=1 vermek kolaydır. Gerçek handle ile tek drill çalıştırılsın; gerekli bilgi ancak o çağrıda bulunursa puan verilsin. Drill süresi ve tokenları da maliyete katılsın.
- **Zayıf referans + −.05:** NotebookLM kötü sonuç verirse düşük kaliteye izin çıkar; C1’de mutlak fayda tabanı yoktur. Önerim: mevcut U=0 sınırına ek olarak **cevaplanabilir sorularda ortalama U≥1.6**; referans karşılaştırması ayrıca geçsin.
- **Belirsiz “parite”:** −.05 eşitlik değil, izin verilen gerilemedir; C1’in 0–2 ölçeğinde ve C2’nin oran ölçeğinde farklı anlam taşır. Ölçekleri açıkça sabitleyin; eşleştirilmiş farkın önceden belirlenen güven aralığı −.05’i dışlamadan “eşdeğer” demeyin.
- **Referans korpusu:** D-158 NotebookLM için B korpusunu tarif ederken PR güncel ağaçta çalışıyor. PR ve B için ayrı, aynı içerikli ve hash’lenmiş referans kopyaları oluşturun; eski/eksik referans ürünün barını düşürmesin.
- **Geniş kaynak kapsamı:** Kaynak metnine göre sadakat ölçmek meşrudur; 3×3200 karakter içinde sözcüklerin bulunması iddianın desteklendiği anlamına gelmez. Her atomik iddia için erişilebilir citation, doğru özne/koşul/zaman ve gerçek entailment arayın; D-156’daki atıfsız cümleyi tüm excerpt’lerle doğrulama kaçışını kaldırın.
- **Sadakat indirimi:** D-150’nin eski quote sözleşmesindeki küçük hata denetimi, yeni source kapsamındaki .92’yi “gerçekte ≥.94” yapmaz. Yeni kapsamda kabul ve ret örneklerini kör denetleyin; bu yapılana kadar **.95’i koruyun**.
- **Payda oyunu:** İddia bazında çelişki oranı, çok sayıda zararsız cümleyle sulandırılabilir. “En az bir yanlış eylem yönlendiren iddia içeren cevap” oranını da ölçün; kritik yanlışları ayrı başarısızlık sayın.
- **Alt kümeler ve hold-out:** Sabit dev alt kümesi tek başına hile değildir; hold-out’un korunması güçlüdür. Ancak D-153’te judge hatasıyla soru çıkarılması emsal oluşturuyor: tüm final soruları paydada kalsın, judge hataları bağımsız karara gitsin; PR ve B **ayrı ayrı** geçsin.
- **Tekrar deneyerek geçme:** Kod, ayarlar, rubric, korpus, soru listeleri ve tekrar sayısı sonuçlardan önce dondurulsun. En iyi koşu seçilmesin; başarısız finalden sonra değişiklik yapılırsa yeni hold-out kullanılsın.

**2. Eksik tüketici ihtiyaçları**
- **Zamansal doğruluk:** Eski kaynağa sadakat, bugünün doğru cevabı değildir. Güncel/eski değer, yürürlük tarihi, supersession ve çözülmemiş çelişki örnekleri ayrı test edilmeli; belirsizlik açıkça söylenmelidir.
- **Gecikme ve hata kuyruğu:** p95 zaten kuyruğu kısmen ölçer; tek küçük koşu yeterli değildir. Soğuk/sıcak başlangıç, eşzamanlı yük ve fallback altında tekrarlar; timeout/hata oranı, p99 ve toplam answer+drill süresi raporlanmalıdır.
- **Kullanılabilir kanıt:** Handle gerçekten açılmalı, doğru sürüme gitmeli; ana cevap ile ek bağlam ayrılmalı. Medyan token sınırı uzun cevapların yarısını gizleyebilir: toplam tüketim için p95 de ölçülmelidir.
- **Kapsam güvenliği:** D-130’un **sıfır proje/cihaz kapsamı sızıntısı** şartı D-158 tablosunda kaybolmuş; açık release şartı olarak geri konmalıdır.

**3. C2: .80 → .70**
D-155’teki .84, 25 soruda **21 başarıdır**; ispatlanmış model veya judge tavanı değildir. .80 aynı örneklemde 20 başarı demektir. Ayrıca mevcut seçilmiş kaynaklarla oracle .76 üretmiştir.
Kapıyı tüketici ihtiyaçlarına göre revize etmek yetki içindedir; fakat bu sonuçlar .70’i gerekçelendirmez. Önceki başarısızlıklar görüldüğünden “yeni koşudan önce kayıt” geçmiş sonuçlardan bağımsızlık sağlamaz.
**Önerim C2≥.80 ve ayrıca NotebookLM−.05 şartını korumak.** Gold/judge kusurları bağımsız doğrulanırsa ölçümü düzeltip iki sistemi yeniden puanlayın.

**4. En önemli beş değişiklik**
1. Gold’dan habersiz gerçek tüketici denemesi, çalıştırılan tek drill ve mutlak C1 tabanı.
2. C2=.80; C3 indirimi için yeni kapsamda bağımsız kalibrasyon.
3. İddia düzeyinde erişilebilir kanıt, zamansal doğruluk ve cevap düzeyinde kritik yanlış kontrolü.
4. Eş korpuslar, dondurulmuş bağımsız final protokolü, ayrı PR/B geçişi ve belirsizlik hesabı.
5. Sıfır kapsam sızıntısı şartı; yük/fallback altında hata ve toplam answer+drill gecikmesi ölçümü.