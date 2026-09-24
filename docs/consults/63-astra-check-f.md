## Verdict (FIX-NEEDED)

- Config: **OK** — genel görev çözümleme ve çağrı başına seçim: `src/hlmemo/config.py:153`, `src/hlmemo/librarian/profiles.py:182`, `src/hlmemo/librarian/provider.py:384`; yeni wiring model kimliği sabitlemiyor.
- Startup: **OK** — etkin librarian ve `llm_mode != off` koşulunda fail-fast; bilinmeyen görev uyarısı: `src/hlmemo/server/app.py:421`, `src/hlmemo/librarian/worker.py:1482`, `src/hlmemo/librarian/profiles.py:215`.
- D-084 / breakers / ledger: **OK** — mevcut %55 süre politikası korunuyor; breaker anahtarı profil adı, ücret kullanılan profil üzerinden: `src/hlmemo/librarian/provider.py:389`, `:498`, `:663`.
- Privacy: **PARTIAL** — precheck her denemede çalışıyor (`src/hlmemo/librarian/provider.py:681`); `require_parameters` bütün üretim profillerinde bulunmuyor ve merkezi zorlanmıyor (`:421`).
- risk_judge / D-071: **OK** — yeterli olmayan fallback eleniyor, outage durumunda retrieval-only korunuyor: `src/hlmemo/librarian/profiles.py:179`, `src/hlmemo/librarian/risk_judge.py:145`.
- Production mapping / installer: **OK** — D-094 eşlemesi ve yeni anahtarların işlenmesi: `deploy/llm.env.example:35`, `deploy/scripts/install_llm_env.sh:159`.
- Ops: **OK** — görev bazlı zincir hesaplanıp gösteriliyor: `src/hlmemo/librarian/profiles.py:240`, `src/hlmemo/ops/service.py:539`, `src/hlmemo/ops/cli.py:268`.
- **P2 | `profiles/openrouter.toml:7` | synthesis/query_rewrite fallback çağrısı `require_parameters=true` göndermiyor | Profilin `extra.provider` alanına ekleyip üretim zincirlerinin request-body kontrolünü ekleyin.** Mevcut profil açığı; F’nin getirdiği regresyon değil.
- Doğrulama: statik inceleme ve salt okunur profil çözümleme; tam test/gate çalıştırılmadı.