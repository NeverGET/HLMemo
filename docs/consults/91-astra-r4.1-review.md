## Checks

| Kontrol | Sonuç |
|---|---|
| **C1** | **PARTIAL.** 0010, mevcut satırları `ACCESS EXCLUSIVE` kilidi altında doğrular; `lock_timeout=3s` kilidin tutulma süresini sınırlamaz (`alembic/versions/0010_billing_outcome.py:28`). Normal deploy servisleri durdurur ve rollback dump’ı geri yükler; yalnızca R4 koduna dönüp 0010’u bırakmak readiness kontrolünü geçmez (`deploy/RUNBOOK.md:516`, `:972`; `src/hlmemo/server/app.py:251`). |
| **C2** | **NO** — marker/freshness satırları doğrulamadan sonra eklenir; claim, literal veya source sayılmaz, JSON string sözleşmesini bozmaz ve bütçeye dahildir (`src/hlmemo/core/research_service.py:2061`, `:2152`). Ancak kesilme bayrağının ayrı bir yanlış-negatif yolu var: **F-5**. |
| **C3** | **YES** — numaralandırma liste sınırını aşarak tarihleri değiştirebilir (**F-2**). Fenced code ve `>` ile başlayan alıntılar regex’e uymaz; adım silmenin kendisi önceden vardı (`src/hlmemo/librarian/tasks/research.py:2453`). |
| **C4** | **YES** — başlangıçtaki view yetkiyle sınırlıdır, fakat istek sırasında kaldırılan ortak proje erişimi zaman hesabına uygulanmaz; korunan kaydın zaman bilgisi dönebilir (**F-1**, `src/hlmemo/core/research_service.py:2061`). |
| **C5** | **PARTIAL.** Normal hata yanlış sınıflandırılabilir (**F-4**), ancak retry/fallback/breaker davranışı değişmez; yeni log ve ops çıktısı sağlayıcı gövdesini yazmaz (`src/hlmemo/librarian/provider.py:155`, `:778`, `:1158`; `src/hlmemo/ops/cli.py:300`). |
| **C6** | **PARTIAL.** `.env`/`export` atamaları yakalanır; bağımsız YAML `password:` ve boşluklu bazı parolalar kaçabilir—bunlar mevcut açıklar. Yeni yanlış pozitif tüm dosyayı atlatır (**F-3**); `secret-pattern:<rule>` nedeni döndürülür, CLI gösterimi izinli diff’ten doğrulanamıyor (`src/hlmemo/importers/common.py:161`, `:169`, `:213`). |

`main=14cba80` doğrulandı. `deploy/` ve `profiles/` değişmedi; fingerprint/manifest mekanizması ve env şablonu korunuyor. RUNBOOK, disk/API/librarian fingerprint eşleşmesini şart koşuyor (`deploy/RUNBOOK.md:891`); canlı `llm.env` okunmadı ve dağıtım yapılmadı.

İzole yeniden üreticiler çalıştırıldı; F-1 gerçek `_finish` fonksiyonuyla, bağımlılık mock’ları kullanılarak doğrulandı. PostgreSQL migration/rollback provası ve tam pytest çalışması tamamlanmadı.

## Findings

| ID | Severity | Threat | Konum | Kusur |
|---|---|---|---|---|
| F-1 | **CRITICAL** | T7 | `src/hlmemo/core/research_service.py:2061` | Yetkisi kaldırılan kaydın zaman bilgisi dönüyor |
| F-2 | **MEDIUM** | T8 | `src/hlmemo/librarian/tasks/research.py:2465` | Numaralandırma liste dışındaki tarihleri değiştiriyor |
| F-3 | **MEDIUM** | T8 | `src/hlmemo/importers/common.py:170` | Sır olmayan ayar tüm dosyanın atlanmasına neden oluyor |
| F-4 | **MEDIUM** | T8 | `src/hlmemo/librarian/provider.py:155` | Normal rate-limit hatası billing olarak raporlanıyor |
| F-5 | **MEDIUM** | T8 | `src/hlmemo/librarian/tasks/research.py:2401` | Kesilmiş ilk cümle için yeni bayrak yanlışlıkla false kalıyor |

**F-1 — Yetki değişiminden sonra recency ifşası**

**Senaryo:** Başlangıçta okunabilen en yeni kayıt P/Q ortak sahipliğindedir; soru sürerken Q okuma izni kaldırılır, P erişimi kalır. Son kontrol yanıt kaynaklarını filtrelese bile `memory_as_of`, eski view’daki korunan kaydın tam zamanını döndürür. Gösterilen ifşa zaman metadatasıyla sınırlıdır; gövde ifşası saptanmadı, ancak §8’in özel bellek verisi ifşası kuralı nedeniyle CRITICAL’dır.

**Kanıt:**
```python
vids = sorted(run.sent | {shown[h].version_id for h in handles if h in shown})
as_of = memory_as_of(run.view.values())
```

**Minimal düzeltme:** Zaman hesabına katılan bütün view öğelerini son yetki kontrolüne dahil et; yalnızca hâlen okunabilir, aktif ve güncel öğelerden hesapla. Mevcut `readable` kümesi yalnızca gönderilen/dönen öğelerden türediği için tek başına yeterli değildir.

**Yeniden üreten test:** Aşağıdaki test mevcut kodda son assertion’da başarısız olur; `2026-09-30` döner.

