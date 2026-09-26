## Verdict (FIX-NEEDED)

- T1 → FIXED — `src/hlmemo/core/memory_map.py:497`; `src/hlmemo/librarian/privacy.py:162`; `src/hlmemo/librarian/tasks/map_summary.py:110`.
- T2 → FIXED — `src/hlmemo/librarian/tasks/research.py:380`; `src/hlmemo/librarian/tasks/map_summary.py:179`.
- T4-high → FIXED — `src/hlmemo/librarian/provider.py:668`; `src/hlmemo/core/research_service.py:506`.
- T4-med → FIXED — `src/hlmemo/librarian/tasks/map_summary.py:214,244,344`.
- T5 → FIXED — `deploy/scripts/check_librarian.py:78,102,111,505`; `deploy/scripts/llm_env_release.py:88`; `deploy/scripts/install_llm_env.sh:284`; `deploy/RUNBOOK.md:677`.
- T6 → OPEN — `src/hlmemo/librarian/tasks/research.py:251`: kesintisiz alıntı kontrolü düzelmiş; olumsuzluk kontrolü aşılabiliyor.
- **HIGH — T6 devamı:** `src/hlmemo/librarian/tasks/research.py:251` | Kaynak: “The release gate is not enabled by default. It runs weekly, not daily.” İddia/yanıt: “The release gate is enabled by default and runs weekly, not daily.” `validate_answer` çalıştırıldığında `answered=True`, `confidence=high`, kaynak `primary` olarak kabul edildi. | Olumsuzluğu tüm metindeki varlığıyla değil, ilgili önerme kapsamında eşleştir.

## Residual (owner decision)

- `deploy/RUNBOOK.md:697`: genel convergence açıklaması hâlâ `--release r3` diyor; R4 bölümü ve installer doğru.
- `src/hlmemo/librarian/tasks/map_summary.py:321`: provider başarısından sonraki DB yazma hatası `_failed` yolunu atlıyor; boşta kalan DB’de yeniden deneme yeni olaya kadar durabilir.
- Saf birim testleri: **48 geçti**; manifest kontrolleri ve shell sözdizimi doğrulandı. DB entegrasyon testleri çalıştırılmadı.