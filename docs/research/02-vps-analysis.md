# VPS provider analysis (research agent, 2026-09-22)
Prices as of 2026-09-22. VAT basis stated per row (DE VAT 19%).

## Provider table
| Provider | ~4vCPU/8GB (EUR/mo) | ~16GB (EUR/mo) | Storage | Traffic | Backups/Snapshots | Vertical resize | GPU (cheapest) | Dedicated path | Object storage | Terraform | EU/CH DCs | Source |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Hetzner Cloud | CX33 €8.49 ex VAT (+€0.50 IPv4) | CX43 8vCPU/16GB €15.99 ex VAT; CAX31 ARM 8/16 €20.99 | 80 / 160 GB NVMe, volumes extra | 20 TB incl, €1/TB over | Backups 20% of server; snapshots ~€0.0143/GB | Yes, in-place rescale | Dedicated GEX44-1 RTX 4000 SFF Ada 20GB €232.30 + €114 setup; GEX131 RTX PRO 6000 96GB | Yes (AX/EX from ~€57) | €6.49 base incl 1 TB + 1 TB egress | Official hcloud | FSN/NBG (DE), HEL (FI) | https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment |
| netcup | VPS 1000 G12 4c/8GB €10.37 incl VAT (12-mo) | VPS 2000 G12 8c/16GB €19.25; RS 2000 G12 8 ded. cores/16GB €21.43 incl VAT | 256 / 512 GB NVMe | Included (fair use) | CoW snapshots incl; no managed backup | Only within product line; cross-line = reorder + migrate | vGPU tariffs (unverified) | Root servers; no bare metal | None | Community only | NBG, VIE, AMS | https://www.netcup.com/en/server/vps |
| Contabo | Cloud VPS 4: 4c/8GB €5.50 incl VAT (24-mo) | Cloud VPS 8: 8c/24GB €14.00 | 100 / 300 GB SSD | Fair-use | Auto-backup €6.70/mo | Ticket-driven migration w/ downtime | None | Yes from ~€50 | €2.49/250 GB | Official | Nuremberg/Munich | https://contabo.com/en/vps/ |
| OVHcloud | VPS-2 4c/8GB from €7.21 ex VAT (12-mo upfront) | VPS-4 8c/24GB €19.96 ex VAT; Public Cloud b3-16 ~€56 | 75 / 200 GB NVMe | Unlimited | Daily backup incl | 1-click upgrade (no downgrade); VPS→Public Cloud = migration | l4-1-gpu (L4 24GB) €0.9877/h incl VAT; L40S, A100, H100 | Large bare-metal catalogue | S3 ~€0.008/GB ex VAT, free egress | Official | Frankfurt, Limburg (DE), FR, Warsaw | https://www.ovhcloud.com/de/vps/ |
| Scaleway | PLAY2-MICRO 4c/8GB €40.20 ex VAT | PRO2-XS 4c/16GB €81.90 | Block €0.095/GB/mo | Egress incl | Snapshot €0.0358/GB/mo | Yes | L4-1-24G €0.79/h; L40S/H100 | Elastic Metal | €0.008/GB | Official | PAR, AMS, WAW (no DE/CH) | https://www.scaleway.com/en/pricing/virtual-instances/ |
| Exoscale (CH) | Large 4c/8GB €68.13 ex VAT | Extra-Large 4c/16GB €136.27 | per GiB-hour | 1 TiB free | per GiB | Yes | A30 ~$1.23/h (screening) | Partner-only | ~€0.02/GB | Official | Geneva, Zurich, Frankfurt, Munich, Vienna | https://www.exoscale.com/pricing/ |
| Infomaniak (CH) | 4c/8GB CHF 16.10 (~€10.59) | a8-ram32 ~€42 | Ceph block per GB | Free | per GB | Yes (OpenStack) | A100/H100/L40S/L4/T4 (unverified) | No | S3/Swift (unverified) | OpenStack only | Geneva, Zurich (2026) | https://www.infomaniak.com/en/hosting/public-cloud |
| DigitalOcean | Basic 4c/8GB $48 | Basic 8c/16GB $96 | 160 / 320 GB SSD | 5-6 TB | Backups 20%; snapshots $0.06/GB | Yes | RTX 4000 Ada $0.76/h; L40S $1.57/h | No | $5/250 GiB | Official | Frankfurt, Amsterdam, London | https://www.digitalocean.com/pricing/droplets |
| Vultr | HP AMD 4c/8GB $48 | HP 8c/16GB $96 | 180 / 350 GB NVMe | 6-8 TB | Backups +20% | Yes | A16 $0.47/GPU-h; L40S $1.56/h | Bare metal yes | $18/TB | Official | Frankfurt, AMS, Paris, Warsaw | https://www.vultr.com/pricing/ |
| Hostinger (baseline) | KVM 2: 2c/8GB €7.99 intro / €14.99 renewal | KVM 4: 4c/16GB €10.99 intro / €27.99 renewal | 100 / 200 GB NVMe | 8 / 16 TB | Weekly backups free | Upgrade only | None | None | None | Official | Frankfurt, NL, LT, FR, UK | https://www.hostinger.de/vps-hosting |
| Google Cloud (baseline) | e2-standard-2 ~$63 | e2-standard-4 ~$126 europe-west3 (est.) | PD ~$0.10/GB | ~$0.12/GB egress | ~$0.026/GB | Yes | g2-standard-4 L4 ~$0.71/h | No | $0.02/GB | Official | Frankfurt, Berlin, Zurich | (aggregator) |
| IONOS | VPS L+ 6c/8GB €18 regular | VPS XL+ 8c/16GB €38 | 240 / 480 GB NVMe | Unlimited | Cloud Backup ~$0.065/GB | Upgrade only | IONOS Cloud (separate) | Yes | Via IONOS Cloud | ionos-cloud (not VPS) | Germany, Spain, UK | https://www.ionos.de/server/vps |
| UpCloud (FI) | 4c/8GB/160GB $52 (Frankfurt) | 6c/16GB $121 | MaxIOPS incl | Zero egress | extra | Yes | L4 from $0.68/h | No | Yes | Official | Helsinki, Frankfurt, AMS, Warsaw | https://upcloud.com/pricing/ |

