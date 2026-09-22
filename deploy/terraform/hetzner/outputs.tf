output "ipv4_address" {
  description = "Server public IPv4; set your domain A record to this address."
  value       = hcloud_server.hlmemo.ipv4_address
}

output "ipv6_address" {
  description = "Server public IPv6; set AAAA only after confirming connectivity."
  value       = hcloud_server.hlmemo.ipv6_address
}

output "ssh_command" {
  description = "Wait for cloud-init to finish before the first deploy."
  value       = "ssh ${var.deploy_user}@${hcloud_server.hlmemo.ipv4_address}"
}
