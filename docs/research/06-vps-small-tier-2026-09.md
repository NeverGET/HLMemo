# Small-tier VPS options once embeddings move to a cloud API (2026-09-22)

## Hostinger tiers
FX: ECB reference rate on 2026-09-22, 1 EUR = 55.9492 TRY (https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml).
Specs, 24-month prices and renewal prices are from https://www.hostinger.com/tr/vps-hosting, read today. The page says "Fiyatlara KDV dahil değildir", so **prices exclude the 20% KDV**. Every plan is paid up front.

| Tier | Specs | 24-mo monthly TRY → EUR (with 20% KDV) | 24-mo total incl. KDV | Renewal, 2-yr term (with KDV) |
|---|---|---|---|---|
| KVM 1 | 1 vCPU / 4 GB / 50 GB NVMe / 4 TB | 276.99 → €4.95 (€5.94) | 7,977 TRY ≈ **€143** | 552.99 TRY → €11.86 |
| KVM 2 | 2 vCPU / 8 GB / 100 GB / 8 TB | 405.99 → €7.26 (€8.71) | 11,693 TRY ≈ **€209** | 718.99 → €15.42 |
| KVM 4 | 4 vCPU / 16 GB / **200 GB** (not 299) / 16 TB | 552.99 → €9.88 (€11.86) | 15,926 TRY ≈ €285 | 1,381.99 → €29.64 |
| KVM 8 | 8 vCPU / 32 GB / 400 GB / 32 TB | 1,105.99 → €19.77 (€23.72) | ≈ €569 | 2,394.99 → €51.37 |

