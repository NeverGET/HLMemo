# LLM Kodlama Ajanları için Kendi Sunucunuzda Barındırılan, İnsan Benzeri Uzun Vadeli Bellek Sistemi: Tasarım Odaklı Araştırma Raporu

## TL;DR (Özet)
- Piyasada tek başına tüm ihtiyaçları karşılayan bir çözüm yoktur: en yakın adaylar Zep/Graphiti (çift zamanlı bilgi grafiği), Mem0 (hibrit vektör+grafik, güçlü kıyaslama sonuçları) ve Letta/MemGPT (kademeli çekirdek/arşiv belleği) olsa da; token bütçeli çok katmanlı ipucu (clue) tabanlı okuma, kütüphaneci LLM tetikleme mantığı ve projeler arası deneyim katmanının tümünü tek pakette sunan bir sistem yoktur. Bu nedenle önerilen yol HİBRİT: Graphiti'yi çift zamanlı grafik çekirdeği olarak yeniden kullanın, üstüne katman modeli, kütüphaneci orkestrasyonu ve MCP araç setini özel olarak inşa edin.
- Mimari, kullanıcının anlaştığı çift yollu tasarımı korumalıdır: HIZLI DETERMINISTIK YOL (vektör+anahtar kelime+grafik, LLM'siz, önceden hesaplanmış özet ve ipucu id'leri, ~200 ms) ve YAVAŞ YOL (kütüphaneci LLM: sentez, çelişki çözümü, yazma yerleşimi ve konsolidasyon). Kütüphaneci LLM işinin çoğunu yazma ve konsolidasyon tarafında yapar.
- Kütüphaneci model olarak, gizlilik birinci öncelik olduğu için kendi sunucunuzda barındırılabilen açık ağırlıklı bir modeli (Qwen veya Mistral Small 4, vLLM ile) tavsiye ederiz; bulut kabul edilebilirse DeepSeek V4 Flash (cache-hit fiyatı ile ~0.003 USD/M girdi) veya Gemini Flash fiyat/performansta en iyi konumdadır. Tek bir yoğun kullanıcı için tahmini aylık maliyet ~30-70 USD (VPS + LLM) aralığındadır.

## Ana Bulgular (Key Findings)

1. **Hiçbir mevcut sistem tam örtüşmüyor, ama parçalar hazır.** Çift zamanlı grafik (Graphiti/Zep), hiyerarşik özetleme (RAPTOR, MemoryOS), unutma eğrileri (MemoryBank), skorlama (Generative Agents), uyku benzeri konsolidasyon (Letta sleep-time compute) ve MCP taşıma katmanı olgun ve büyük ölçüde açık kaynaktır. Eksik olan: token bütçeli ipucu tabanlı ilerleyici açığa çıkarma (progressive disclosure) API'si, kütüphaneci tetikleme/oturum yaşam döngüsü ve deneyim katmanı. Bunlar özel inşa edilmeli.

2. **Üç CLI de uzak MCP'yi destekliyor ama farklı yollarla.** claude-code streamable HTTP + OAuth 2.1 kullanır (`claude mcp add --transport http`), codex CLI `config.toml` içinde `mcp_servers` ile hem stdio hem streamable HTTP destekler, antigravity-cli ise `~/.gemini/antigravity-cli/mcp_config.json` (global) veya `.agents/mcp_config.json` (workspace) dosyalarını kullanır. Üçü de tek bir uzak MCP sunucusuna bağlanabilir.

3. **Proje kimliği bağlama.** Her CLI'nin kendi bağlam dosyası var (CLAUDE.md, AGENTS.md, GEMINI.md). Proje id'si bu dosyalara veya MCP araç çağrısı parametresine/env değişkenine yazılabilir; böylece hangi CLI/model açarsa açsın aynı belleğe ulaşır.

4. **Kancalara (hooks) bağımlı olmayın.** Kullanıcının gözlemi doğru: modeller çoğu zaman başlangıç adımlarını atlayıp doğrudan göreve dalıyor. Proaktif flashback, kanca yerine kütüphaneci tetikleme + yazma/görev-başlangıcı MCP çağrısı üzerinden tasarlanmalı; kancalar sadece opsiyonel destek olarak belgelenmeli.

5. **Kendi sunucunuzda barındırma için en iyi yığın: Postgres + pgvector + Apache AGE.** Tek veritabanında vektör, ilişkisel ve grafik; SQL join'leri, işlemsel tutarlılık, tek yedekleme. Alternatif olarak Graphiti'nin desteklediği FalkorDB (AI/GraphRAG odaklı). Neo4j güçlü ama ayrı operasyonel yük. Kuzu Ekim 2025'te Apple tarafından satın alınıp arşivlendi, riskli.

## Detaylar (Details)