## Red flags / recent changes
- Hetzner raised prices twice in 2026 (1 Apr up to +37%; 15 Jun new orders/rescales: CX/CAX +33-38%, CPX +144-176%, CCX +113-169%). Dedicated-vCPU lines no longer good value; a rescale re-prices your server. CX43/CX53 showed "currently unavailable" in NBG1/HEL1 during check.
- netcup raised prices May 2026 (+18.5% existing, +24.3% new); 12-month term default; no in-place upgrade across lines/generations; no Terraform/object storage.
- Contabo: heavy overselling (Geekbench single-core 482 vs Hetzner 1,442); ticket-driven upgrades with downtime.
- OVHcloud raised VPS prices 1 Apr 2026; up to +87% on Gen-2026 dedicated from Sept 2026. VPS and Public Cloud are separate silos.
- Exoscale repriced compute ~+74% May 2026; GPU requires manual screening.
- Scaleway has no DE/CH zone. Hostinger dual pricing (intro vs ~2.5× renewal); no GPU/object storage/dedicated.

## Recommendation
Primary: **Hetzner Cloud CX43** (8 vCPU shared / 16 GB / 160 GB NVMe, Falkenstein or Nuremberg). Fallback: **OVHcloud** (VPS-2/VPS-4 Frankfurt/Limburg; Public Cloud for GPU).
- Hetzner still lowest EUR/GB-RAM in Germany on CX/CAX. In-place rescale, Volumes, Firewalls/private networks/snapshots/backups all API + Terraform (official hcloud), 20 TB egress incl.
- GPU path without lock-in: MCP server only needs an OpenAI-compatible vLLM endpoint over HTTPS → run memory backend on Hetzner, rent GPU hourly elsewhere (Scaleway L4 €0.79/h, OVH L4, DO L40S) or Hetzner GEX44-1 (RTX 4000 SFF Ada 20 GB, 7B-14B in 4-8 bit) €232.30 + €114 setup, GEX131 (96 GB) for 30B-class. Keep model weights in Hetzner Object Storage (S3) so swapping providers is a bucket sync.
- Dedicated path native: Hetzner AX/EX root servers with vSwitch to cloud network.
- Avoid CPX/CCX at Hetzner now. CAX31 (ARM) only if CX43 out of stock; check ONNX/pgvector ARM builds first.
- OVH fallback: cheapest 4c/8GB with DE DC, daily backup incl, free egress, Terraform, GPU/object-storage/bare-metal in same account. Downsides: two product silos, 2026 hikes.
- netcup RS 2000 G12 better raw value but 12-month term, no Terraform, no upgrade path beyond product line.
- **Starting-config estimate on Hetzner (incl. 19% VAT): CX43 €15.99 + IPv4 €0.50 + Backups €3.20 + snapshot ~€0.86 = €20.55 ex VAT ≈ €24.45/mo; + Object Storage €6.49 → ≈ €32.20/mo.** CX33 variant ≈ €12.70/mo (≈ €20.45 with object storage).

## Caveats
- Hetzner IPv4/snapshot/volume prices from third-party trackers (plan pages render client-side). Confirm in Cloud Console before ordering.
- OVH "from" prices are 12-month-upfront; monthly no-commitment (~€8.48 ex VAT VPS-2) unverified.
- Exoscale/Infomaniak from aggregator snapshots. GCP europe-west3 estimated. Contabo/netcup/IONOS several items unverified.
