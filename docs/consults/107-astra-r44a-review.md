## Verdict

**NO-GO — Round 1.** Astra low ve Sol xhigh incelemeleri birleştirildi.

1. **HIGH — Promotion guard eşzamanlı batch onayını eksik sayabiliyor.**  
   **Konum:** `src/hlmemo/librarian/roles.py:89`, `:391`.  
   Promotion exclusive rol kilidi alıyor; `record_batch_decision` aynı kilidin shared tarafını almıyor. Sayımdan sonra `open → approved` geçişi commit edebilir.  
   **Reproducer:** Observer altında geçerli bir açık soru oluşturun. `promotion_release` gerçek sorgudan `{}` döndürdükten sonra promotion’ı asyncio bariyerinde durdurun. İkinci bağlantıda batch’i kabul edip commit edin. Promotion’ı sürdürün: `--release-pending` olmadan başarılı olur; assistant worker soruyu uygular.  
   **Minimal fix:** `record_batch_decision` içinde soru kilitlerinden **önce** `lock_role_order(conn, exclusive=False)` alın. Bu interleaving’i kapsayan integration testi ekleyin.

2. **HIGH — Başarısız downgrade, withdraw’u engelleyen CHECK bırakabiliyor.**  
   **Konum:** `alembic/versions/0011_question_withdrawn.py:63`, `:75–80`.  
   Dar `_v2` constraint ayrı autocommit adımında kalıcılaşıyor. Downgrade reddedildiğinde cleanup `DROP` da 3 saniyelik lock timeout’a tabi; başarısız olursa Alembic hâlâ 0011 gösterirken `_v2`, `withdrawn` yazımlarını reddediyor.  
   **Reproducer:** Bir withdrawn satırı oluşturun. Staged `ADD` commit’inden sonra bariyer koyun; ikinci bağlantıda transaction içinde tabloyu okuyup `ACCESS SHARE` kilidini tutun. Downgrade’ı sürdürün: refusal sonrası cleanup timeout olur. `_v2` kalır; başka pending soruyu withdraw etmek CHECK hatası verir.  
   **Minimal fix:** Downgrade’daki staged `ADD`, refusal kontrolü ve swap’ı tek transaction’a alın; refusal staged constraint’i de rollback etsin. Upgrade staging’i korunabilir. Kilit süresini sınırlayın ve bu hata yolunu test edin.

Withdraw/apply status kontrollerinde, `resolved.question_status` replay yolunda veya owner-token/grant kurallarında başka somut regresyon bulunmadı.

**Doğrulama:** `main@head=0011` ve üç verifier-format kontrolü geçti. Pytest geçici dosya izinleri nedeniyle setup’ta durdu; `HLM_TEST_DSN` yok. Yukarıdaki DB reproducer’ları test taslağıdır, çalıştırılmadı.

**Rollback:** Pre-upgrade dump restore, snapshot sonrasındaki **bütün yazıları**, withdraw kayıtları dahil, kaybettirir; geri çekilen sorular yeniden pending olur. Diff env/compose değiştirmiyor; arşivde `edge` ağı mevcut ve korunmalı.