### A. Mevcut Bellek Sistemleri Karşılaştırması

Aşağıdaki tablo, kendi sunucunuzda barındırılabilirlik ve MCP yeteneği odağıyla mevcut sistemleri özetler.

| Sistem | Mimari | Zamansal destek | Çoklu proje ad alanı | Konsolidasyon/unutma | MCP + taşıma | Lisans | Kendi sunucu | Olgunluk | Kıyaslama |
|---|---|---|---|---|---|---|---|---|---|
| **Zep / Graphiti** | Çift zamanlı bilgi grafiği (Neo4j/FalkorDB/Kuzu) | Çok güçlü (bi-temporal, t_valid/t_invalid, edge invalidation) | Grup/graph id ile | Edge geçersizleştirme, çelişki çözümü | Graphiti açık kaynak; Zep Cloud MCP | Apache 2.0 (Graphiti) | Evet (grafik DB'yi siz işletirsiniz) | Yüksek (20k+ yıldız) | DMR: Zep %94.8 (gpt-4-turbo), %98.2 (gpt-4o-mini) vs MemGPT %93.4 |
| **Mem0 / OpenMemory** | Hibrit vektör + opsiyonel bilgi grafiği; tek geçişli çıkarım, çok sinyalli getirme (semantik+BM25+entity) | Zamansal yeniden sıralama | Evet (user/agent/run id) | ADD-only çıkarım, entity linking | OpenMemory MCP (yerel-öncelikli) | Apache 2.0 | Evet (Qdrant + docker) | Yüksek | LoCoMo 92.5, LongMemEval 94.4, BEAM(1M) 64.1; sorgu başına ort. 6.956 token |
| **Letta (MemGPT)** | OS-esintili kademeli bellek (core/recall/archival), ajan kendi belleğini düzenler | Sınırlı | Ajan başına | Sleep-time compute ile konsolidasyon | MCP var | Apache 2.0 | Evet | Yüksek (23k+ yıldız) | DMR temeli |
| **Cognee** | Grafik+vektör hibrit, ECL boru hattı (extract/cognify/load), ontoloji | Var | Dataset ile | Yeniden konsolidasyon, feedback | Özel MCP (SSE); Claude Code/Codex eklentisi | Apache 2.0 | Evet (SQLite/LanceDB varsayılan, Postgres+pgvector önerilir) | Orta-Yüksek | COGX içe/dışa aktarma (Mem0/Letta/Zep/Graphiti) |
| **Supermemory** | Bellek+bağlam motoru, otomatik profil, çelişki çözümü | Var | x-sm-project başlığı | Seçici unutma | MCP (Cloudflare Workers), opsiyonel self-host | MIT | Kısmi (API anahtarı) | Orta | LongMemEval %95 Recall@15, ~720 token bağlam |
| **LangMem** | LangGraph bellek yardımcıları | Sınırlı | Namespace | Temel | LangGraph içinde | MIT | Evet | Orta | - |
| **HippoRAG / A-MEM / MemoryOS / MemoryBank** | Akademik: hipokampal PPR grafiği / Zettelkasten notları / OS-esintili katmanlar / Ebbinghaus unutma | Değişken | - | A-MEM evrim, MemoryBank decay | Genellikle yok | Çoğu MIT/araştırma | Evet (referans kod) | Araştırma | LoCoMo çeşitli |
| **basic-memory / mcp-memory-service** | Markdown/SQLite tabanlı, bilgi grafiği + otonom konsolidasyon | Sınırlı | Proje/etiket | mcp-memory-service otonom konsolidasyon | MCP (stdio + streamable HTTP + OAuth) | Apache/MIT | Evet (docker, ONNX yerel embed) | Orta | ~5ms getirme iddiası |
| **Anthropic memory tool + CLAUDE.md** | Dosya tabanlı bellek (/memories, memory_20250818), context editing/compaction | Yok | Proje silolama | Manuel/otomatik (auto-memory) | Anthropic yerel | Tescilli | Hayır | Yüksek | 100-tur web görevinde context editing ile %84 token tasarrufu, bellek aracı ile %39 performans artışı |
| **Codex AGENTS.md** | Talimat dosyası (davranış, bellek değil) | Yok | Dizin bazlı | Yok | - | Tescilli | - | Yüksek | - |
| **Gemini/Antigravity GEMINI.md** | Bağlam dosyası + eklenti belleği | Yok | Workspace | Yok | - | Tescilli | - | Yüksek | - |

**Yorum:** Kullanıcının tasarımına en yakın temel Graphiti'dir çünkü çift zamanlı geçerlilik, çelişki tespiti (semantik+anahtar kelime+grafik) ve edge invalidation zaten kullanıcının "zaman algısı", "şu an geçerli vs eskiden geçerli" ve "çelişkide kütüphaneciye sor" gereksinimleriyle birebir örtüşür. Graphiti dört zaman damgasını (t_created, t_expired sistem zamanı; t_valid, t_invalid gerçeklik zamanı) izler ve çelişki çıktığında eskiyi silmek yerine geçersizleştirir, tarihsel doğruluğu korur. Mem0 ise token verimli getirme (LoCoMo'da sorgu başına ortalama 6.956 token, tam bağlamın ~25.000+ tokenine karşı) ve çok sinyalli füzyon (semantik + BM25 + entity) için mükemmel bir referanstır; en büyük kazanımları zamansal (+29.3) ve çok atlamalı (+25.2) muhakemededir.