```python
import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from hlmemo.core import research_service as rsv
from hlmemo.librarian.tasks import research as rs


@pytest.mark.asyncio
async def test_as_of_excludes_revoked_view_item(monkeypatch):
    conn = NS(execute=AsyncMock())

    @contextlib.asynccontextmanager
    async def db():
        yield conn

    old = datetime(2026, 9, 20, tzinfo=UTC)
    protected = datetime(2026, 9, 30, tzinfo=UTC)
    rows = {
        1: NS(version_id=1, project_ids=[1], device_scope=None,
              current=True, status="active"),
        2: NS(version_id=2, project_ids=[1, 2], device_scope=None,
              current=True, status="active"),
    }

    async def version_access(_conn, vids, _at):
        return [rows[v] for v in vids]

    monkeypatch.setattr(rsv.sq, "version_access", version_access)
    monkeypatch.setattr(
        rsv.mm, "project_policies", AsyncMock(return_value={1: "on", 2: "on"})
    )
    monkeypatch.setattr(
        rsv, "cross_project_excluded", AsyncMock(return_value=set())
    )
    monkeypatch.setattr(rsv.mm, "view_scopes", lambda _: [None])
    monkeypatch.setattr(rsv.mm, "isolation_ok", lambda *_: True)

    run = NS(
        db=db,
        fresh_ctx=AsyncMock(return_value=NS(has=lambda p, _: p == 1)),
        project_id=1, sent={1}, excluded=set(),
        view={
            1: NS(path="old.md", recorded_at=old),
            2: NS(path="protected.md", recorded_at=protected),
        },
        researcher=NS(answer_mode="claims", writer_profile="w",
                      redactor=NS(text=lambda s: s)),
        question="q", slug="p", queries=[], calls=0, steps=[],
        map_tokens=0, flags={"budget_stop": False},
        excerpts_shown=["v1.0"], cite=False, prose=False, claims_mode=True,
    )
    excerpt = rs.Excerpt(
        "v1.0", 1, "old", "old.md", "2026-09-20", "old fact"
    )
    validated = rs.Validated(
        rs.ANSWERED, rs.ANSWERED, "old fact",
        [rs.Claim("old fact", [("v1.0", "old fact")], "kept")],
        ["v1.0"], [], "high",
    )
    out = await rsv._finish(
        run, validated, [excerpt],
        datetime(2026, 9, 30, 12, tzinfo=UTC),
    )
    assert out["meta"]["memory_as_of"] == old.isoformat()
```

**F-2 — Liste durumu sonraki tarihe taşınıyor**

**Senaryo:** `1.` öğesi düşer, `2. Deploy` kalır; ardından numarasız bir başlık ve `3. Ekim` gelir. Fonksiyon başlıkta durumunu sıfırlamadığından tarihi `2. Ekim` yapar; bu dönüşüm yeniden üretildi.

**Kanıt:**
```python
if m is None:
    continue
```

**Minimal düzeltme:** Liste sınırlarında `prev/lost` durumunu sıfırla; yalnızca gerçek, bitişik liste öğelerini yeniden numaralandır ve tarih biçimlerini dışla. Tarih, ayrı liste ve adım referansı regresyonlarını ekle.

**F-3 — Tokenizer ayarı secret olarak algılanıyor**

**Senaryo:** Yararlı bir kurulum belgesi `TOKENIZER_MODEL=bert-base-uncased` içerir. `token` alt dizisi eşleştiğinden yeni kural bütün dosyayı atlar; yeniden üreticide `_env_secret(...) == True` çıktı.

**Kanıt:**
```python
r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*(?:secret|password|passwd|token|api[_-]?key)[A-Za-z0-9_]*)"
return None, f"secret-pattern:{hit}"
```

**Minimal düzeltme:** Secret adlarını tanımlayıcı bileşeni sınırlarında eşleştir; `TOKENIZER_MODEL` gibi sır olmayan adların eşleşmesini engelle. Mevcut gerçek-secret pozitiflerini koruyan negatif regresyon ekle.

**F-4 — `balancer`, `balance` olarak eşleşiyor**

**Senaryo:** Sağlayıcı `429 {"error":{"message":"Load balancer rate limit exceeded"}}` döndürür. `balance`, `balancer` içinde bulunduğundan ledger ve ops yanlış billing/auto-reload uyarısı üretir; sınıflandırıcıda yeniden üretildi.

**Kanıt:**
```python
"balance",
return any(w in text for w in _BILLING_WORDS)
```

**Minimal düzeltme:** Normal sözcükleri tam sözcük sınırlarıyla, yapılandırılmış hata kodlarını ayrı eşleştir. Bu 429 örneğinin billing sayılmadığını doğrula.

**F-5 — İlk uzun birimde yanlış `truncated=false`**

**Senaryo:** Desteklenen ilk ve tek prose cümlesi 4001 karakterdir. `size == 0` nedeniyle yeni cap sinyali oluşmaz, fakat birleştirme 3200 karakterde keser; gözlenen sonuç `len=3200, truncated=False` oldu.

**Kanıt:**
```python
if size and size + 1 + len(text) > ANSWER_MAX_CHARS:
return redact("".join(parts))[:ANSWER_MAX_CHARS]
```

**Minimal düzeltme:** Son dilimlemenin gerçekten içerik kestiğini bayrağa yansıt; ilk uzun cümle ve ilk uzun blok yollarını kapsa. Sorun eski kesmenin kendisi değil, R4.1’in yeni bayrağının yanlış sonuç bildirmesidir.

## Verdict

**NO-GO — F-1 düzeltilmeden yayınlanmamalı.** Yetki değişimi sonrası korunan zaman metadatası dönebiliyor. F-2–F-5 de düzeltilmeli; ardından 0010 upgrade ve dump üzerinden R4 rollback provası tamamlanmalı.