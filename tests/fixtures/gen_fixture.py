#!/usr/bin/env python3
"""Deterministic G3 recall fixture generator (PHASE0-SPEC.md §7).

Writes a synthetic "project world" for the Recall@5 gate:

  tests/fixtures/g3/items.jsonl    2,000 items in project ``fx-main`` (+ 400 decoy
                                   items in ``fx-other``), each a 600-900 word TR/DE/EN
                                   dev-memory note (architecture, bug fix, env config,
                                   decision, incident, session, perf, deploy) that
                                   chunks into ~5 x 400-token pieces.
  tests/fixtures/g3/queries.jsonl  100 queries (34 TR / 33 DE / 33 EN, 25 identifier-
                                   heavy) generated from a DIFFERENT template than the
                                   gold item, with morphological variants and synonym
                                   paraphrases; gold = originating logical_key.
  tests/fixtures/g3/SHA256SUMS     freeze hashes, asserted by test_fixture_frozen.py.

stdlib only; ``random.Random(SEED)``; never uses set/dict-hash iteration order for any
random decision, so output is byte-identical across runs and Python versions >= 3.12.

Regenerate:  python3 tests/fixtures/gen_fixture.py [--out DIR]
Changing anything that alters the output requires a docs/decisions/DECISIONS.md entry.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
import re
import sys
from pathlib import Path

SEED = 20260922
N_MAIN = 2000
N_OTHER = 400  # ~5 chunks each -> ~2,000 decoy chunks
MAIN_PROJECT = "fx-main"
OTHER_PROJECT = "fx-other"
N_QUERIES = {"tr": 34, "de": 33, "en": 33}
N_HEAVY = {"tr": 9, "de": 8, "en": 8}  # 25 identifier-heavy
LANGS = ("tr", "de", "en")
TOPICS = ("arch", "bugfix", "envconfig", "decision", "incident", "session", "perf", "deploy")
MIN_WORDS, MAX_WORDS = 300, 900
WORD_TARGET = (580, 790)
MIN_PARAPHRASE_RATIO = 0.60

KINDS_BY_TOPIC = {
    "arch": ["doc_chunk", "fact"],
    "bugfix": ["lesson", "episode"],
    "envconfig": ["fact"],
    "decision": ["fact", "lesson"],
    "incident": ["episode", "experience"],
    "session": ["session_note"],
    "perf": ["experience", "lesson"],
    "deploy": ["doc_chunk", "fact"],
}
STABLE_KINDS = {"fact", "lesson", "doc_chunk"}

# --------------------------------------------------------------------------- entities
ENV_PREFIX = ["APP", "HLM", "SVC", "API", "DB", "CACHE", "QUEUE", "AUTH", "MAIL", "LOG"]
ENV_MID = ["DB", "REDIS", "HTTP", "GRPC", "S3", "SMTP", "JWT", "OTEL", "PG", "KAFKA"]
ENV_SUFFIX = [
    "DSN",
    "URL",
    "HOST",
    "PORT",
    "TIMEOUT_MS",
    "POOL_SIZE",
    "SECRET",
    "TOKEN",
    "BUCKET",
    "REGION",
    "MAX_RETRIES",
    "TTL",
]
PATH_DIRS = [
    "src/hlmemo/server",
    "src/hlmemo/worker",
    "src/hlmemo/retrieval",
    "src/hlmemo/auth",
    "src/hlmemo/storage",
    "migrations/versions",
    "deploy/compose",
    "tests/gates",
    "scripts/ops",
    "config",
]
PATH_FILES = [
    "app.py",
    "routes.py",
    "settings.py",
    "pool.py",
    "embed.py",
    "rank.py",
    "tokens.py",
    "outbox.py",
    "healthz.py",
    "cursor.py",
    "0001_init.py",
    "0002_hnsw.py",
    "docker-compose.yml",
    "worker.env",
    "nginx.conf",
    "backup.sh",
    "rotate_keys.sh",
    "conftest.py",
    "test_budget.py",
    "limits.yaml",
    "librarian.toml",
    "providers.json",
]
PEOPLE = [
    "Ayşe Demir",
    "Mehmet Kaya",
    "Elif Şahin",
    "Burak Yıldız",
    "Zeynep Çelik",
    "Can Aydın",
    "Selin Koç",
    "Emre Arslan",
    "Lukas Weber",
    "Anna Schmidt",
    "Jonas Becker",
    "Lena Fischer",
    "Felix Wagner",
    "Mia Hoffmann",
    "Paul Richter",
    "Sophie Krüger",
    "Alice Turner",
    "Bob Marsh",
    "Priya Nair",
    "Tom Reed",
    "Grace Liu",
    "Omar Haddad",
    "Nadia Petrova",
    "Sam O'Neill",
]
PORTS = [8080, 8443, 9090, 5432, 6379, 50051, 4317, 5000]
ADJ_TR = ["geçici", "kalıcı", "kırılgan", "idempotent", "asenkron", "kademeli", "tutarlı", "beklenmedik"]
ADJ_DE = [
    "vorübergehende",
    "dauerhafte",
    "fragile",
    "idempotente",
    "asynchrone",
    "schrittweise",
    "konsistente",
    "unerwartete",
]
ADJ_EN = [
    "transient",
    "persistent",
    "brittle",
    "idempotent",
    "asynchronous",
    "gradual",
    "consistent",
    "unexpected",
]
COMP = {
    "tr": [
        "bağlantı havuzu",
        "önbellek katmanı",
        "kuyruk tüketicisi",
        "kimlik doğrulama ara katmanı",
        "zamanlayıcı",
        "göç betiği",
        "sağlık kontrolü",
        "oran sınırlayıcı",
        "olay günlüğü",
        "gömme işçisi",
    ],
    # all masculine so that "der/den/des {comp}" in the DE sentence pools stays grammatical
    "de": [
        "Verbindungspool",
        "Cache-Layer",
        "Queue-Consumer",
        "Auth-Filter",
        "Scheduler",
        "Migrationslauf",
        "Healthcheck",
        "Rate-Limiter",
        "Ereignisstrom",
        "Embedding-Worker",
    ],
    "en": [
        "connection pool",
        "cache layer",
        "queue consumer",
        "auth middleware",
        "scheduler",
        "migration script",
        "health check",
        "rate limiter",
        "event log",
        "embedding worker",
    ],
}
HEADINGS = {
    "tr": ["Bağlam", "Belirtiler", "Analiz", "Çözüm", "Sonraki adımlar"],
    "de": ["Kontext", "Symptome", "Analyse", "Lösung", "Nächste Schritte"],
    "en": ["Context", "Symptoms", "Analysis", "Resolution", "Follow-ups"],
}
ENV_LABEL = {"tr": "Ortam", "de": "Umgebung", "en": "Environment"}

# --------------------------------------------------------------------------- synonyms
# index 0 = canonical word used in item TITLES; queries draw from index 1.. only.
SYN = {
    "tr": {
        "error": ["hatası", "sorun", "arıza", "aksaklık"],
        "config": ["ortam yapılandırması", "ayarları", "değişkenleri", "konfigürasyonu"],
        "arch": ["mimari notu", "yapısı", "tasarımı", "bileşen şeması"],
        "decision": ["Karar", "seçilen yaklaşım", "alınan karar", "tercih"],
        "incident": ["kesintisi", "arıza", "olay", "çökme"],
        "session": ["Oturum notu", "çalışma notları", "günün özeti", "oturumda yapılanlar"],
        "perf": ["performans iyileştirmesi", "hız kazanımı", "gecikme düşürme", "verim çalışması"],
        "deploy": ["dağıtım rehberi", "yükseltme adımları", "canlıya alma", "sürüm çıkarma"],
        "service": ["servisi", "bileşeni", "uygulaması", "modülü"],
    },
    "de": {
        "error": ["Fehler", "Störung", "Panne", "Fehlfunktion"],
        "config": [
            "Umgebungskonfiguration",
            "Einstellungen",
            "Umgebungsvariablen",
            "Konfigurationsschlüssel",
        ],
        "arch": ["Architekturnotiz", "Aufbau", "Entwurf", "Bauplan"],
        "decision": ["Entscheidung", "Beschluss", "Ansatz", "Entschluss"],
        "incident": ["Ausfall", "Vorfall", "Zusammenbruch", "Stillstand"],
        "session": ["Sitzungsnotiz", "Arbeitsnotizen", "Tagesnotizen", "Pairing-Notizen"],
        "perf": ["Performance-Optimierung", "Beschleunigung", "Latenzsenkung", "Durchsatzarbeit"],
        "deploy": ["Deployment-Leitfaden", "Inbetriebnahme", "Auslieferung", "Bereitstellung"],
        "service": ["Dienst", "Komponente", "Anwendung", "Modul"],
    },
    "en": {
        "error": ["error", "issue", "failure", "defect"],
        "config": ["environment configuration", "settings", "env variables", "config keys"],
        "arch": ["architecture note", "structure", "design", "component layout"],
        "decision": ["Decision", "chosen approach", "resolution taken", "call we made"],
        "incident": ["outage", "breakdown", "incident", "crash"],
        "session": ["Session note", "working notes", "day summary", "session outcomes"],
        "perf": ["performance improvement", "speed-up", "latency reduction", "throughput work"],
        "deploy": ["deployment guide", "upgrade steps", "rollout", "release procedure"],
        "service": ["service", "component", "application", "module"],
    },
}

# --------------------------------------------------------------------------- titles
TITLE = {
    "tr": {
        "arch": "{svc} servisinin {S_arch}: {comp}",
        "bugfix": "{svc} servisinde {err} {S_error} düzeltildi",
        "envconfig": "{svc} için {env} {S_config}",
        "decision": "{S_decision}: {svc} servisinde {comp} yaklaşımı",
        "incident": "Olay raporu: {svc} servisinde {err} {S_incident}",
        "session": "{S_session} {date}: {svc} servisi",
        "perf": "{svc} servisinde {comp} {S_perf}",
        "deploy": "{svc} servisinin {ver} {S_deploy}",
    },
    "de": {
        "arch": "{S_arch} zum Dienst {svc}: {comp}",
        "bugfix": "{err}-{S_error} im Dienst {svc} behoben",
        "envconfig": "{S_config} für {svc}: {env}",
        "decision": "{S_decision}: {comp}-Ansatz im Dienst {svc}",
        "incident": "Störungsbericht: {err}-{S_incident} im Dienst {svc}",
        "session": "{S_session} {date}: Dienst {svc}",
        "perf": "{S_perf} für {comp} in {svc}",
        "deploy": "{S_deploy} für {svc} {ver}",
    },
    "en": {
        "arch": "{svc} service {S_arch}: {comp}",
        "bugfix": "Fixed {err} {S_error} in the {svc} service",
        "envconfig": "{S_config} for {svc}: {env}",
        "decision": "{S_decision}: {comp} approach in the {svc} service",
        "incident": "Incident report: {err} {S_incident} in {svc}",
        "session": "{S_session} {date}: {svc} service",
        "perf": "{comp} {S_perf} in the {svc} service",
        "deploy": "{S_deploy} for {svc} {ver}",
    },
}

# --------------------------------------------------------------------------- body sentence pools
GEN = {
    "tr": [
        "{person}, {date} tarihinde {svc} servisinin {comp} bileşeninde {adj} bir davranış fark etti.",
        "İlk inceleme {env} değişkeninin {path} içinde beklenenden farklı okunduğunu gösterdi.",
        "Loglarda {err} kodu {num} kez tekrarlanıyordu ve her seferinde {comp} zaman aşımına uğruyordu.",
        "{svc2} servisi aynı {comp_alt} bileşenini paylaştığı için etkisi oraya da yansıdı.",
        "Ekip olarak {adj} bir geçici çözüm yerine kök nedeni bulmayı tercih ettik.",
        (
            "Yeniden üretim adımları: {env} değerini boş bırak, {svc} servisini "
            "başlat ve {port} portuna istek gönder."
        ),
        "{path} içindeki {comp} başlatma sırası {ver} sürümünden beri değişmişti.",
        "Düzeltme, {env} için güvenli bir varsayılan eklemek ve {err} durumunu açıkça loglamak oldu.",
        "Testler {date} gecesi CI üzerinde yeşile döndü; incelemeyi {person} tamamladı.",
        "Bu not, aynı durumla karşılaşan herkes için {svc} servisinin {comp} davranışını özetler.",
        "{svc} servisi {ver} sürümünde {comp} için {num} saniyelik bir zaman aşımı kullanır.",
        "{env} değişkeni yalnızca {path} tarafından okunur; başka bir yerde tekrar tanımlanmamalıdır.",
        "{person} bu kararı {date} toplantısında önerdi ve itiraz gelmedi.",
        "Üretim ortamında {svc} ile {svc2} arasındaki trafik {port} portu üzerinden gRPC ile akar.",
        "{err} kodu gördüğünüzde önce {env} değerini, sonra {path} dosyasındaki {comp} ayarını kontrol edin.",
        (
            "{comp_alt} bileşeninin {adj} olması, yeniden başlatmalarda {num} "
            "milisaniyelik bir gecikmeye yol açıyor."
        ),
        "Bu davranış {num} kullanıcıyı etkiledi ve {date} tarihine kadar fark edilmedi.",
        "Alternatif olarak {comp} bileşenini {svc2} tarafına taşımayı değerlendirdik ama vazgeçtik.",
        "{path} dosyasına eklenen {num2} satırlık yama, {err} hatasının tekrarını engelledi.",
        "Ölçümler {comp} gecikmesinin {num} ms'den {num2} ms'ye düştüğünü gösterdi.",
        "{svc} servisinin sağlık kontrolü artık {env} eksikse başlangıçta hata veriyor.",
        (
            "Yapılandırma dosyası {path} sürüm kontrolünde tutulur; gizli değerler "
            "yalnızca {env} üzerinden gelir."
        ),
        "{person} ile yaptığımız eşli programlama oturumunda {comp} için birim testleri yazdık.",
        "Bilinen kısıt: {svc} servisi {comp} olmadan {adj} modda çalışamaz.",
        "Bir sonraki sürümde {err} kodunu daha açıklayıcı bir mesajla değiştirmeyi planlıyoruz.",
        "{svc2} servisinin sahibi, aynı sorunun kendi tarafında görülmediğini doğruladı.",
        "Geriye dönük uyumluluk için {env} eski adıyla da {date} tarihine kadar okunmaya devam edecek.",
        "{comp} üzerindeki yük saniyede {num} isteği geçince {err} kodu ortaya çıkıyor.",
        "Bu kayıt {svc} servisinin {comp} bileşeni hakkında {adj} bir bilgi içerir; güncel tutulmalıdır.",
        "Dağıtım {date} günü {person} tarafından {ver} etiketiyle yapıldı ve geri alma planı hazırdı.",
        "{comp_alt} için yazılan entegrasyon testi {path} yanına eklendi ve {num2} saniyede tamamlanıyor.",
        (
            "Nöbetçi mühendis {err} gördüğünde {svc} servisini yeniden başlatmadan "
            "önce {env} değerini doğrulamalı."
        ),
    ],
    "de": [
        "{person} bemerkte am {date} ein {adj}s Verhalten im {comp} des Dienstes {svc}.",
        "Die erste Analyse zeigte, dass {env} in {path} anders gelesen wurde als erwartet.",
        "In den Logs tauchte der Code {err} {num} Mal auf, jedes Mal lief der {comp} in ein Timeout.",
        "Der Dienst {svc2} teilt sich den {comp_alt}, deshalb war er ebenfalls betroffen.",
        "Wir haben uns gegen einen {adj}n Workaround und für die Ursachenanalyse entschieden.",
        "Reproduktion: {env} leer lassen, {svc} starten und eine Anfrage an Port {port} schicken.",
        "Die Startreihenfolge des {comp} in {path} hatte sich seit Version {ver} geändert.",
        "Die Korrektur: ein sicherer Default für {env} und explizites Logging des Zustands {err}.",
        "Die Tests wurden in der Nacht zum {date} in der CI grün; {person} hat das Review abgeschlossen.",
        (
            "Diese Notiz fasst das Verhalten des {comp} im Dienst {svc} für alle "
            "zusammen, die denselben Fall sehen."
        ),
        "Der Dienst {svc} nutzt in Version {ver} ein Timeout von {num} Sekunden für den {comp}.",
        "{env} wird ausschließlich von {path} gelesen und darf nirgendwo sonst definiert werden.",
        "{person} hat diese Entscheidung im Meeting am {date} vorgeschlagen; es gab keinen Einwand.",
        "In Produktion läuft der Verkehr zwischen {svc} und {svc2} über gRPC auf Port {port}.",
        "Wer {err} sieht, prüft zuerst {env} und dann die {comp}-Einstellung in {path}.",
        "Weil der {comp_alt} {adj} ist, verzögert sich jeder Neustart um {num} Millisekunden.",
        "Das Verhalten betraf {num} Nutzer und blieb bis zum {date} unbemerkt.",
        "Alternativ hatten wir überlegt, den {comp} nach {svc2} zu verschieben, das aber verworfen.",
        "Der {num2}-zeilige Patch in {path} verhindert, dass {err} erneut auftritt.",
        "Messungen zeigten, dass die Latenz des {comp} von {num} ms auf {num2} ms gesunken ist.",
        "Der Healthcheck von {svc} schlägt jetzt beim Start fehl, wenn {env} fehlt.",
        "Die Konfigurationsdatei {path} liegt in der Versionskontrolle; Geheimnisse kommen nur über {env}.",
        "Im Pairing mit {person} haben wir Unit-Tests für den {comp} geschrieben.",
        "Bekannte Einschränkung: {svc} kann ohne {comp} nicht im {adj}n Modus laufen.",
        "Im nächsten Release soll {err} durch eine sprechendere Meldung ersetzt werden.",
        "Der Owner von {svc2} bestätigte, dass dasselbe Problem dort nicht auftritt.",
        "Aus Kompatibilitätsgründen wird {env} bis zum {date} auch unter dem alten Namen gelesen.",
        "Sobald die Last auf dem {comp} {num} Anfragen pro Sekunde übersteigt, erscheint {err}.",
        "Dieser Eintrag enthält eine {adj} Information über den {comp} von {svc} und muss aktuell bleiben.",
        "Das Deployment erfolgte am {date} durch {person} mit dem Tag {ver}; ein Rollback-Plan lag bereit.",
        "Der Integrationstest für den {comp_alt} liegt neben {path} und läuft in {num2} Sekunden durch.",
        "Bei {err} prüft der Bereitschaftsdienst {env}, bevor {svc} neu gestartet wird.",
    ],
    "en": [
        "{person} noticed {adj} behaviour in the {comp} of the {svc} service on {date}.",
        "The first pass showed that {env} was being read differently than expected inside {path}.",
        "The logs repeated code {err} {num} times, and each time the {comp} hit its timeout.",
        "The {svc2} service shares the same {comp_alt}, so the effect spilled over there too.",
        "As a team we chose to find the root cause instead of shipping a {adj} workaround.",
        "Repro steps: leave {env} empty, start {svc}, and send a request to port {port}.",
        "The start-up order of the {comp} in {path} had changed since version {ver}.",
        "The fix was to add a safe default for {env} and to log the {err} condition explicitly.",
        "Tests went green on CI the night of {date}; {person} finished the review.",
        "This note summarises the {comp} behaviour of the {svc} service for anyone hitting the same case.",
        "The {svc} service uses a {num} second timeout for the {comp} as of version {ver}.",
        "{env} is read only by {path}; it must not be redefined anywhere else.",
        "{person} proposed this in the {date} meeting and nobody objected.",
        "In production, traffic between {svc} and {svc2} flows over gRPC on port {port}.",
        "When you see {err}, check {env} first, then the {comp} setting in {path}.",
        "Because the {comp_alt} is {adj}, every restart adds about {num} milliseconds.",
        "This behaviour affected {num} users and went unnoticed until {date}.",
        "We considered moving the {comp} into {svc2} as an alternative but decided against it.",
        "The {num2}-line patch in {path} stops {err} from recurring.",
        "Measurements showed {comp} latency dropping from {num} ms to {num2} ms.",
        "The {svc} health check now fails at start-up if {env} is missing.",
        "The config file {path} lives in version control; secrets only come in through {env}.",
        "During a pairing session with {person} we wrote unit tests for the {comp}.",
        "Known limitation: {svc} cannot run in {adj} mode without the {comp}.",
        "In the next release we plan to replace {err} with a more descriptive message.",
        "The owner of {svc2} confirmed the same problem does not show up on their side.",
        "For backwards compatibility {env} keeps being read under its old name until {date}.",
        "Once load on the {comp} exceeds {num} requests per second, {err} shows up.",
        "This record holds a {adj} piece of knowledge about the {comp} of {svc} and must be kept current.",
        "The deployment was done on {date} by {person} with tag {ver}, with a rollback plan ready.",
        "The integration test for the {comp_alt} sits next to {path} and finishes in {num2} seconds.",
        "On {err} the on-call engineer verifies {env} before restarting {svc}.",
    ],
}

OPEN = {
    "tr": {
        "arch": [
            (
                "{svc} servisinin mimarisi {comp} etrafında şekillenir ve {svc2} ile "
                "{port} portu üzerinden konuşur."
            ),
            "Bu doküman {svc} servisinin bileşenlerini ve {path} altındaki modül düzenini açıklar.",
            "{svc} için tasarım hedefi, {comp} bileşenini {adj} tutmak ve {env} üzerinden yapılandırmaktı.",
        ],
        "bugfix": [
            "{svc} servisinde {err} hatası {date} tarihinde {person} tarafından bildirildi.",
            "Hata: {comp} bileşeni {env} boşken {err} ile çöküyordu.",
            "Bu kayıt {svc} servisindeki {err} hatasının düzeltilmesini anlatır.",
        ],
        "envconfig": [
            "{svc} servisinin ortam yapılandırması {env} değişkeni ve {path} dosyası ile belirlenir.",
            "{env} anahtarı {svc} servisinin {comp} bileşenini yapılandırır; yanlış değer {err} üretir.",
            "Bu not {svc} için gerekli ortam değişkenlerini ve varsayılanlarını listeler.",
        ],
        "decision": [
            "Karar: {svc} servisinde {comp} için {adj} yaklaşımı benimsiyoruz.",
            "{date} tarihinde {person} ile {svc} servisinin {comp} stratejisine karar verdik.",
            "Bu karar kaydı {svc} ve {svc2} arasındaki {comp} sorumluluğunu netleştirir.",
        ],
        "incident": [
            "Olay: {date} günü {svc} servisi {err} koduyla {num} dakika boyunca yanıt vermedi.",
            "{person} nöbetteyken {svc} servisinde {comp} kaynaklı bir kesinti yaşandı.",
            "Bu olay raporu {svc} servisindeki {err} kesintisinin zaman çizelgesini içerir.",
        ],
        "session": [
            "Oturum notu ({date}): {svc} servisinde {comp} üzerinde çalışıldı.",
            "Bugün {person} ile {svc} servisinin {path} modülünü ele aldık.",
            "Bu oturumda {svc} için {env} ve {err} etrafındaki açık işler tartışıldı.",
        ],
        "perf": [
            "{svc} servisinin {comp} bileşeninde performans ölçümleri {date} tarihinde yapıldı.",
            "Performans notu: {svc} servisi {comp} altında saniyede {num} istekle sınırlanıyordu.",
            "Bu deney {svc} servisindeki {comp} gecikmesini {adj} bir yöntemle azaltmayı hedefledi.",
        ],
        "deploy": [
            "{svc} servisinin dağıtım süreci {path} altındaki compose tanımıyla yürütülür.",
            "Dağıtım rehberi: {svc} servisini {ver} sürümüne {env} ayarıyla yükseltme.",
            "Bu doküman {svc} servisinin {date} tarihli {ver} dağıtımını belgeler.",
        ],
    },
    "de": {
        "arch": [
            (
                "Die Architektur von {svc} ist um den {comp} herum gebaut und spricht "
                "mit {svc2} über Port {port}."
            ),
            "Dieses Dokument beschreibt die Komponenten von {svc} und die Modulstruktur unter {path}.",
            "Designziel für {svc} war, den {comp} {adj} zu halten und über {env} zu konfigurieren.",
        ],
        "bugfix": [
            "Der Fehler {err} im Dienst {svc} wurde am {date} von {person} gemeldet.",
            "Fehlerbild: der {comp} stürzte mit {err} ab, sobald {env} leer war.",
            "Dieser Eintrag beschreibt die Behebung von {err} im Dienst {svc}.",
        ],
        "envconfig": [
            "Die Umgebungskonfiguration von {svc} wird durch {env} und die Datei {path} bestimmt.",
            "Der Schlüssel {env} konfiguriert den {comp} von {svc}; ein falscher Wert erzeugt {err}.",
            "Diese Notiz listet die nötigen Umgebungsvariablen für {svc} samt Defaults.",
        ],
        "decision": [
            "Entscheidung: für den {comp} in {svc} wählen wir den {adj}n Ansatz.",
            "Am {date} haben wir mit {person} die {comp}-Strategie für {svc} festgelegt.",
            "Dieser Beschluss klärt die Zuständigkeit für den {comp} zwischen {svc} und {svc2}.",
        ],
        "incident": [
            "Vorfall: am {date} antwortete {svc} {num} Minuten lang nur noch mit {err}.",
            "Während {person} Bereitschaft hatte, kam es in {svc} zu einem Ausfall durch den {comp}.",
            "Dieser Störungsbericht enthält die Zeitleiste des {err}-Ausfalls in {svc}.",
        ],
        "session": [
            "Sitzungsnotiz ({date}): Arbeit am {comp} im Dienst {svc}.",
            "Heute haben wir mit {person} das Modul {path} von {svc} durchgesehen.",
            "In dieser Sitzung wurden die offenen Punkte rund um {env} und {err} für {svc} besprochen.",
        ],
        "perf": [
            "Die Performance-Messungen am {comp} von {svc} fanden am {date} statt.",
            "Performance-Notiz: {svc} war durch den {comp} auf {num} Anfragen pro Sekunde begrenzt.",
            "Dieses Experiment sollte die {comp}-Latenz in {svc} mit einer {adj}n Methode senken.",
        ],
        "deploy": [
            "Das Deployment von {svc} läuft über die Compose-Definition unter {path}.",
            "Deployment-Leitfaden: {svc} mit der Einstellung {env} auf {ver} heben.",
            "Dieses Dokument beschreibt das Deployment {ver} von {svc} vom {date}.",
        ],
    },
    "en": {
        "arch": [
            "The {svc} architecture is built around the {comp} and talks to {svc2} over port {port}.",
            "This document describes the components of {svc} and the module layout under {path}.",
            "The design goal for {svc} was to keep the {comp} {adj} and configure it through {env}.",
        ],
        "bugfix": [
            "The {err} error in the {svc} service was reported by {person} on {date}.",
            "Symptom: the {comp} crashed with {err} whenever {env} was empty.",
            "This record describes how {err} in the {svc} service was fixed.",
        ],
        "envconfig": [
            "The environment configuration of {svc} is defined by {env} and the file {path}.",
            "The key {env} configures the {comp} of {svc}; a wrong value produces {err}.",
            "This note lists the environment variables {svc} needs along with their defaults.",
        ],
        "decision": [
            "Decision: for the {comp} in {svc} we adopt the {adj} approach.",
            "On {date} we settled the {comp} strategy for {svc} together with {person}.",
            "This decision record clarifies who owns the {comp} between {svc} and {svc2}.",
        ],
        "incident": [
            "Incident: on {date} the {svc} service answered only with {err} for {num} minutes.",
            "While {person} was on call, {svc} suffered an outage caused by the {comp}.",
            "This incident report holds the timeline of the {err} outage in {svc}.",
        ],
        "session": [
            "Session note ({date}): worked on the {comp} in the {svc} service.",
            "Today we went through the {path} module of {svc} with {person}.",
            "This session covered the open items around {env} and {err} for {svc}.",
        ],
        "perf": [
            "Performance measurements on the {comp} of {svc} were taken on {date}.",
            "Performance note: {svc} was capped at {num} requests per second by the {comp}.",
            "This experiment aimed to cut {comp} latency in {svc} with a {adj} method.",
        ],
        "deploy": [
            "The deployment of {svc} runs through the compose definition under {path}.",
            "Deployment guide: upgrading {svc} to {ver} with the {env} setting.",
            "This document records the {ver} deployment of {svc} on {date}.",
        ],
    },
}

CLOSE = {
    "tr": [
        "Sonuç olarak {svc} servisi için {comp} artık {adj} kabul ediliyor.",
        "Açık sorular {person} tarafından takip edilecek; hedef tarih {date}.",
        "Bu kayıt {svc} servisi hakkında güncel bilgiyi yansıtır ve {err} görülürse ilk başvurulacak yerdir.",
    ],
    "de": [
        "Unterm Strich gilt der {comp} von {svc} jetzt als {adj}.",
        "Offene Fragen verfolgt {person}; Zieltermin ist der {date}.",
        (
            "Dieser Eintrag spiegelt den aktuellen Stand von {svc} wider und ist "
            "bei {err} die erste Anlaufstelle."
        ),
    ],
    "en": [
        "Bottom line: the {comp} of {svc} is now considered {adj}.",
        "Open questions will be tracked by {person}; target date {date}.",
        "This record reflects the current state of {svc} and is the first place to look when {err} appears.",
    ],
}

# --------------------------------------------------------------------------- query templates
# Light (paraphrase) templates per topic; morphological slots: {svc_gen} {svc_loc} {svc_loc_adj}
# {svc_dat} {svc_abl} (TR), {svc_gen} {svc_dat} {svc_pl} (DE), {svc_pos} {svc_the} (EN).
# Synonym slots {S_*} are filled from SYN[lang][*][1:] (never the title word).
QLIGHT = {
    "tr": {
        "arch": [
            "{svc_gen} {S_arch} nasıl kurgulanmış, hangi bileşenler var?",
            "{svc_loc} kullanılan {S_arch} ve modül düzenini anlat",
            "{svc_gen} {S_arch} ile {svc2} arasındaki iletişim nasıl işliyor?",
        ],
        "bugfix": [
            "{svc_loc} çıkan {err} kaynaklı {S_error} nasıl giderildi?",
            "{err} koduyla ilgili {svc_loc_adj} {S_error} neden oluşuyordu?",
            "{svc_gen} {err} ile ilgili {S_error} yaması neyi değiştirdi?",
        ],
        "envconfig": [
            "{svc_gen} {S_config} nelerdir, {env} ne işe yarar?",
            "{env} anahtarı {svc_loc} neyi belirliyor?",
            "{svc_gen} {S_config} arasında hangileri zorunlu?",
        ],
        "decision": [
            "{svc_loc} {comp} konusunda {S_decision} neydi ve gerekçesi ne?",
            "{svc_gen} {comp} stratejisiyle ilgili {S_decision}",
            "{comp} sorumluluğu {svc} ile {svc2} arasında nasıl paylaştırıldı?",
        ],
        "incident": [
            "{svc_loc_adj} {err} kaynaklı {S_incident} sırasında neler oldu?",
            "{svc_gen} yanıt vermediği {S_incident} için zaman çizelgesi",
            "{err} yüzünden {svc_loc} yaşanan {S_incident} için kök neden neydi?",
        ],
        "session": [
            "{svc} ile ilgili {S_session} nelerdi?",
            "{svc_gen} {path} modülüyle ilgili son {S_session}",
            "{svc_loc} açık kalan işler ve tartışılan konular",
        ],
        "perf": [
            "{svc_gen} {comp} için yapılan {S_perf} ne getirdi?",
            "{svc_loc} yapılan {S_perf} ölçümleri ne gösterdi?",
            "{comp} yüzünden {svc_loc} görülen yavaşlık nasıl çözüldü?",
        ],
        "deploy": [
            "{svc} servisini {ver} sürümüne taşıma: {S_deploy} nasıl yapıldı?",
            "{svc_gen} {S_deploy} ve geri alma planı nasıl?",
            "{svc_loc} compose ile {S_deploy} nasıl yapılıyor?",
        ],
    },
    "de": {
        "arch": [
            "Wie ist der {S_arch} {svc_gen} und welche Komponenten gibt es?",
            "Beschreibe den {S_arch} und die Modulstruktur {svc_gen}",
            "Wie kommunizieren die {svc_pl} im {S_arch} mit {svc2}?",
        ],
        "bugfix": [
            "Wie wurde die {err}-{S_error} {svc_dat} behoben?",
            "Warum trat die {S_error} mit Code {err} {svc_dat} auf?",
            "Was hat der Patch für die {err}-{S_error} {svc_gen} geändert?",
        ],
        "envconfig": [
            "Welche {S_config} braucht {svc_nom} und wofür ist {env}?",
            "Was legt der Schlüssel {env} {svc_dat} fest?",
            "Welche {S_config} sind {svc_dat} Pflicht?",
        ],
        "decision": [
            "Welcher {S_decision} gilt für den {comp} {svc_dat} und warum?",
            "Was war der {S_decision} zur {comp}-Strategie {svc_gen}?",
            "Wie wurde die Zuständigkeit für den {comp} zwischen {svc} und {svc2} aufgeteilt?",
        ],
        "incident": [
            "Was passierte beim {err}-{S_incident} {svc_dat}?",
            "Zeitleiste des {S_incident}s, bei dem {svc_nom} nicht antwortete",
            "Was war die Ursache des {S_incident}s von {svc} wegen {err}?",
        ],
        "session": [
            "Was wurde in den {S_session} zu {svc} erledigt?",
            "Letzte {S_session} zum Modul {path} {svc_gen}",
            "Offene Punkte und besprochene Themen {svc_dat}",
        ],
        "perf": [
            "Was hat die {S_perf} am {comp} {svc_gen} gebracht?",
            "Was zeigten die Messungen zur {S_perf} {svc_dat}?",
            "Wie wurde die Langsamkeit durch den {comp} {svc_dat} gelöst?",
        ],
        "deploy": [
            "{S_deploy} für die {svc_pl} auf Version {ver}",
            "Wie sehen {S_deploy} und Rollback-Plan {svc_gen} aus?",
            "Wie läuft die {S_deploy} {svc_dat} mit Compose?",
        ],
    },
    "en": {
        "arch": [
            "How is {svc_pos} {S_arch} organised and which components exist?",
            "Describe {svc_the}'s {S_arch} and module layout",
            "How does the {S_arch} of {svc} communicate with {svc2}?",
        ],
        "bugfix": [
            "How was the {err} {S_error} in {svc_the} resolved?",
            "Why was {svc_the} failing with the {err} {S_error}?",
            "What did the patch for {svc_pos} {err} {S_error} change?",
        ],
        "envconfig": [
            "What {S_config} does {svc_the} need and what is {env} for?",
            "What does the key {env} control in {svc_the}?",
            "Which of {svc_pos} {S_config} are mandatory?",
        ],
        "decision": [
            "What was the {S_decision} for the {comp} in {svc_the} and why?",
            "The {S_decision} about {svc_pos} {comp} strategy",
            "How was ownership of the {comp} split between {svc} and {svc2}?",
        ],
        "incident": [
            "What happened during the {err} {S_incident} in {svc_the}?",
            "Timeline of the {S_incident} where {svc_the} stopped responding",
            "What was the root cause of the {svc} {S_incident} caused by {err}?",
        ],
        "session": [
            "What got done in the {S_session} on {svc}?",
            "Latest {S_session} on {svc_pos} {path} module",
            "Open items and topics discussed in {svc_the}",
        ],
        "perf": [
            "What did the {S_perf} on {svc_pos} {comp} achieve?",
            "What did the {S_perf} measurements in {svc_the} show?",
            "How was the slowness caused by the {comp} in {svc_the} solved?",
        ],
        "deploy": [
            "{S_deploy} for moving {svc_the} to {ver}",
            "What are {svc_pos} {S_deploy} and rollback plan?",
            "How is the {S_deploy} of {svc_the} done with compose?",
        ],
    },
}

# Identifier-heavy templates (>= 3 identifiers, minimal prose).
QHEAVY = {
    "tr": [
        "{err} {env} {path}",
        "{svc} {env} {err}",
        "{env} {path} {svc} {err} hatası",
        "{path} {err} {env}",
        "{svc_loc} {port} {env} {err}",
    ],
    "de": [
        "{err} {env} {path}",
        "{svc} {env} {err}",
        "{env} {path} {svc} {err} Fehler",
        "{path} {err} {env}",
        "{svc_dat} {port} {env} {err}",
    ],
    "en": [
        "{err} {env} {path}",
        "{svc} {env} {err}",
        "{env} {path} {svc} {err} error",
        "{path} {err} {env}",
        "{svc_the} {port} {env} {err}",
    ],
}

# --------------------------------------------------------------------------- morphology
TR_BACK = set("aıou")
TR_FRONT = set("eiöü")
TR_HARD = set("çfhkpsşt")


def tr_last_vowel(word: str) -> str:
    for ch in reversed(word.lower()):
        if ch in TR_BACK or ch in TR_FRONT:
            return ch
    return "e"


def tr_plural(word: str) -> str:  # -ler / -lar
    return word + ("lar" if tr_last_vowel(word) in TR_BACK else "ler")


def tr_locative(word: str) -> str:  # -de / -da / -te / -ta
    v = tr_last_vowel(word)
    d = "t" if word[-1].lower() in TR_HARD else "d"
    return word + d + ("a" if v in TR_BACK else "e")


def tr_genitive(word: str) -> str:  # -nin / -nın / -nun / -nün / -in / -ın / -un / -ün
    v = tr_last_vowel(word)
    vowel = {"a": "ı", "ı": "ı", "o": "u", "u": "u", "e": "i", "i": "i", "ö": "ü", "ü": "ü"}[v]
    ends_vowel = word[-1].lower() in TR_BACK | TR_FRONT
    return word + ("n" if ends_vowel else "") + vowel + "n"


# How an identifier's final character is pronounced: (harmony vowel, ends in vowel, hard final)
# digits by Turkish numeral (0 sıfır, 1 bir, 2 iki, 3 üç, 4 dört, 5 beş, 6 altı, 7 yedi, 8 sekiz,
# 9 dokuz); letters by letter name (b "be", k "ke", x "iks", q "kü", w "çift ve" ...).
TR_FINAL = {
    "0": ("ı", False, False),
    "1": ("i", False, False),
    "2": ("i", True, False),
    "3": ("ü", False, True),
    "4": ("ö", False, True),
    "5": ("e", False, True),
    "6": ("ı", True, False),
    "7": ("i", True, False),
    "8": ("i", False, False),
    "9": ("u", False, False),
    "a": ("a", True, False),
    "e": ("e", True, False),
    "i": ("i", True, False),
    "o": ("o", True, False),
    "u": ("u", True, False),
    "x": ("i", False, True),
    "q": ("ü", True, False),
}
TR_NARROW = {"a": "ı", "ı": "ı", "o": "u", "u": "u", "e": "i", "i": "i", "ö": "ü", "ü": "ü"}


def tr_ident(svc: str, case: str) -> str:
    """Identifier + apostrophe + case suffix (-ler/-lar, -de/-da, -nin/-in ...), vowel harmony
    and consonant hardening taken from how the final character is read aloud."""
    v, ends_vowel, hard = TR_FINAL.get(svc[-1], ("e", True, False))
    back = v in TR_BACK
    wide = "a" if back else "e"
    d = "t" if hard else "d"
    tbl = {
        "gen": ("n" if ends_vowel else "") + TR_NARROW[v] + "n",
        "loc": d + wide,
        "loc_adj": d + wide + "ki",
        "dat": ("y" if ends_vowel else "") + wide,
        "abl": d + wide + "n",
        "pl": "l" + wide + "r",
    }
    return svc + "'" + tbl[case]


def morph_slots(lang: str, svc: str) -> dict:
    if lang == "tr":
        return {
            "svc_gen": tr_ident(svc, "gen"),
            "svc_loc": tr_ident(svc, "loc"),
            "svc_loc_adj": tr_ident(svc, "loc_adj"),
            "svc_dat": tr_ident(svc, "dat"),
            "svc_abl": tr_ident(svc, "abl"),
            "svc_pl": tr_ident(svc, "pl"),
        }
    if lang == "de":
        return {
            "svc_gen": f"des {svc}-Dienstes",
            "svc_dat": f"im {svc}-Dienst",
            "svc_nom": f"der {svc}-Dienst",
            "svc_pl": f"{svc}-Instanzen",
        }
    return {"svc_pos": f"{svc}'s", "svc_the": f"the {svc} service"}


# --------------------------------------------------------------------------- helpers
SVC_RE = re.compile(r"svc-[a-z0-9]{3}")
ERR_RE = re.compile(r"E\d{4}")
ENV_RE = re.compile(r"\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b")
PATH_RE = re.compile(r"[A-Za-z0-9_./-]+/[A-Za-z0-9_.-]+\.[a-z]+")
WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÿĞğİıŞşÖöÜüÇç_-]+")


def identifiers(text: str) -> list[str]:
    out = []
    for rx in (SVC_RE, ERR_RE, ENV_RE, PATH_RE):
        out.extend(rx.findall(text))
    return out


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def env_value(rng: random.Random, env: str) -> str:
    suf = env.rsplit("_", 1)[-1]
    if env.endswith("TIMEOUT_MS"):
        return str(rng.choice([250, 500, 1500, 3000, 5000]))
    if env.endswith(("POOL_SIZE", "MAX_RETRIES")):
        return str(rng.choice([3, 5, 8, 16, 32]))
    if suf in ("PORT",):
        return str(rng.choice(PORTS))
    if suf in ("DSN", "URL"):
        return rng.choice(
            [
                "postgresql://hlm@db:5432/hlm",
                "redis://cache:6379/0",
                "http://gateway:8080",
                "kafka://broker:9092",
            ]
        )
    if suf in ("SECRET", "TOKEN"):
        return "<redacted>"
    if suf in ("POOL_SIZE", "MAX_RETRIES", "TTL"):
        return str(rng.choice([4, 8, 16, 32, 60, 300]))
    if suf == "REGION":
        return rng.choice(["eu-central-1", "eu-west-1"])
    if suf == "BUCKET":
        return rng.choice(["hlm-artifacts", "hlm-backups"])
    return rng.choice(["db", "cache", "gateway", "worker"])


def make_pools(rng: random.Random) -> dict:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    services: list[str] = []
    seen = set()
    while len(services) < N_MAIN:
        tail = "".join(rng.choice(alphabet) for _ in range(3))
        if not any(c.isalpha() for c in tail) or tail in seen:
            continue
        seen.add(tail)
        services.append("svc-" + tail)
    decoys: list[str] = []
    for i in range(N_OTHER):
        base = services[i]  # confusable: one character different from a main service
        while True:
            pos = rng.randrange(4, 7)
            ch = rng.choice(alphabet)
            cand = base[:pos] + ch + base[pos + 1 :]
            tail = cand[4:]
            if ch != base[pos] and tail not in seen and any(c.isalpha() for c in tail):
                seen.add(tail)
                decoys.append(cand)
                break
    env_all = [f"{a}_{b}_{c}" for a in ENV_PREFIX for b in ENV_MID for c in ENV_SUFFIX]
    envs = rng.sample(env_all, 150)
    path_all = [f"{d}/{f}" for d in PATH_DIRS for f in PATH_FILES]
    paths = rng.sample(path_all, 200)
    errs = [f"E{n}" for n in rng.sample(range(1000, 10000), 300)]
    return {"services": services, "decoys": decoys, "envs": envs, "paths": paths, "errs": errs}


def rand_ts(rng: random.Random) -> dt.datetime:
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC)
    span = int((dt.datetime(2026, 9, 21, tzinfo=dt.UTC) - start).total_seconds())
    return start + dt.timedelta(seconds=rng.randrange(span))


def build_item(
    rng: random.Random, idx: int, project: str, lang: str, svc: str, pools: dict, svc2_pool: list[str]
) -> dict:
    topic = rng.choice(TOPICS)
    adj_pool = {"tr": ADJ_TR, "de": ADJ_DE, "en": ADJ_EN}[lang]
    ts = rand_ts(rng)
    slots = {
        "svc": svc,
        "svc2": None,
        "env": rng.choice(pools["envs"]),
        "path": rng.choice(pools["paths"]),
        "err": rng.choice(pools["errs"]),
        "person": rng.choice(PEOPLE),
        "date": (ts - dt.timedelta(days=rng.randint(0, 40))).strftime("%Y-%m-%d"),
        "ver": f"v{rng.randint(0, 3)}.{rng.randint(0, 12)}.{rng.randint(0, 9)}",
        "port": rng.choice(PORTS),
        "comp": rng.choice(COMP[lang]),
    }
    # second service: any other service from the same world (main services also appear as
    # cross-references inside other bodies, so the retriever must rank the owner higher).
    while True:
        s2 = rng.choice(svc2_pool)
        if s2 != svc:
            slots["svc2"] = s2
            break
    syn = {f"S_{k}": v[0] for k, v in SYN[lang].items()}
    title = TITLE[lang][topic].format(**slots, **syn)

    def fill(t: str) -> str:
        per = {
            "adj": rng.choice(adj_pool),
            "num": rng.randint(2, 500),
            "num2": rng.randint(1, 60),
            "comp_alt": rng.choice(COMP[lang]),
        }
        return t.format(**slots, **per)

    target = rng.randint(*WORD_TARGET)
    sentences: list[str] = [fill(t) for t in OPEN[lang][topic]]
    pool = list(GEN[lang])
    rng.shuffle(pool)
    cursor = 0
    body_sents: list[str] = []
    while sum(len(s.split()) for s in sentences) + sum(len(s.split()) for s in body_sents) < target:
        if cursor >= len(pool):
            rng.shuffle(pool)
            cursor = 0
        body_sents.append(fill(pool[cursor]))
        cursor += 1
    closing = [fill(t) for t in CLOSE[lang]]

    heads = HEADINGS[lang]
    env_block = (
        f"## {ENV_LABEL[lang]}\n- {slots['env']}={env_value(rng, slots['env'])}\n"
        f"- path: {slots['path']}\n- error: {slots['err']}\n- owner: {slots['person']}\n"
        f"- port: {slots['port']}\n- version: {slots['ver']}"
    )
    n = len(body_sents)
    q = max(1, n // 4)
    parts = [f"## {heads[0]}", " ".join(sentences), env_block]
    for i in range(4):
        chunk = body_sents[i * q : (i + 1) * q] if i < 3 else body_sents[3 * q :]
        parts.append(f"## {heads[i + 1]}")
        parts.append(" ".join(chunk))
    parts.append(" ".join(closing))
    body = "\n\n".join(parts)

    kind = rng.choice(KINDS_BY_TOPIC[topic])
    stable_p = 0.85 if kind in STABLE_KINDS else 0.2
    stability = "stable" if rng.random() < stable_p else "volatile"
    importance = rng.choices(range(1, 11), weights=[1, 2, 4, 6, 8, 8, 6, 4, 2, 1])[0]
    tags = sorted({topic, svc, slots["err"] if topic in ("bugfix", "incident") else slots["env"], lang})
    return {
        "logical_key": f"{project}:{idx:05d}",
        "project": project,
        "kind": kind,
        "title": title,
        "body": body,
        "tags": tags,
        "stability": stability,
        "importance": importance,
        "valid_from": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lang": lang,
        "_template_id": f"{lang}.{topic}",
        "_slots": slots,
    }


def build_query(rng: random.Random, item: dict, heavy: bool, qidx: int) -> dict:
    lang = item["lang"]
    topic = item["_template_id"].split(".", 1)[1]
    slots = dict(item["_slots"])
    slots.update(morph_slots(lang, slots["svc"]))
    syn = {f"S_{k}": rng.choice(v[1:]) for k, v in SYN[lang].items()}
    if heavy:
        k = rng.randrange(len(QHEAVY[lang]))
        tmpl_id = f"{lang}.q.heavy.{k}"
        text = QHEAVY[lang][k].format(**slots, **syn)
    else:
        k = rng.randrange(len(QLIGHT[lang][topic]))
        tmpl_id = f"{lang}.q.{topic}.{k}"
        text = QLIGHT[lang][topic][k].format(**slots, **syn)
    return {
        "qid": f"q{qidx:03d}",
        "lang": lang,
        "query": text,
        "gold_logical_key": item["logical_key"],
        "identifier_heavy": heavy,
        "template_id": tmpl_id,
        "gold_template_id": item["_template_id"],
    }


def shares_title_wording(query: str, title: str) -> bool:
    """True when >= 50% of the title's content words (len>=4, non-identifier) appear verbatim."""
    ids = set(identifiers(title))
    words = [
        w.lower()
        for w in WORD_RE.findall(title)
        if len(w) >= 4 and w not in ids and not re.fullmatch(r"[\d.-]+|v\d.*", w)
    ]
    if not words:
        return False
    qwords = {w.lower() for w in WORD_RE.findall(query)}
    hit = sum(1 for w in words if w in qwords)
    return hit / len(words) >= 0.5