### B. Akademik ve Mimari Temeller ve Kullanıcının Katmanlarına Eşlenmesi

- **RAPTOR (Sarthi vd., Stanford, ICLR 2024, arXiv:2401.18059):** Metni özyinelemeli olarak gömme, kümeleme ve özetleme ile alttan üste bir ağaç kurar; sorgu anında farklı soyutlama seviyelerinden getirir (collapsed tree retrieval). GPT-4 ile eşleştiğinde QuALITY kıyaslamasında en iyi sonucu mutlak doğrulukta %20 iyileştirdi. Bu, kullanıcının L0 (ham) -> L2 (konu özeti) -> L3 (proje genel bakış) katmanlarının ve "önce genel özet, sonra ipucu ile derine in" akışının doğrudan teorik temelidir.
- **MemGPT / MemoryOS:** OS-esintili sanal bellek, çekirdek (her zaman bağlamda) / recall / archival kademeleri. Kullanıcının "aktif çalışma belleği + derine inen katmanlar" fikrini besler; kütüphanecinin çalışma belleği tasarımı buradan gelir. MemoryOS kısa/orta/uzun vadeli kademeleri koordine eder.
- **Generative Agents (Park vd., UIST 2023, arXiv:2304.03442):** Getirme skoru = recency + importance + relevance (referans uygulamada tüm ağırlıklar 1). Recency, "belleğin son getirilmesinden bu yana geçen saat sayısı üzerinde üssel decay, decay faktörü 0.995"tir. Importance, LLM'e doğrudan sorulan 1-10 puanıdır ("1 tamamen sıradan, 10 son derece çarpıcı"). Reflection, biriken önem eşiği aşınca tetiklenir. Bu, kullanıcının SKOR ve "eşik altını sessizce düşür" ile konsolidasyon tetikleme fikrinin temelidir.
- **Temporal/bitemporal knowledge graph (Zep, arXiv:2501.13956):** Dört zaman damgası (t_created, t_expired, t_valid, t_invalid). Kullanıcının sürüm geçmişi, geçerlilik aralıkları ve "eskiden doğruydu" modeli için kanonik temel.
- **Uyku benzeri konsolidasyon (Letta sleep-time compute, Lin vd. 2025):** Boşta kalınca ham bağlamı "öğrenilmiş bağlama" dönüştürme; ikili ajan modeli (canlı ajan + arka planda çalışan uyku ajanı: parçalanmış bellekleri birleştirir, örüntü bulur, tekilleştirir, eskiyeni arşivler/budar). Kullanıcının "sleep" protokolü ve kütüphanecinin fresh-wake durumu için doğrudan referans. Not: sleep-time compute'un faydası, gelecekteki sorgular mevcut bağlamdan bir ölçüde tahmin edilebilir olduğunda en yüksektir.
- **Unutma eğrileri (MemoryBank, AAAI 2024, arXiv:2305.10250):** Ebbinghaus üssel decay; sık erişilen/önemli bellekler pekişir, ihmal edilenler solar. A-MEM deneyleri: forgetting-curve bellek boyutunu ve getirme süresini etkili biçimde kontrol eder ama görev performansında ciddi düşüşe yol açabilir (dikkatli eşik gerekir). FadeMem (2026) diferansiyel decay ile %45 depolama azaltımı bildirir.
- **Proaktif getirme (ProactAgent arXiv:2604.20572; Proactive Memory Agent arXiv:2607.08716; Reflexion, Shinn vd. 2023):** Öğrenilmiş proaktif getirme, pasif stratejilerden üstün; ProactAgent SciWorld'de etkileşim turlarını GRPO+Reflexion'a göre %33.2 azaltır. Reflexion, başarısızlıklardan epizodik tampona sözel ders çıkarır. Bu, kullanıcının çift yönlü flashback ve "geçmiş hatayı tekrar etme riskini önceden uyar" gereksiniminin temelidir. PROJECTMEM (arXiv:2606.12329) bunu tam olarak kodlama ajanları için "eylem öncesi kapı" (pre-action gate) olarak dışsallaştırır, yani Reflexion'ın epizodik belleğini oturumlar ve projeler arası kalıcı ve tipli hale getirir.
- **Progressive disclosure / token bütçesi:** Anthropic Agent Skills modeli (ad+açıklama ~100 token bağlamda kalır, gövde tetiklenince yüklenir) ilerleyici açığa çıkarmanın üretim örneğidir ve kullanıcının token-bütçeli ipucu API'sinin tasarım şablonudur. Anthropic'in context editing + bellek aracı kombinasyonu 100-turlu bir web arama değerlendirmesinde token tüketimini %84 azaltıp bellek aracıyla %39 performans artışı sağladı, bu da katmanlı/ipucu tabanlı okumanın somut kazanımını gösterir.

