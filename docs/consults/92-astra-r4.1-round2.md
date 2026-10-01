## Round-1 closure

İnceleme hedefi `a77abee`; fix aralığı `14cba80..a77abee`. **60 ilgili unit test geçti**; geçici dosya gerektiren iki test çalıştırılmadı. PostgreSQL migration/rollback provası yapılmadı. Yasaklanan dosyalar okunmadı.

| ID | Durum | Kanıt | Kalan somut senaryo |
|---|---|---|---|
| Astra F-1 | CLOSED | `research_service.py:1898,1931,2063`; `test_as_of_excludes_revoked_view_item` | — |
| Astra F-2 | PARTIAL | `research.py:2473–2487`; başlık/tarih regresyonları geçti | Boş satırla ayrılan liste veya `3)` ile başlayan farklı liste, önceki listenin düşen öğe sayısını devralıyor. |
| Astra F-3 | CLOSED | `common.py:186–193`; `test_non_secret_setting_is_not_a_hit` | — |
| Astra F-4 | CLOSED | `provider.py:128–168`; `test_rate_limits_are_not_billing` | — |
| Astra F-5 | CLOSED | `research.py:2669–2670,2705–2706`; ilk uzun cümle/blok testleri | — |
| Sol F-1 | CLOSED | `test_generic_key_assignment_is_blocked`: özgün `HMAC_KEY` reproducer geçti | Ayrı, fix kaynaklı credential regresyonu aşağıda N-1. |
| Sol F-2 | CLOSED | `0010_billing_outcome.py:41–54`: ayrı NOT VALID / VALIDATE / atomik swap | Kapanış kod incelemesine dayanıyor; PostgreSQL üzerinde yeniden çalıştırılmadı. |
| Sol F-3 | PARTIAL | `research.py:2473–2487`; gerçek `validate_prose` çağrısıyla doğrulandı | Liste ayıracı/girinti/boş satır sınırları korunmuyor; semantik adım numaraları hâlâ değişebiliyor. |
| Sol F-4 | CLOSED | `research_service.py:1898,1920–1931,2063`; revoke/supersession testleri | — |
| Sol F-5 | CLOSED | `common.py:186–193`; `TOKENIZER`, `TOKEN_BUDGET`, `MAX_TOKENS` negatif testleri | — |

Liste reproducer’ında, desteklenmeyen ilk öğe düştükten sonra `2. Deploy…\n\n3. Verify…` sonucu `1. Deploy…\n2. Verify…` oluyor. Son öğe `3)` olduğunda da `2)` yapılıyor. Tarih ve araya giren başlık örnekleri ise düzelmiş.

## New findings

**N-1 — CRITICAL · T2 · Secret filtresi düzeltmesi daha önce engellenen credential’ları geçiriyor**

**Konum:** [src/hlmemo/importers/common.py:173](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/common.py:173), aynı dosyada `179,186–193`.

**Senaryo:** `PRIMARY_API_KEY` veya `CACHE_API_KEY` gerçek bir credential içerdiğinde, isimdeki `primary`/`cache` bileşeni bütün key eşleşmesini iptal ediyor. Ayrıca acronym sınırı bölünmediğinden `APIToken` tanınmıyor. Bu atamaları içeren belge import edilebilir ve credential daha sonra sağlayıcı promptuna taşınabilir.

```python
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
return bool(parts) and parts[-1] == "key" and not _NOT_KEY_COMPONENTS.intersection(parts)
```

**Doğrulama:** Her iki sürümün gerçek `secret_hit()` fonksiyonu bellekte çalıştırıldı:

| İsim | `14cba80` | `a77abee` |
|---|---|---|
| `PRIMARY_API_KEY` | `env-secret-assignment` | `None` |
| `CACHE_API_KEY` | `env-secret-assignment` | `None` |
| `APIToken` | `env-secret-assignment` | `None` |

**Reproducer:** Aşağıdaki assertion’ların üçü de hedef sürümde başarısızdır; değer sentetiktir.

