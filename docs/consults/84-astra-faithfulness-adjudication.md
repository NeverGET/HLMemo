```jsonl
{"k":1,"label":"P","evidence":"v89.2","why":"Bayrakların düşmesi belirtiliyor; normalde dışlanan testlerin çalıştığı sonucu kaynakta belirtilmiyor."}
{"k":2,"label":"S","evidence":"v63.0,v74.5","why":"İlk öneri, bölgeler ve donanım özellikleri açıkça belirtiliyor."}
{"k":3,"label":"S","evidence":"v63.15,v68.0","why":"D-026 uygulamada 0.9 kullanıldığını; şartname 0.1 değerini belirtiyor."}
{"k":4,"label":"P","evidence":"v89.2","why":"Bash altında çalıştırma belirtiliyor; zsh dizisi çözümü eklenmiş. Kaynak ayrı örnekte Bash dizisi öneriyor."}
{"k":5,"label":"S","evidence":"v63.1","why":"D-007 yeniden değerlendirme eşiklerini aynen belirtiyor."}
{"k":6,"label":"S","evidence":"v3.23","why":"SSH bağlantısının kopmasının migration veya recovery işlemini iptal etmediği açıkça belirtiliyor."}
{"k":7,"label":"S","evidence":"v63.10","why":"D-022 hedefi HLMemo ilk günden kullanılmış gibi bir bellek oluşturmak olarak belirtiyor."}
{"k":8,"label":"S","evidence":"v3.10,v68.20","why":"ORT_DISABLE_TELEMETRY=1 değişkeninin ONNX Runtime başlatılmadan önce ayarlanması açıkça belirtiliyor."}
{"k":9,"label":"S","evidence":"v63.5,v9.1","why":"Proje oluşturulurken iskelet hazırlanması ve sonrasında istemcinin onboarding sırasında doldurması açıkça belirtiliyor."}
{"k":10,"label":"S","evidence":"v89.2","why":"Tırnaksız $IGN değişkeninin zsh altında sözcüklere ayrılmadığı açıkça belirtiliyor."}
{"k":11,"label":"S","evidence":"v67.20,v68.31","why":"Replay kayıtlı değerleri kullanıyor; saat ve sekans fonksiyonlarını çağırmaması açıkça belirtiliyor."}
{"k":12,"label":"P","evidence":"v9.0","why":"Sonlu testlerin sınırı ve geri alınabilir arşiv belirtiliyor; kararın bu nedenle verildiği söylenmiyor."}
{"k":13,"label":"S","evidence":"v63.4","why":"D-014 instruction files için belt-and-braces, hooks için optional ifadelerini aynen kullanıyor."}
{"k":14,"label":"S","evidence":"v63.3,v9.0","why":"Arşiv olayı, kanıt ve değişmez kimliklerin korunması, include_archived ve geri yükleme açıkça belirtiliyor."}
{"k":15,"label":"P","evidence":"v63.1,v5.1","why":"Sürüm geriliği ve işletilecek ek eklenti belirtiliyor; geriliğin uyumluluk ve bakım yüküne neden olduğu çıkarılıyor."}
{"k":16,"label":"S","evidence":"v63.1,v5.0,v6.1","why":"Kendi bi-temporal şeması, Graphiti kavramları ve Phase 2+ yeniden değerlendirme koşulu açıkça belirtiliyor."}
{"k":17,"label":"S","evidence":"v63.7,v10.14","why":"Fallback openai profili olarak belirtiliyor; profil model değeri gpt-5.6-luna."}
{"k":18,"label":"C","evidence":"v80,v63.42","why":"STATUS.md implementer olarak gpt-6-astra diyor; Claude subagent kararı STATUS.md’de değil, D-050’de bulunuyor."}
{"k":19,"label":"S","evidence":"v3.23","why":"Dosyadan çalıştırma, SSH’den ayrılma, kapalı stdin ve tam günlük yolu açıkça belirtiliyor."}
{"k":20,"label":"P","evidence":"v9.0,v63.3","why":"Geri alınabilir arşiv destekleniyor; tazelik amacı ve silmenin doğrulama olanağını yok ettiği gerekçesi ek çıkarımlar."}
{"k":21,"label":"E","evidence":"v3.10,v68.20,v63","why":"Mutex hatası atıflarda belirtiliyor; native abort nedeninin telemetri olduğu açıkça başka öğedeki D-043’te belirtiliyor."}
{"k":22,"label":"S","evidence":"v63.15","why":"D-026 eşikleri, tüm parçaların taranmasını, süreyi ve aynı recall sonucunu açıkça belirtiyor."}
{"k":23,"label":"S","evidence":"v3.23","why":"PID, heartbeat, yeniden denemeden önce inceleme ve bağlantı kesilmesinin uzaktaki işi durdurmaması belirtiliyor."}
{"k":24,"label":"S","evidence":"v89.2","why":"Kaynak zsh sözcük ayırma davranışını ve görünen 52 dakikalık regresyonu doğrudan ilişkilendiriyor."}
{"k":25,"label":"S","evidence":"v60.0","why":"İlk kusur satırı örnek dosyayı, mevcut VPS ortamını, deploy.sh kullanımını ve D-052 riskini açıkça belirtiyor."}
{"k":26,"label":"S","evidence":"v65.3,v67.46","why":"Query için omitted ve cursorsuz tasarım, drilldown ve raw için cursor kullanımı açıkça belirtiliyor."}
{"k":27,"label":"E","evidence":"v68","why":"Atıf yapılan v67 yalnızca omitted alanını tanımlıyor; bütçeye sığan sonuçlar ve kalanların sayılması ayrı v68 öğesinde açıklanıyor."}
```

Temel örüntü, desteklenen olgulara kaynakta belirtilmeyen nedenler veya sonuçlar eklenmesi.
Aynı dosyanın farklı bellek öğeleri, öğe kapsamında ayrı kaynaklar sayıldı.
Belge atfı da iddianın parçası olarak değerlendirildi.