```jsonl
{"k":1,"label":"CONTRADICTS","missing":["STATUS.md still lists gpt-6-astra (codex) as implementer"],"why":"Yanıt, STATUS.md’nin hâlâ Astra’yı listelediği olgusuyla çelişerek dosyanın Claude subagent’larını gösterdiğini söylüyor."}
{"k":2,"label":"MISSING","missing":["codex mcp add registration uses --bearer-token-env-var HLM_DEVICE_TOKEN"],"why":"Ortam değişkeni doğru; kayıt komutu ve --bearer-token-env-var bayrağı belirtilmemiş."}
{"k":3,"label":"MISSING","missing":["Events are replayed in event_id order","Request bodies supply chunk text during replay","Replay never calls the chunker"],"why":"Olay sırası ve chunk metninin kaynağı eksik; tokenizer çağırmamak, chunker çağırmamakla eşdeğer değil."}
{"k":4,"label":"FABRICATED","missing":[],"why":"Bellek LRU önbelleği belirtir; yanıtın olgu olarak sunduğu süreç içi önbellek niteliğini belirtmez."}
{"k":5,"label":"ALL","missing":[],"why":"Hibrit retrieval, tetikleyen cihazın güncel kapsamı ve izinleri ile iki aday kümesinin tüm özellikleri belirtilmiş."}
{"k":6,"label":"ALL","missing":[],"why":"zsh word-splitting davranışı, bayrakların düşmesi, 52 dakikalık görünür regresyon ve bash çözümü belirtilmiş."}
{"k":7,"label":"ABSTAIN-EQUIVALENT","missing":[],"why":"GraphQL öncülünü reddediyor; sunduğu MCP ve JSON Schema ayrıntıları bellekte bulunuyor."}
{"k":8,"label":"ALL","missing":[],"why":"Yok edilmiş mutex’i kilitleyen telemetry callback ve ONNX Runtime başlatılmadan önce ayarlanan ORT_DISABLE_TELEMETRY=1 belirtilmiş."}
{"k":9,"label":"ALL","missing":[],"why":"Tek kullanıcı, 10–20 MB bellek, binlerce kenar ve recursive CTE ile 2–3 hop geçiş belirtilmiş."}
{"k":10,"label":"MISSING","missing":["G7 passed with agy 1.2.8"],"why":"Sürümler, otomatik güncelleme ve yeniden sabitleme belirtilmiş; G7’nin başarıyla geçtiği belirtilmemiş."}
{"k":11,"label":"MISSING","missing":["Codex issued the warning that forgetting destroys evidence and entrenches mistakes"],"why":"Kanıtın korunması, sıralama ve geri alınabilir arşiv açıklanmış; uyarının Codex’ten geldiği belirtilmemiş."}
```