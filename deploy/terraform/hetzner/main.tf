resource "hcloud_ssh_key" "admin" {
  name       = "${var.server_name}-admin"
  public_key = trimspace(var.ssh_public_key)
}

resource "hcloud_firewall" "hlmemo" {
  name = "${var.server_name}-ingress"

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_cidrs
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  # No other ingress; no egress rules means outbound is allowed (apt, ACME,
  # model downloads, configured model providers and optional object storage).
}

resource "hcloud_server" "hlmemo" {
  name         = var.server_name
  server_type  = var.server_type
  location     = var.location
  image        = var.image
  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.hlmemo.id]
  backups      = var.backups

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    deploy_user    = var.deploy_user
    ssh_public_key = trimspace(var.ssh_public_key)
  })

  labels = { app = "hlmemo" }
}