### C. Üç CLI için Entegrasyon Ayrıntıları (2026 doğrulaması)

**claude-code:**
- Uzak MCP: `claude mcp add --transport http <ad> <url>`. Streamable HTTP önerilir (SSE artık kullanımdan kaldırıldı). OAuth 2.1 yerel destekli: `/mcp` yazıp tarayıcıda giriş yapılır, token saklanır ve otomatik yenilenir.
- JSON yapılandırma: `.mcp.json`, `~/.claude.json` içinde `"type": "http"` (veya `streamable-http` takma adı) + `url` gereklidir. Type olmadan url = yapılandırma hatası. Per-sunucu `timeout` (ms) ve `MCP_TIMEOUT` env değişkeni ayarlanabilir.
- Proje bağlama: CLAUDE.md (Anthropic 200 satır altında tutulmasını önerir, çünkü her satır her oturuma yüklenir). Auto-memory ve memory tool (/memories dizini, memory_20250818) mevcut ama tescilli.
- Kanca: SessionStart/PreToolUse mevcut ama önceliklendirilmemeli.

**codex CLI:**
- `config.toml` içinde `[mcp_servers.ad]`; stdio için `command`, uzak için `codex mcp add <ad> --url <url>` (streamable HTTP, bearer token). Mart 2026'dan beri `codex mcp` alt komut ailesi var. `/mcp` ile araçlar listelenir.
- Proje bağlama: AGENTS.md (global `~/.codex/AGENTS.md`, proje ve iç içe dizinler; AGENTS.override.md ile dar kapsam; `project_doc_fallback_filenames` ile alternatif dosyalar). Codex çalışmadan önce AGENTS.md okur.
- Bellek MCP ekosistemi hazır (Memorix, codebase-memory-mcp örnekleri config.toml'de çoklu sunucu çalıştırabiliyor; tool şemaları system prompt'ta token tüketir, dört sunucu ~5.100 token).

**antigravity-cli (agy):**
- Gemini CLI 18 Haziran 2026'da (ücretsiz ve Pro/Ultra katmanları için) emekliye ayrıldı; Antigravity CLI (agy, Go tabanlı) halefidir. MCP config: global `~/.gemini/antigravity-cli/mcp_config.json`, workspace `.agents/mcp_config.json`. `serverUrl` + `authProviderType` (ör. google_credentials) veya bearer.
- Uyarı: url tipi yanlışsa agy başlangıçta hata vermez, sadece o sunucunun aracı çağrıldığında başarısız olur. `/mcp` ile durum kontrol edilir. Skills/Hooks/Subagents/Extensions (artık plugins) taşınıyor; hook JSON formatı ve yaşam döngüsü aynı kalıyor.
- Proje bağlama: GEMINI.md (bağlam dosyası).

**Ortak tasarım kararı:** Tek bir streamable HTTP MCP sunucusu, üç CLI'de de aynı URL ile kaydedilir. Kimlik doğrulama: tek kullanıcı için başlangıçta bearer API anahtarı (env veya config), ileride OAuth 2.1 (claude-code yerel, codex destekli). Proje id'si iki yoldan bağlanır: (1) her projede CLAUDE.md/AGENTS.md/GEMINI.md içine kısa `project_id: xyz` notu; (2) MCP araç çağrısında `project_id` parametresi. Model çoğu zaman bağlam dosyasını okuyup id'yi ilk MCP çağrısına aktarır. Bilgisayar A veya B'den, hangi model olursa olsun aynı uzak sunucuya bağlanıldığı için bellek paylaşılır.

### D. Tasarım Sentezi: Önerilen Hibrit Mimari

Bu, raporun merkez parçasıdır. Her bileşen için "yeniden kullan" veya "özel inşa et" kararı belirtilir.

