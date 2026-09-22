terraform {
  required_version = ">= 1.6, < 2.0"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = ">= 1.50, < 2.0"
    }
  }
}

# HCLOUD_TOKEN is read directly by the provider; never put it in tfvars.
provider "hcloud" {}
