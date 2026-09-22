variable "server_name" {
  description = "Name used for the server, firewall and SSH key."
  type        = string
  default     = "hlmemo"
}

variable "server_type" {
  description = "Hetzner server type; confirm availability and price before provisioning."
  type        = string
  default     = "cx43"
}

variable "location" {
  description = "Hetzner location (fsn1 or nbg1 recommended)."
  type        = string
  default     = "fsn1"
}

variable "image" {
  description = "OS image; cloud-init is written for Ubuntu with apt."
  type        = string
  default     = "ubuntu-24.04"
}

variable "ssh_public_key" {
  description = "An administrator's OpenSSH PUBLIC key; never pass the private key."
  type        = string
  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) ", trimspace(var.ssh_public_key))) || can(regex("^ecdsa-sha2-", trimspace(var.ssh_public_key)))
    error_message = "Supply an OpenSSH public key."
  }
}

variable "admin_cidrs" {
  description = "Nonempty trusted IPv4/IPv6 CIDRs allowed to reach SSH; use /32 or /128 where possible."
  type        = list(string)
  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for cidr in var.admin_cidrs : can(cidrhost(cidr, 0)) && !endswith(cidr, "/0")])
    error_message = "Supply valid restricted admin CIDRs; /0 SSH access is forbidden."
  }
}

variable "deploy_user" {
  description = "Non-root deployment user (docker membership grants root-equivalent access)."
  type        = string
  default     = "hlmdeploy"
  validation {
    condition     = can(regex("^[a-z][a-z0-9_-]{0,30}$", var.deploy_user)) && var.deploy_user != "root"
    error_message = "Use a valid non-root Linux username."
  }
}

variable "backups" {
  description = "Enable Hetzner server snapshots (additional cost); pg_dump backups remain required."
  type        = bool
  default     = false
}