#### D.1 Katman Modeli
- **L0 - Ham bellek (raw):** Orijinal belgeler, transkriptler, PDF/DOCX parçaları, tam olaylar. Postgres tabloları + nesne depolama. *Yeniden kullan: Postgres.*
- **L1 - Atomik gerçekler/epizodlar:** Graphiti düğüm/kenarları, çift zamanlı geçerlilikle. *Yeniden kullan: Graphiti.*
- **L2 - Konu özetleri:** RAPTOR tarzı özyinelemeli küme özetleri, her biri ipucu id'leri taşır. *Özel inşa (RAPTOR mantığı).*
- **L3 - Proje genel bakışı:** Proje başına tek "kart", token bütçeli ilk yanıtın çekirdeği. *Özel inşa.*
- **L4 - Projeler arası / global + deneyim katmanı:** Cross-project dersler (birden çok projeye bağlı birinci sınıf varlıklar, kopya değil) ve zamandan bağımsız çalışan "deneyim" bilgisi. *Özel inşa.*

#### D.2 Veri Modeli
- **Varlıklar:** memory_item (id, layer, project_ids[], user_id, type {fact, episode, lesson, experience, doc_chunk}, content, summary, clue_ids[], embedding, score, t_valid, t_invalid, t_created, t_expired, version, stability_class {stable|volatile}, source_ref).
- **İlişkiler:** Graphiti kenarları (relates_to, contradicts, supersedes, derived_from, applies_to_project). Cross-project ders: tek düğüm, çoklu `applies_to_project` kenarı (kopya değil).
- **Skor:** aşağıdaki konsolidasyon formülü. Kararlı (stable) vs oynak (volatile) sınıflandırması bir alan olarak tutulur; deneyim katmanı düşük decay katsayısı alır.

#### D.3 MCP Araç Seti
1. `memory.query(project_id, query, token_budget, want_clues=true)` -> genel özet + ipucu id'leri, verilen token bütçesinde (ör. 3k). *Hızlı yol, LLM'siz, ~200 ms.*
2. `memory.drilldown(clue_ids[], token_budget)` -> bir sonraki katman detayı.
3. `memory.raw(item_id)` -> L0 ham veriye doğrudan erişim (kurallara uyarak).
4. `memory.write(project_id, items[], relations[])` -> kütüphaneciye teslim; yerleşimi o yapar.
5. `memory.call_the_day(project_id, notes)` / `memory.compact(project_id)` -> oturum sonu yazma protokolü.
6. `memory.register_lesson(project_id, mistake, fix, context)` -> ders/hata kaydı.
7. `memory.risk_check(project_id, task_description)` -> proaktif flashback: göreve karşı geçmiş hatalar eşleştirilir; risk varsa uyarı döner, yoksa "uyarılacak bir şey yok".
8. `memory.ingest_document(project_id, file)` -> PDF/DOCX yükleme (chunk, özet, katmana bağla).

Tüm sorgu araçları bir `token_budget` parametresi alır ve asla hepsini döndürmez; yanıt her zaman "daha fazlası için ipucu id'leri" içerir. Amaç: 10-20 MB'lık belleğe sahip bir projede, proje LLM'ine 100k token bile harcatmadan gereken tüm genel bilgiyi verip, talep üzerine derine inmesini sağlamak.

