```jsonl
{"k":1,"label":"S","evidence":"v68.0,v63.15","why":"Spec 0.1 değerini, D-026 ise uygulamanın 0.9 kullandığını açıkça belirtiyor."}
{"k":2,"label":"W","evidence":"v10","why":"Hit ve kart için kesin ipucu biçimleri aynı öğenin getirme algoritmasında, adım 11’de belirtiliyor."}
{"k":3,"label":"S","evidence":"v5.0,v63.1","why":"Her episode alımında LLM ile varlık ve kenar çıkarılması, LLM’siz Phase 0 ile açıkça çelişiyor."}
{"k":4,"label":"W","evidence":"v67","why":"Kesin zaman sınırları ve replay sözleşmesinin tamamı aynı öğenin 1.1 bölümünde bulunuyor."}
{"k":5,"label":"W","evidence":"v70,v63.9","why":"Bellek sistemlerinin ve kaynak biçimlerinin tam çeşitliliği, atıf yapılan öğenin diğer bölümlerinde açıklanıyor."}
{"k":6,"label":"E","evidence":"v65","why":"Query için cursor kullanılmayacağı başka öğedeki karar tablosunda açıkça belirtiliyor."}
{"k":7,"label":"E","evidence":"v69,v9.0,v63.3","why":"Olay ve kanıt koruma atıflarda mevcut; yeni archived sürümü oluşturma ayrıntısı v69’da bulunuyor."}
{"k":8,"label":"S","evidence":"v63.1,v5.0","why":"Düz kenar tablosu ve recursive CTE’lerin bu ölçekte 2–3 atlamayı karşılaması açıkça belirtiliyor."}
{"k":9,"label":"E","evidence":"v6,v63.1","why":"Adaptör, tek yetkili Postgres ve işletim maliyeti gerekçeleri v6’da; Phase 0–1 kapsamı atıfta belirtiliyor."}
{"k":10,"label":"S","evidence":"v63.42,v80.0","why":"D-050 önceki rol kararını açıkça değiştiriyor; Claude implementer, Sol eş-mimar ve reviewer olarak belirtiliyor."}
{"k":11,"label":"E","evidence":"v57","why":"Embedder içindeki lazy import öncesi ayarlama ayrıntısı v57’de açıkça belirtiliyor."}
{"k":12,"label":"S","evidence":"v60.0","why":"Başlık Sol’u tanımlıyor; çözüm sütunu üç talebi de açıkça içeriyor."}
{"k":13,"label":"W","evidence":"v70","why":"İmzalı AUDIT.md ve skor güncellemesiyle kapanış, aynı öğenin yedinci adımında belirtiliyor."}
{"k":14,"label":"S","evidence":"v63.3,v9.0","why":"Gölge çalışma, çokdilli testler, kanıt erişimi, Recall sınırı ve sonlu testlerin yetersizliği açıkça belirtiliyor."}
{"k":15,"label":"E","evidence":"v63,v9","why":"Üç günlük döngüyle geri alınabilir arşivleme bu öğelerde; atıf yapılan parça eski DROP tasarımını anlatıyor."}
{"k":16,"label":"S","evidence":"v80.0,v63.42","why":"STATUS aktif hedef bölümünde Claude subagent’larını, D-050 ise Opus 5.5 modelini ve güncel rolü doğruluyor."}
{"k":17,"label":"W","evidence":"v60","why":"Anonim istemcilerin pending cihaz oluşturabilmesi, aynı öğenin Answers bölümünde açıkça belirtiliyor."}
{"k":18,"label":"S","evidence":"v63.38","why":"uid 10001 ile çalışan worker’ın entrypoint dosyasını okuyamaması ve crash loop doğrudan belirtiliyor."}
{"k":19,"label":"E","evidence":"v8,v63","why":"Belleği taze tutma, kaynakları silmeme ve arşivlenmiş bilgiye erişim başka öğelerde birlikte açıklanıyor."}
{"k":20,"label":"S","evidence":"v60.0","why":"Başlık Sol’u tanımlıyor; ilk bulgu kayıt varsayılanının open olduğunu açıkça söylüyor."}
{"k":21,"label":"S","evidence":"v63.40","why":"D-046, sahibin kabul ettiği üretim hedefini Hostinger olarak belirtiyor."}
{"k":22,"label":"S","evidence":"v66.5","why":"Worker crash/restart testi için make test-o2 doğrudan veriliyor."}
{"k":23,"label":"P","evidence":"v4.2,v66","why":"1.1.4 atıfta mevcut; belgenin geride kaldığı sonucu atıflarda belirtilmeyen güncel sürümle karşılaştırma gerektiriyor."}
{"k":24,"label":"E","evidence":"v65,v10,v37.1","why":"Query için omitted ve cursor yasağı v65’te; omitted:int v10’da, imzalı okuma cursor’ları v37.1’de belirtiliyor."}
{"k":25,"label":"S","evidence":"v63.1","why":"D-007 yeniden değerlendirme eşiklerini >1M kenar veya >3 atlama olarak açıkça belirtiyor."}
```

Dağılım: S=12, W=5, E=7, P=1, U=0, C=0.
Başlıca sorun yanlış bilgi değil, desteğin atıf yapılan parçanın veya öğenin dışında kalması.
Başlıklar aktör atfını destekliyor; açıkça güncellenmiş kararlar eski rol ifadelerinin önüne geçiyor.
Yalnızca atıf yapılan parçaların tam desteği sayıldığında oran 12/25 = 0.48.