- **Backups:** a weekly backup is free. Daily backups are an add-on at US$6/mo and keep only 2 daily plus 2 weekly copies (https://www.hostinger.com/support/1665153-how-to-activate-daily-backups-in-hostinger).
- **Snapshots:** only 1 manual snapshot is kept, and it expires after 1 day (https://support.hostinger.com/en/articles/1583232-how-to-back-up-or-restore-a-vps-server).
- **Locations:** US, Brazil, France, Germany, Lithuania and India. There is no Turkey location.
- **Upgrades are in place.** They take up to 10 minutes, and "all your files and configurations will remain unchanged" (https://www.hostinger.com/support/1583229-how-to-upgrade-a-vps-server-at-hostinger/).
- I did not get the 1-month and 12-month prices. The checkout click was blocked as a transaction.

## Sizing (no local model)
- **Steady RAM is about 1.9–2.7 GB.** Postgres with shared_buffers at 512 MB–1 GB is about 0.8–1.3 GB with backends. Add API 0.3–0.5, worker 0.2–0.3, Caddy 0.05, and OS plus Docker about 0.5.
- **Vector data is small.** At 50k rows × 1536 dims × 4 B you get about 300 MB of vectors and an HNSW index of about 0.4–0.6 GB, so the working set fits in RAM on either size.
- **1 vCPU / 4 GB runs Phase 1, with little margin.** About 1.3 GB is left for page cache. An HNSW rebuild (maintenance_work_mem ≥ 512 MB), a migration or a restore test running at the same time will squeeze or swap it. With 1 vCPU, the import, index build and API requests all queue behind each other.
- **2 vCPU / 8 GB leaves about 5 GB free.** That is room for shared_buffers of 1.5–2 GB, a parallel Phase 1.5 import job, and the Phase 2 librarian. The librarian waits on cloud LLM calls, so it needs about 200–300 MB and very little CPU. It would also fit a second compose stack for restore drills.
- **Disk:** the DB, local backups and images come to about 5–10 GB, so 40 GB is enough. The 100 GB on KVM 2 is comfortable.

## Alternatives
Totals are monthly and 24-month, including 20% VAT.

| Option | Specs | €/mo incl. VAT | 24-mo | Backups | Notes |
|---|---|---|---|---|---|
| OVH VPS-1 | 2 vCore / 4 GB / 40 GB | 4.57 | ~€110 | **Daily, included** | In-place resize "without interruption". Listed "ab €4.53 incl. 19% MwSt" (https://www.ovhcloud.com/de/vps/). The commitment term is unverified. |
| OVH VPS-2 | 4 vCore / 8 GB / 75 GB | 8.65 | **~€208** | Daily, included | Same terms as VPS-1. Measured latency to OVH FR was 73 ms. |
| Hetzner CX23 / CAX11 | 2 / 4 GB / 40 GB | 8.51 / 9.23 with backups | €204 / €221 | +20%, 7 slots | Prices rose on 15 June 2026 (https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment). IPv4 costs €0.50 (https://docs.hetzner.com/cloud/servers/primary-ips/overview). **Shown "not available" today**, and the status page has had "Limited availability of cloud instances" open since 26 June. |
| Hetzner CX33 / CAX21 | 4 / 8 GB / 80 GB | 12.83 / 15.71 with backups | €308 / €377 | +20% | Your repo's Terraform already targets Hetzner. Currently out of stock. |
| netcup VPS 500 / 1000 G12 | 2/4 GB/128 GB; 4/8 GB/256 GB | 5.96 / 10.46 | €143 / €251 | CoW snapshots only | 12-month minimum term (https://www.netcup.com/en/server/vps/vps-500-g12-iv-12m). Some locations are currently unavailable. |
| Contabo Cloud VPS 4 | 4 / 8 GB / 100 GB SSD | 5.28 | €127 | Auto Backup is a paid add-on | The price holds "for the first 24 months", with list price €6.60 (https://contabo.com/en/vps/). 200 Mbit/s port, 1 snapshot. Known for overselling and noisy neighbours. |
| IONOS VPS M+ | 4 vCore / 4 GB | €3 for 3 months, then €9 | ~€200 + €10 setup | From €0.06/GB | Promotional pricing with a minimum term (https://www.ionos.de/server/vps). |
| DigitalOcean Basic | 2 / 4 GB | ~$24 + VAT | ~€590 | +20% | https://www.digitalocean.com/pricing/droplets. Not competitive. |

I did not verify Vultr or Scaleway. The Vultr page only showed managed-database prices.

## Recommendation
- **Primary: OVH VPS-2, about €208 over 24 months incl. VAT and daily backups.** It gives 4 vCore / 8 GB and includes daily backups. It resizes in place, has no Hostinger-style promotional price cliff, and runs in EU data centers with latency similar to what we measured.
- **Fallback: Hostinger KVM 2, about €209 over 24 months with weekly backups.** Adding daily backups brings it to about €332.
- **Is KVM 2 a good deal? Yes, for 24 months it is fair.** It costs about the same as OVH VPS-2 and less than Hetzner now charges. Its weak points are fewer cores (2 vs 4), weekly-only free backups, a 1-day snapshot, and renewal jumping 77% to about €15.42/mo.
- **Don't buy KVM 4.** It is €285 plus a €29.64/mo renewal, which is oversized now that the local model is gone.
- **KVM 1, netcup VPS 500 or OVH VPS-1 are enough for Phase 1 only.** At about €110–143 they will get tight during the legacy import.
- **Hetzner CX33** remains the best fit for the existing Terraform if stock comes back, at about €100 more over 24 months.

## Caveats
- **VAT basis:** all totals assume 20% VAT (Turkish KDV). If you are billed as a German customer, EU providers charge 19% instead. If you have a Turkish business VAT ID, reverse charge may apply.
- **Currency:** TRY is volatile, but Hostinger's up-front payment fixes the TRY amount. Renewal will be re-priced in TRY.
- **OVH:** check in the configurator whether the "ab" price needs a 12- or 24-month commitment and what the no-commitment price is.
- **Backups:** HLMemo's own `deploy/backup` offsite backups make provider backup frequency less critical. Keep them either way.
- **Hetzner:** stock and the post-June prices can change again.