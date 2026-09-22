Düzeltmeler tamamlandı; commit atılmadı. Yalnız izin verilen dosyalar değişti.

- [write_service.py:499](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/src/hlmemo/core/write_service.py:499): Revision, pin ve kartların tüm güncel segmentlerinde görünürlük kontrolü; gizli hedeflere `E_NOT_FOUND`.
- [middleware.py:117](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/src/hlmemo/server/middleware.py:117): Gövdeyi DB öncesinde sınırlar; bağlantıyı SSE başlıklarından/sonlu yanıt gönderiminden önce bırakır; iptal temizliğini korur.
- [resolve.py:20](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/src/hlmemo/auth/resolve.py:20): Yeni okuyucuların bekleyen revoke’u geçmesini önleyen advisory kilit ve ardından token doğrulaması.
- [devices.py:221](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/src/hlmemo/server/devices.py:221): Revoke için sınırlı, aktif istekleri bekleyebilen kilit süresi.
- [pool.py:13](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/src/hlmemo/db/pool.py:13): PostgreSQL zaman aşımı korumaları.
- [config.py:160](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/src/hlmemo/config.py:160): Yapılandırılabilir limitler.
- [test_revision_scope.py:1](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/tests/integration/test_revision_scope.py:1): 19 yetkilendirme regresyonu.
- [test_request_lifetime.py:1](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/tests/integration/test_request_lifetime.py:1): 13 gövde, bağlantı, iptal ve commit regresyonu.
- [test_revoke_deadline.py:1](/Users/cemalkurt/Projects/HLMemo-bake/astra-r3/tests/integration/test_revoke_deadline.py:1): İki revoke/kilit kuyruğu regresyonu.

Doğrulama:

```text
A1: 5 passed in 3.38s*
A2: 333 passed in 41.68s
A3: All checks passed! / 96 files already formatted
A4: 4 passed in 45.50s
G3: Recall@5 = 0.930
G4: p95 = 270.2 ms
```

*A1’in birebir komutu, mevcut `conftest.py` yalnız yasaklanan `hlm_r1judge` adına izin verdiğinden toplanamıyor. Repro dosyalarını değiştirmeden, aynı fixtures’ı `PYTHONPATH=. --noconftest -p tests.conftest` ile yükleyerek `hlm_r3_w1` üzerinde doğruladım. Modeller mevcut dizinden `HLM_MODELS_DIR` ile kullanıldı. A4 yalnız bir kez, PostgreSQL salt okunur modunda çalıştırıldı.

Varsayılanlar:

| Ayar | Değer |
|---|---:|
| `HLM_REQUEST_MAX_BODY_BYTES` | 4194304 |
| `HLM_REQUEST_BODY_TIMEOUT_S` | 10 |
| `HLM_REQUEST_DB_TIMEOUT_S` | 15 |
| `HLM_POOL_TIMEOUT_S` | 5 |
| `HLM_DB_LOCK_TIMEOUT_MS` | 2000 |
| `HLM_DB_STATEMENT_TIMEOUT_MS` | 10000 |
| `HLM_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` | 5000 |

Revoke’un türetilmiş DB bekleme sınırı 17, toplam işlem sınırı 24 saniye. Kalan sınırlar: büyük/uzun meşru istekler limitlere takılabilir; açık SSE hâlâ HTTP kaynakları tüketir, DB bağlantısı tutmaz.

Önerilen commit mesajı: `fix: enforce revision visibility and bound request DB lifetimes`