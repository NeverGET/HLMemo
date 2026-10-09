**GO-WITH-FIXES**

- **MEDIUM — `src/hlmemo/cli/mcp_client.py:137–154`:** Metindeki sayıdan HTTP durumu çıkarımı, timeout kontrolünden önce çalışıyor. Somut girdi: `httpx2.ReadTimeout("read timed out after 500 ms")`. Doğrulanan sonuç: `error:server/E_UNAVAILABLE`, retry `True`; beklenen: `timeout`, retry `False`. Timeout türünü metinden durum çıkarmadan önce kontrol edin.

Kalan HIGH bulunmadı. İki yeni partial-snapshot testi, HTTP-timeout testi ve gerçek istemci cancellation testi doğrudan çalıştırılarak geçti; standart ReadTimeout/ConnectTimeout ve JSON’suz 503 sınıflandırmaları doğrulandı. Tam pytest paketi çalıştırılmadı. Dosyalar değiştirilmedi.