#### D.4 Kütüphaneci LLM Yaşam Döngüsü (kullanıcının IMPORTANT ve SENSITIVE işaretlediği bölüm)
- **Tetikleyiciler:** (a) yazma/commit anı (yerleşim, çelişki tespiti, cross-project benzerlik kontrolü); (b) `risk_check` çağrısı; (c) belirsiz/sentez gerektiren sorgu (hızlı yol düşük güven döndürünce); (d) zamanlanmış/tembel konsolidasyon. Kütüphaneci "başka projelerde benzer bir şey var mı?" kontrolünü yapar; proje LLM'i uyarılmalıysa durumu netleştirip uyarır, aksi halde "uyarılacak bir şey yok" işaretler.
- **Çalışma belleği:** Kütüphaneci kendi küçük iç belleğine (kararlar, çözülen çelişkiler, yerleşim kuralları) sahiptir. Aktif işi belirli koşullara kadar bağlamda tutar.
- **Kompaktlama:** Bağlam eşiği (ör. 100k token, Anthropic'in clear_tool_uses_20250919 varsayılanı gibi) aşılınca eski araç sonuçları temizlenir veya compact_20260112 tarzı özetlenir; kütüphaneci "call the day" benzeri bir compact yapar.
- **Fresh-wake:** Boşta kalınca sıfırdan uyanır, kendi küçük iç belleğini kontrol eder ve proje LLM'inden sorgu bekler. Bu, Letta sleep-time compute ikili ajan modelinin (canlı ajan + uyku ajanı) uyarlamasıdır.
- **Bloke etmeme kuralı:** Yerleşim belirsizliği ASLA bloke etmez (varsayılan: her iki yere yaz + çapraz bağla). Yalnızca mevcut gerçeklerle GERÇEK çelişki proje LLM'ine soru döndürür; yanıt alınınca kütüphaneci yönergeyi izler ve durumu kendi basit belleğine kaydeder.

#### D.5 Konsolidasyon Protokolü ("uyku")
- **Tetikleme:** LLM çağrıları maliyetli olduğundan sabit kısa aralık YOK; proje bir günden fazla kullanılmayınca veya sabit zamanlanmış saatlerde çalışır.
- **İşler:** yeni girdileri birleştir, tekilleştir (dedupe), özetleri yeniden üret, önemli öğeleri üst katmanlara terfi ettir, solmakta olanları en alta göm.
- **Skor formülü (Generative Agents + MemoryBank uyarlaması):** `score = w_r * recency + w_i * importance + w_rel * relevance + w_u * usage_frequency`; recency üssel decay (Generative Agents referansı 0.995 faktör; deneyim katmanı için decay ~0, oynak bilgi için yüksek). Importance yazma anında kütüphaneci tarafından 1-10 ölçeğinde atanır.
- **Sessiz düşürme:** Zamandan bağımsız bir geçişte eşik altı öğeler sessizce DROP edilir; böylece bellek taze ve ileriye dönük kalır, daha rafine bilgi büyür.
- **Deneyim terfisi:** Tekrarlanan/doğrulanan dersler zamanla "deneyim" katmanına (L4) terfi eder; küçük düzeltmeler dışında zamandan etkilenmez. Bir işi yapış biçimi, birikmiş deneyimle ustalığa dönüşür.

#### D.6 Ingestion Boru Hattı
PDF/DOCX -> metin çıkarımı -> chunk -> embedding -> L0 kaydı -> RAPTOR tarzı küme özeti (L2) -> Graphiti varlık/ilişki çıkarımı (L1) -> ipucu bağları. Yerel embedding (ör. ONNX veya bge) gizlilik için tercih edilir. Hatalardan çıkarılan dersler de aynı boru hattından `register_lesson` ile girer.

#### D.7 Eşzamanlılık / Olay Günlüğü
Append-only event log + konsolidasyon modeli önerilir (kullanıcının değerlendirmesiyle uyumlu). Her yazma bir olaydır; konsolidasyon olayları maddeleştirir. Tek kullanıcı için bile bu, çoklu bilgisayar (A/B) ve çoklu CLI'den gelen eşzamanlı yazmaları çakışmasız birleştirir. Kullanıcı bazlı bellek (paylaşılan projelerde bile) şimdilik varsayılan; ileride ekip için tenant izolasyonu eklenir.

#### D.8 Gelecek: "Paketlenmiş Bellek Paylaşımı" (packed memory share)
Tek seferlik, paketlenmiş bellek paketleri (bir fikri paylaşmak gibi, tüm belleği değil) belirli koşullarda bellek sistemleri arasında paylaşılır; alıcı modül bunu içe alır. Cognee'nin COGX (Cognee eXchange) değişim formatı bunun mevcut en yakın örneğidir: `cognee.export(..., format="cogx")` ile dışa aktarır ve Mem0, LangMem, Letta, Zep, Graphiti'den yerleşik adaptörlerle içe alır. Tasarım referansı olarak kullanılmalıdır. Güvenli tasarım: imzalı, sürümlü, kaynak-atıflı paket; alıcı tarafta karantina + kütüphaneci onayı ile ingest.

#### D.9 Kütüphaneci Model Seçimi ve Maliyet (Eylül 2026 verileri)

| Model | $/M girdi | $/M çıktı | Bağlam | JSON+araç | Açık ağırlık |
|---|---|---|---|---|---|
| Claude Haiku 4.5 | 1.00 | 5.00 | 200K | Evet | Hayır |
| GPT-5 nano | 0.05 | 0.40 | ~400K | Evet | Hayır |
| GPT-5 mini | 0.25 | 2.00 | ~400K | Evet | Hayır |
| GPT-5.6 Luna (güncel ucuz tier) | 0.20 | 1.20 | ~1.05M | Evet | Hayır |
| Gemini 3.5 Flash | 0.75 | 4.50 | 1M | Evet | Hayır |
| Gemini 3.6 Flash | 0.75 | 3.75 | 1M | Evet | Hayır |
| DeepSeek V4 Flash | 0.14 (cache-miss), ~0.003 (cache-hit) | 0.28 | 1M | Evet | Kısmen |
| Qwen3.5 Flash (hosted) / açık ağırlık self-host | ~0.10 / 0 | ~0.40 / 0 | 1M | Evet | Evet (çoğu model) |
| Mistral Small 4 | ~0.15 / 0 self-host | ~0.60 / 0 | 256K | Evet | Evet (Apache 2.0) |

**Tavsiye:** Gizlilik birinci öncelik olduğu için birincil öneri kendi sunucunuzda açık ağırlıklı model (Qwen veya Mistral Small 4, vLLM ile) çalıştırmaktır; token başına maliyet sıfır, veri dışarı çıkmaz, Mistral Small 4 Apache 2.0'dır. Bulut kabul edilebilir işler için (yalnızca hassas olmayan konsolidasyon) DeepSeek V4 Flash, otomatik prefix cache ile cache-hit fiyatlaması (~0.003 USD/M girdi) ve 1M bağlamı sayesinde açık ara en ucuzudur; sabit önekli (system prompt + şema) promptlar %70+ cache-hit yakalarsa maliyet neredeyse yok olur. Gemini Flash yüksek yapılandırılmış çıktı güvenilirliği ve 1M bağlam ister ama daha pahalıdır. Kullanıcının adayları (Gemini Flash, DeepSeek) doğru sezgidir; DeepSeek fiyat/performansta önde, ancak self-host gizlilik açısından üstündür. Kütüphaneci işinin çoğu asenkron (yazma/konsolidasyon) olduğundan gecikme kritik değil, bu self-host lehine güçlü bir argümandır.

**Tahmini aylık maliyet (tek yoğun kullanıcı):** VPS (Hetzner, EU/İsviçre, ~8 vCPU/16-32 GB, Postgres+pgvector+AGE + MCP sunucusu) ~20-50 USD/ay. Kütüphaneci self-host ise ek GPU maliyeti (küçük modeller için CPU/quantize mümkün) veya bulut API ile konsolidasyon ~5-20 USD/ay (DeepSeek cache-hit ile alt uçta). Toplam kaba tahmin ~30-70 USD/ay.

### Risk Listesi
- **Kütüphaneci maliyet kaçağı:** Konsolidasyonun sık tetiklenmesi maliyeti patlatır. Azaltım: tembel/zamanlanmış tetikleme, cache-hit dostu sabit önek promptlar.
- **Sessiz düşürmede önemli bilgi kaybı:** A-MEM bulgusu (forgetting görev performansını düşürebilir). Azaltım: yalnızca düşük skor + düşük kullanım + oynak sınıf; deneyim/kararlı bilgi asla düşmez; düşürmeden önce tombstone.
- **Çelişki yanlış çözümü:** Kütüphaneci yanlış edge invalidation yapabilir. Azaltım: Graphiti bi-temporal (eskiyi silme, geçersizleştir), tam provenance.
- **MCP kimlik/gizlilik:** Uzak sunucu sızıntısı. Azaltım: at-rest şifreleme, bearer/OAuth, EU/İsviçre barındırma, yerel embedding.
- **CLI API değişiklikleri:** antigravity-cli yeni ve config yolları oturmamış; url tip hatası sessiz. Azaltım: sağlık kontrolü aracı, sürüm sabitleme.
- **Prompt injection (belge ingest):** Kötü amaçlı PDF. Azaltım: ingest karantinası, ham veriyi araç sonucu olarak izole etme, provenance zorunluluğu.

### Aşamalı Uygulama Yol Haritası
- **Faz 0 (MVP, 2-4 hafta):** Postgres+pgvector, tek streamable HTTP MCP sunucusu, `query` (token bütçeli) + `write` + `call_the_day`, proje ad alanı, üç CLI kaydı. LLM yok, sadece hızlı yol.
- **Faz 1:** Graphiti entegrasyonu (L1, bi-temporal), RAPTOR özetleri (L2), ipucu id'leri ve drilldown.
- **Faz 2:** Kütüphaneci LLM (self-host Qwen/Mistral Small 4), yazma yerleşimi, çelişki çözümü, cross-project kontrol, risk_check (proaktif flashback).
- **Faz 3:** Konsolidasyon protokolü (skor, sessiz düşürme, deneyim terfisi), ingestion boru hattı (PDF/DOCX).
- **Faz 4:** Deneyim katmanı (L4) olgunlaştırma, event log tabanlı çoklu bilgisayar senkronu, gelecek packed memory share (COGX tarzı).

### Değerlendirme Planı
- **Kıyaslamalar:** LoCoMo (çok oturumlu diyalog, tek/çok atlamalı, zamansal, çelişkili, adversarial isim değişimi; 10 konuşma, 1.986 QA), LongMemEval (asistan/kullanıcı hatırlama, zamansal muhakeme, bilgi güncelleme), BEAM (1M token üretim ölçeği). Mem0'ın açık kaynak memory-benchmarks paketi (Docker + Qdrant) bu üçünü çalıştırabilir.
- **Üretim metrikleri:** Recall@5 > 0.85 (hedef 0.92), Precision > 0.95, çelişki oranı < 0.02. Token verimi: sorgu başına < 7k token hedefi (Mem0 referansı ortalama 6.956).
- **Özel testler:** proaktif risk_check'in gerçek geçmiş hataları yakalama oranı; deneyim terfisinin zamanla doğruluğu koruması; sessiz düşürmenin görev performansını düşürmemesi (A-MEM regresyon testi).

## Tavsiyeler (Recommendations)

1. **Hemen (Faz 0):** Postgres + pgvector + Apache AGE'yi tek Docker yığını olarak Hetzner (EU/İsviçre) üzerinde kurun; token bütçeli `query`/`write`/`call_the_day` araçlarıyla streamable HTTP MCP sunucusu yazın; üç CLI'yi tek URL ile bağlayın. Bu, LLM olmadan bile değer üretir ve mimarinin belkemiğidir.
2. **Graphiti'yi yeniden kullanın**, sıfırdan grafik yazmayın: bi-temporal geçerlilik, çelişki tespiti ve provenance kullanıcının zaman/çelişki gereksinimlerini karşılar. Katman/ipucu/kütüphaneci mantığını üstüne özel inşa edin. Grafik DB olarak FalkorDB (Graphiti destekli) veya Postgres+AGE tercih edin; Kuzu arşivlendiği için kaçının.
3. **Kütüphaneciyi self-host açık ağırlıklı modelle başlatın** (Qwen veya Mistral Small 4, vLLM). Gizlilik korunur, gecikme kritik değildir çünkü iş asenkrondur. Hassas olmayan toplu konsolidasyon için DeepSeek V4 Flash'ı cache-hit fiyatıyla yedek olarak değerlendirin.
4. **Kancalara bağımlı olmayın**; proaktif flashback'i `risk_check` MCP aracı + kütüphaneci tetikleme ile kurun. Kancaları yalnızca opsiyonel belgeleyin.
5. **Sessiz düşürmeyi muhafazakar ayarlayın**: yalnızca düşük skor + düşük kullanım + oynak sınıf; deneyim ve kararlı bilgi bağışık. Her düşürmeden önce tombstone tutun.
6. **Değerlendirmeyi baştan kurun**: Mem0 memory-benchmarks ile LoCoMo/LongMemEval'i CI'da çalıştırın; Recall/Precision/çelişki eşiklerini karar kapıları yapın.

**Eşikleri değiştirecek ölçütler:** Recall@5 0.85 altına düşerse embedding modelini yükseltin (varsayılan text-embedding-3-small SOTA değil). Çelişki oranı 0.02 üstüne çıkarsa kütüphaneci çelişki çözümünü sıkılaştırın. Aylık LLM maliyeti 50 USD'yi aşarsa konsolidasyonu tamamen self-host'a taşıyın. Ekip kullanımına geçilirse kullanıcı-bazlı bellekten olay-günlüğü tabanlı çoklu kullanıcı + tenant izolasyonuna geçin.

## Uyarılar (Caveats)
- 2026 model sürüm adları ve fiyatları hızla değişiyor; Claude Haiku 4.5, GPT-5 mini/nano ve GPT-5.6 Luna, Gemini Flash (3.5/3.6/3.8), DeepSeek V4 ve Qwen/Mistral sürüm/fiyatları yayın anında resmi sayfalardan (anthropic.com, openai.com, deepseek.com, Alibaba Cloud Model Studio, mistral.ai) doğrulanmalı. Rapordaki fiyatların çoğu üçüncü taraf toplayıcılardan (OpenRouter, pricepertoken, benchlm, artificialanalysis) gelir; fatura-düzeyi rakam için resmi sayfalar esastır. GPT-5 mini/nano bağlam penceresi kaynaklar arasında çelişiyor (400K vs 272K).
- Kıyaslama skorları (Mem0 LoCoMo 92.5 vb.) satıcının yönetilen platformundan olup açık kaynak sürümde birebir tekrarlanmayabilir; kendi iş yükünüzde çalıştırın.
- antigravity-cli çok yeni; config yolları ve davranış büyük ölçüde topluluk kaynaklarına (Medium, DEV, GitHub issue) dayanıyor, resmi CLI dokümantasyonu henüz eksik olabilir. Resmi Google Cloud dokümanı mcp_config.json formatını doğruluyor.
- "İnsan benzeri" decay ve deneyim terfisi hâlâ aktif araştırma; A-MEM unutmanın performansı düşürebildiğini gösteriyor, bu yüzden muhafazakar eşikler ve regresyon testleri şart.
- Zep Community Edition kullanımdan kaldırıldı; self-host artık Graphiti + kendi grafik DB'niz demektir, bu operasyonel bir yüktür. Bu, MVP'de Graphiti'yi kütüphane olarak gömüp grafik DB'yi kendi Postgres/FalkorDB yığınınıza bağlama kararını destekler.