# --------------------------------------------------------------------------- generation
def generate(out_dir: Path) -> dict:
    rng = random.Random(SEED)
    pools = make_pools(rng)
    items: list[dict] = []
    langs_main = [LANGS[i % 3] for i in range(N_MAIN)]
    rng.shuffle(langs_main)
    for i in range(N_MAIN):
        items.append(
            build_item(
                rng, i + 1, MAIN_PROJECT, langs_main[i], pools["services"][i], pools, pools["services"]
            )
        )
    langs_other = [LANGS[i % 3] for i in range(N_OTHER)]
    rng.shuffle(langs_other)
    for i in range(N_OTHER):
        items.append(
            build_item(
                rng,
                i + 1,
                OTHER_PROJECT,
                langs_other[i],
                pools["decoys"][i],
                pools,
                pools["services"] + pools["decoys"],
            )
        )

    # identifier triples must single out the gold item for heavy queries
    triple_count: dict[tuple, int] = {}
    for it in items:
        s = it["_slots"]
        t = (s["err"], s["env"], s["path"])
        triple_count[t] = triple_count.get(t, 0) + 1
    main = [it for it in items if it["project"] == MAIN_PROJECT]
    queries: list[dict] = []
    picked: list[tuple[dict, bool]] = []
    for lang in LANGS:
        cands = [it for it in main if it["lang"] == lang]
        uniq = [
            it
            for it in cands
            if triple_count[(it["_slots"]["err"], it["_slots"]["env"], it["_slots"]["path"])] == 1
        ]
        heavy_items = rng.sample(uniq, N_HEAVY[lang])
        rest = [it for it in cands if it["logical_key"] not in {h["logical_key"] for h in heavy_items}]
        light_items = rng.sample(rest, N_QUERIES[lang] - N_HEAVY[lang])
        picked.extend((it, True) for it in heavy_items)
        picked.extend((it, False) for it in light_items)
    rng.shuffle(picked)
    for n, (it, heavy) in enumerate(picked, start=1):
        queries.append(build_query(rng, it, heavy, n))

    stats = validate(items, queries)

    out_dir.mkdir(parents=True, exist_ok=True)
    public = [
        "logical_key",
        "project",
        "kind",
        "title",
        "body",
        "tags",
        "stability",
        "importance",
        "valid_from",
        "lang",
    ]
    with (out_dir / "items.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for it in items:
            f.write(json.dumps({k: it[k] for k in public}, ensure_ascii=False) + "\n")
    with (out_dir / "queries.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    with (out_dir / "SHA256SUMS").open("w", encoding="utf-8", newline="\n") as f:
        for name in ("items.jsonl", "queries.jsonl"):
            f.write(f"{sha256_file(out_dir / name)}  {name}\n")
    return stats


def validate(items: list[dict], queries: list[dict]) -> dict:
    by_key = {it["logical_key"]: it for it in items}
    assert len(by_key) == len(items) == N_MAIN + N_OTHER
    n_main = sum(1 for it in items if it["project"] == MAIN_PROJECT)
    assert n_main == N_MAIN
    words = [len(it["body"].split()) for it in items]
    assert all(MIN_WORDS <= w <= MAX_WORDS for w in words), (min(words), max(words))
    for it in items:
        assert it["kind"] in ("fact", "episode", "lesson", "experience", "session_note", "doc_chunk")
        assert it["stability"] in ("stable", "volatile") and 1 <= it["importance"] <= 10
        assert 1 <= len(it["title"]) <= 200 and len(it["body"]) <= 64000
        s = it["_slots"]
        for tok in (s["svc"], s["env"], s["path"], s["err"]):
            assert tok in it["body"], (it["logical_key"], tok)

    assert len(queries) == sum(N_QUERIES.values()) == 100
    per_lang = {lang: 0 for lang in LANGS}
    heavy = 0
    golds = set()
    not_shared = 0
    for q in queries:
        gold = by_key[q["gold_logical_key"]]
        assert gold["project"] == MAIN_PROJECT and gold["lang"] == q["lang"]
        assert q["template_id"] != q["gold_template_id"] == gold["_template_id"]
        assert q["gold_logical_key"] not in golds
        golds.add(q["gold_logical_key"])
        per_lang[q["lang"]] += 1
        ids = identifiers(q["query"])
        hay = gold["title"] + "\n" + gold["body"]
        for tok in ids:
            assert tok in hay, (q["qid"], tok)
        if q["identifier_heavy"]:
            heavy += 1
            assert len(ids) >= 3, q
        else:
            assert len(ids) <= 2, q
        if not shares_title_wording(q["query"], gold["title"]):
            not_shared += 1
    assert per_lang == N_QUERIES, per_lang
    assert heavy == sum(N_HEAVY.values()) == 25
    ratio = not_shared / len(queries)
    assert ratio >= MIN_PARAPHRASE_RATIO, ratio
    total_words = sum(words)
    return {
        "items_total": len(items),
        "items_main": n_main,
        "items_other": len(items) - n_main,
        "items_by_lang": {lang: sum(1 for it in items if it["lang"] == lang) for lang in LANGS},
        "words_min": min(words),
        "words_max": max(words),
        "words_mean": round(total_words / len(words), 1),
        "est_chunks_words_div_300_main": round(
            sum(w for it, w in zip(items, words, strict=False) if it["project"] == MAIN_PROJECT) / 300
        ),
        "est_chunks_words_div_300_other": round(
            sum(w for it, w in zip(items, words, strict=False) if it["project"] == OTHER_PROJECT) / 300
        ),
        "queries": len(queries),
        "queries_by_lang": per_lang,
        "identifier_heavy": heavy,
        "paraphrase_ratio": round(ratio, 2),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "g3")
    args = ap.parse_args(argv)
    stats = generate(args.out)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    for name in ("items.jsonl", "queries.jsonl"):
        print(f"{sha256_file(args.out / name)}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
