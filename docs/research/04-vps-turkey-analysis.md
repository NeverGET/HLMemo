# VPS options located in Turkey + measured latency (2026-09-22)
Complements 02-vps-analysis.md (EU-only). Client location details are private.

## Turkey-located options table
Rates used: 1 EUR = 55.97 TRY and 1 EUR = 1.1468 USD (XE, 21 Sep 2026 16:00 UTC, https://www.xe.com/en-us/currencytables?from=EUR). KDV is 20%. "+K" means the price excludes KDV and I added it. "K?" means the page does not say whether KDV is included, so I show the listed price and the price with 20% added.

| Provider | ~4c/8GB (as billed → EUR/mo) | ~16GB plan | NVMe | Traffic | Backups | Resize | GPU | Dedicated | Object storage | API/Terraform | DC | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Radore Cloud | Disk-Elite 4c/8GB/300GB, $29.5 +K → **€30.9** | Disk-Infinity 6c/16GB, $49.5 → €51.8 | Yes | ? | Paid add-on | "Can increase/decrease" (panel or ticket?) | Not found | Yes (own DC) | No | None found | Istanbul | https://radore.com/tr/hizmetler/bulut-sunucu |
| Turhost | VDS TR 4, 4c/8GB/200GB, $72.79 +K → €76.2 (intro $24.99 for 3 months) | VDS TR 6 6c/16GB, $110.42 → €115.6 | "NVMe SAN" | 3 TB | Daily, 30 days kept | Order-based | No | Yes | No | No | TT Gayrettepe | https://www.turhost.com/sunucu/vps-tr-sunucu |
| Natro | XCloud Pro 4c/8GB/200GB SSD, 1,453.85 TL ($29.99 promo; list $71.99) K? → €26.0–31.2 | None at 16GB (Pro+ is 4c/12GB, $34.99) | SSD | Unmetered at 100 Mbit | ? | Configurator | No | Yes | No | No | Istanbul | https://www.natro.com/sunucu-kiralama/vds-sunucu |
| Veridyen | gnCloud S-5 4c/8GB/160GB, 1,680.95 TL K? → €30.0–36.0 | SR-7 4c/16GB, 2,374.56 TL → €42.4–50.9 | Yes (VMware) | 1 Gbit shared | Asked about in FAQ | "We upgrade it for you" | No | Yes | No | No | Istanbul | https://www.veridyen.com/sunucu/bulut-sunucu |
| Sunucun | CLD-7 4c/12GB/150GB, 1,659 TL (15% off) K? → €29.6–35.6 | CLD-10 6c/24GB, 3,167 TL → €56.6 | All-flash | Fair-use | ? | ? | GPU dedicated servers | Yes | No | No | Istanbul, Ankara | https://sunucun.com.tr/bulut-sunucu |
| hosting.com.tr | VDS Ultra 150, 12c E5/12GB, $49.99 K? → €43.6–52.3 | Ultra 300 24GB, $99.99 | SSD or NVMe | 100 Mbit | ? | Via sales chat | No | Yes | No | No | Istanbul | https://www.hosting.com.tr/bulut-sunucu |
| İHS Telekom | Build your own: 4c+8GB+100GB = $42 K? → €36.6–44.0; backup +$7.20 | 8c/16GB ≈ $80 → €69.8 | SSD | 10 TB | Paid | Configurator | No | Yes | No | No | Istanbul | https://www.ihs.com.tr/sunucu-kiralama/vds-sunucu.html |
| Vargonen (now EclitGO) | VPS Pro 4c/8GB/200GB SSD, $11.49 on a 1-year term (list $22.98) → €10.0–20.0 | None | SSD | Unmetered | 2 snapshots + 7-day backup, firewall | ? | No | ? | No | No | Istanbul (Ataşehir) | https://www.eclitgo.com/vps |
| Türk Telekom Bulut | Virtual data centre (SVM) from 400 TL/mo with a 12-month commitment, shared-CPU pool, 4c/8GB price not public | Configurable | ? | ? | From 340 TL/mo | Self-service | GPU servers (enterprise) | Colocation | Yes | Not verified | TT DCs | https://turktelekombulut.com/urunler/sanal-veri-merkezi |
| Turkcell Bulut | Package 4 vCPU/8GB/200GB **SAS**, no public price, apply through a corporate form | ? | No (SAS) | ? | Extra cost | Add-ons | ? | Yes | ? | Not verified | Turkcell DCs | https://turkcellbulut.com/product-detail/Yeni-Nesil-Sanal-Sunucu |
| Güzel Hosting, DGN | Pages 404 or no public cloud price list (DGN is in Bursa) | – | – | – | – | – | – | Yes | – | – | – | dgn.net.tr |
| **Huawei Cloud TR-Istanbul** | Full hyperscaler (ECS, OBS, backups, official Terraform, GPU flavours); I could not get a TR price from the calculator | – | EVS SSD | Paid EIP | Yes | Yes | Yes | BMS | OBS | Yes | Istanbul | https://www.huaweicloud.com/intl/en-us/pricing/calculator.html |
| **Gcore Istanbul** | g1-standard-4-8 €33.91 ex VAT (dedicated vCPU, free egress) | g1-standard-8-16 €67.82 | Volumes | Free egress | Snapshots | Yes | Other regions | Yes | Yes (Istanbul not verified) | Official | Istanbul | https://gcore.com/pricing/cloud |

No Turkey location (checked): Google has only an announced Ankara region with Turkcell, expected 2028–29 (https://www.invest.gov.tr/...turkcell-and-google-cloud...). Oracle has no Turkey region on its list. Vultr has no Istanbul. For Azure, AWS, DigitalOcean, OVH, Contabo, Linode and Alibaba I found nothing. Zenlayer has Istanbul sites IST1/IST2 (IST2 is inside the Radore DC), but its pricing is not public.

## Latency from this Mac (measured)
Measured from the owner's client machine in Turkey (location/ISP kept private).

| Target | DC | Method | median ms | p90 ms |
|---|---|---|---|---|
| Hetzner nbg1-speed | Nuremberg | TCP/443, icmp 76 | 64.3 | 66.1 |
| Hetzner fsn1-speed | Falkenstein | TCP/443, icmp 75 | 72.5 | 75.6 |
| Hetzner hel1-speed | Helsinki | TCP/443, icmp 103 | 86.9 | 113.7 |
| OVH proof.ovh.net | FR | TCP/80, icmp 78 | 73.5 | 144.2 |
| turktelekombulut.com | TT Istanbul (on-net for the measuring client) | TCP/443, provider's own site | 12.5 | 24.1 |
| turkcellbulut.com | Superonline Istanbul | TCP/443, own site | 21.5 | 72.4 |
| cloud.radore.com | Radore AS42926 | TCP/443, icmp 27 | 20.2 | 20.9 |
| ecs / obs.tr-west-1.myhuaweicloud.com | Huawei Istanbul | TCP/443 on API endpoints | 22.0 / 23.7 | 119.9 / 106.0 |
| mail.turhost.com | Çizgi Şişli | TCP/443 | 18.8 | 19.5 |
| mail.natro.com | Çizgi Şişli | TCP/80 | 19.7 | 23.4 |
| veridyen.net | AS209853 | TCP/443 | 19.2 | 42.3 |
| uranus.hosttrik.com | Sunucun AS213652 | TCP/443 | 20.2 | 21.2 |
| spamgw1.hosting.com.tr | Niobe Istanbul | TCP/443 | 21.2 | 21.9 |
| www.ihs.com.tr | IHS AS49126 | TCP/443 | 20.2 | 21.1 |
| mt-spamexperts.guzel.net.tr | GNET | TCP/443 | 19.2 | 20.5 |
| www.dgn.net.tr | Bursa | TCP/443 | 19.8 | 102.3 |
| mx01.vargonen.net | Eclit Ataşehir | TCP/443 | 18.4 | 41.3 |

## Does latency matter here
- The gap is steady: about 20 ms to Turkey versus 64–73 ms to Hetzner NBG/FSN, so roughly 45–55 ms more per round trip to Germany.
- If the MCP client keeps its connection open, each call pays one extra round trip: +~50 ms on a 250 ms server time, about 20% slower per call.
- If every call opens a new connection (TCP + TLS 1.3 + request = 3 round trips), each call pays about +150 ms.
- Over a 50-call session that adds 2.5 s (connection kept open) to 7.5 s (new connections). A session lasts minutes, mostly waiting on the LLM, so this is under about 2% of wall-clock time.
- The embedding worker runs asynchronously and is not on this path. Conclusion: latency is not a deciding factor for this workload. Choose nbg1 over fsn1 (about 8 ms better).

## Red flags
- None of the Turkish hosts I checked has a public API or Terraform. Upgrades go through a ticket or new order. None offers object storage or a GPU cloud. Several run older Xeon E5 CPUs.
- Headline prices are often introductory: Turhost is 66% off for 3 months then $72.79, Natro shows 58% off, hosting.com.tr 50% off for 3 months, EclitGO needs a 1-year term.
- Most prices are set in USD and charged in TRY, so FX risk sits with the buyer. Plans priced in TRY (Veridyen, Sunucun) are likely to be re-priced with inflation.
- Türk Telekom and Turkcell sell to businesses: prices are not public, there is a 12-month commitment or an application form, and a Turkish tax ID is probably needed.
- Huawei showed p90 spikes above 100 ms, and US sanctions exposure is a governance question.
- KVKK: moving personal data abroad (for example to Hetzner) needs standard contractual clauses notified to the KVKK within 5 business days (https://www.dataprotectionturkey.com). This matters if memories from the **work computer** hold employer or third-party personal data. Check the employer's policy.

## Recommendation
- **Primary: Hetzner CX43 in nbg1, about €24.45/mo incl. VAT (about €32.20 with object storage).** It loses about 50 ms of latency, which barely registers in a session. It wins on in-place resize, official Terraform, S3 storage, and a path to dedicated and GPU servers.
- **Best Turkish option: Radore Cloud Disk-Elite, $29.5 + KDV ≈ €30.9/mo.** It is about 3.5× closer (20 ms), a proper Istanbul DC, NVMe, with dedicated servers and colocation later. It has no Terraform, no object storage and no GPU path. **Turkish providers win on latency but lose clearly on the upgrade path.**
- **Fallback if the data must stay in Turkey (KVKK or employer policy):** Gcore Istanbul g1-standard-4-8 at €33.91 ex VAT (Terraform, free egress; check Istanbul availability in the console), or Huawei TR-Istanbul for a full hyperscaler.
- Lock-in: Hetzner and Gcore are Terraform plus plain Docker, so easy to leave. The Turkish VPS hosts are low lock-in but fully manual to operate.
- For the GPU path, keep the D-017 design: an OpenAI-compatible endpoint hosted anywhere. None of the Turkish hosts adds anything here.
- Avoid promo-priced plans (Turhost, Natro, hosting.com.tr) and the SAS-disk Turkcell package.

## Caveats
- The Türk Telekom Bulut and Turkcell sites measured may be marketing front-ends, not the cloud DC. Measured from the owner's client machine in Turkey (location/ISP kept private).
- I measured nothing for Gcore Istanbul (no endpoint found). The Gcore price is from the list page and I could not confirm it applies to Istanbul.
- I could not get a Huawei TR price from the calculator.
- KDV inclusion is unclear for Natro, Veridyen, Sunucun, hosting.com.tr and İHS, hence the price ranges.
- Not checked: resize without rebuild, snapshot pricing, and reputation or uptime history for most Turkish hosts. Law 5651 obligations for Turkish hosting providers were not researched.