```python
@pytest.mark.parametrize("name", ["PRIMARY_API_KEY", "CACHE_API_KEY", "APIToken"])
def test_existing_credential_detection_is_preserved(name):
    assert common.secret_hit(f"{name}=Abcd1234!") == "env-secret-assignment"
```

**Minimal düzeltme:** Açık `API_KEY` bileşimini genel key istisnalarından önce tanı; istisnaları değişkenin herhangi bir bileşenine yayma. Acronym→kelime sınırını da bölerek `APIToken` eşleşmesini koru. Mevcut `TOKENIZER` ve `PRIMARY_KEY` negatif regresyonlarını koru.

## Migration 0010

- **İşlem sırası doğru:** [0010_billing_outcome.py:41](/Users/cemalkurt/Projects/HLMemo/alembic/versions/0010_billing_outcome.py:41) içindeki `autocommit_block`, `ADD … NOT VALID` ve ardından `VALIDATE` çağrılarını ayrı transaction’larda çalıştırıyor. Tarama boyunca `ACCESS EXCLUSIVE` tutulmuyor.
- **Son swap atomik:** `DROP` ve `RENAME`, tek çok-komutlu SQL çağrısında aynı implicit transaction’a giriyor (`:51–54`). Session `lock_timeout=3s` bütün swap adımlarında etkin; sonunda sıfırlanıyor. Bu süre taramanın toplam süresini sınırlamıyor.
- **Kesinti sonrası tekrar:** ADD/VALIDATE öncesi veya sonrası ölümde eski constraint ve muhtemel `_v2` kalır; yeniden çalıştırma `_v2`’yi kaldırıp yeniden kurar. DROP/RENAME arasında kısmi commit oluşmaz. Swap tamamlanıp Alembic revision kaydı güncellenmeden ölüm de yeniden çalıştırılabilir.
- **Downgrade:** `billing_or_quota → http_error` dönüşümü ayrı commit edilir (`:63–67`); ardından aynı staged swap `OLD` değerleriyle çalışır. DDL simetriktir; billing sınıflandırmasının kaybı downgrade’in bilinçli davranışıdır. Writer’lar açıkken UPDATE ile eski constraint’in eklenmesi arasında yeni billing satırı girerse validation başarısız olabilir.
- **R4 rollback:** [rollback.sh:288](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/rollback.sh:288) önceki ref ve pre-upgrade dump’ı geri getirir; 0010 downgrade’ini kullanmaz. Dolayısıyla `a11f8cf`, kendi 0009 şemasına döner. Yalnızca kodu geri alıp 0010’u bırakmak readiness açısından eşdeğer değildir.

`test_migration_0010_final_constraint_name_and_rerun_after_a_leftover` leftover constraint ve downgrade sonucunu kapsıyor. Her transaction sınırında gerçek process-kill ve concurrent DML testi içermiyor; bu incelemede DB testleri çalıştırılmadı.

## Residual risks

- **Kabul/ret:** Astra F-2 / Sol F-3’ün kalan liste sınırı ve semantik adım numarası değişiklikleri.
- **Kabul/ret:** Migration taraması toplam süreyle sınırlı değil; servislerin durdurulduğu deploy sırasında bakım süresi ledger büyüklüğüne bağlı.
- **Kabul/ret:** Manuel downgrade için writer’ların durdurulması gerekir; aksi halde yeniden deneme gerektiren validation hatası oluşabilir.
- **Kabul/ret:** Staged migration’ın bütün crash sınırları ve R4 dump rollback’i bu incelemede canlı PostgreSQL üzerinde sınanmadı.
- **Kabul/ret:** Dump üzerinden R4 rollback, upgrade sonrasında yapılan yazıları kaybettirir.

## Verdict

**NO-GO — N-1, fix commitlerinin getirdiği doğrulanmış CRITICAL/T2 credential kaçırma regresyonudur.** Yayından önce giderilmelidir. Liste bulguları kısmen açıktır; son tur gereği kalan riskler için owner kabul/ret kararı gerekir.