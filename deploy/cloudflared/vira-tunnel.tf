# =============================================================================
# vira-engine API - Cloudflare Tunnel ingress
# =============================================================================
#
# THIS FILE IS NOT APPLIED FROM THIS REPO. It is a copy-paste source.
#
# All IdeaPlaces DNS and tunnel resources are Terraform-managed in one place:
#
#   ideaplaces-devops/infrastructure/terraform/azure/environments/shared/cloudflare.tf
#
# Creating the record by hand in the Cloudflare dashboard would work and would
# then be silently reverted by the next `terraform apply`. Paste the block you
# want into that file, `terraform plan`, `terraform apply`.
#
# The edge is a tunnel, not a reverse proxy on the box, for the reason that
# decided it for C3 and for both dev VMs: the connection is outbound only.
# No inbound port, no NSG rule, no certificate to renew, and nothing that
# collides with sshd — which on chipdev listens on 443 and is the only way in.
# TLS terminates at Cloudflare's edge; cloudflared hands plain HTTP to
# 127.0.0.1:8720. That is why there is no Caddy and no TLS config anywhere in
# this repo.
#
#   browser -> https://vira.ideaplaces.com
#           -> Cloudflare edge (TLS)
#           -> tunnel (outbound from chipdev)
#           -> cloudflared on chipdev
#           -> 127.0.0.1:8720 (uvicorn)
#
# Reference: docs.ideaplaces.com/c3/tunnel, and the c3 / chip_dev / luca_dev
# blocks already in cloudflare.tf.


# -----------------------------------------------------------------------------
# OPTION A (preferred) - add one ingress rule to the tunnel chipdev already runs
# -----------------------------------------------------------------------------
# chipdev already has a cloudflared daemon connected to the `c3-chipdev` tunnel.
# A tunnel carries as many hostnames as you give it, and this config is
# *remotely* managed — cloudflared polls it. So adding vira costs one terraform
# apply and requires touching the box not at all: no second daemon, no restart,
# no blip on c3.
#
# It also sidesteps a real trap. `cloudflared service install` writes
# /etc/systemd/system/cloudflared.service, and that unit is already taken by the
# c3 tunnel. A second tunnel needs a hand-written second unit under a different
# name, which is a new thing to maintain on a box that already runs seven
# Actions runners.
#
# Edit the EXISTING resource in cloudflare.tf — do not add a second one:
#
#   resource "cloudflare_zero_trust_tunnel_cloudflared_config" "c3" {
#     account_id = data.azurerm_key_vault_secret.cloudflare_account_id.value
#     tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.c3.id
#
#     config {
#       ingress_rule {
#         hostname = "c3-chip.ideaplaces.com"
#         service  = "http://localhost:8347"
#       }
#
#       # >>> ADD THIS RULE, above the catch-all. Order matters: the first
#       # >>> matching rule wins and http_status:404 matches everything.
#       ingress_rule {
#         hostname = "vira.ideaplaces.com"
#         service  = "http://localhost:8720"
#       }
#
#       ingress_rule {
#         service = "http_status:404"
#       }
#     }
#   }
#
# Then add the DNS record. Note `proxied = true` — the opposite of every
# Container App record in this file. A cfargotunnel.com target only resolves
# through Cloudflare's proxy; DNS-only would hand the client a name that
# answers nothing.

resource "cloudflare_record" "vira" {
  zone_id = data.cloudflare_zone.ideaplaces.id
  name    = "vira"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.c3.id}.cfargotunnel.com"
  ttl     = 1
  proxied = true
  comment = "vira-engine API - Cloudflare Tunnel to chipdev:8720"
}


# -----------------------------------------------------------------------------
# OPTION B - a dedicated `vira-chipdev` tunnel
# -----------------------------------------------------------------------------
# Take this instead if vira should be able to go down, be rebuilt, or be handed
# to someone else without the c3 tunnel being in the blast radius. The cost is a
# second cloudflared daemon on the box and a second systemd unit that is not the
# one `cloudflared service install` creates by default.
#
# Requires a new Key Vault secret first. Naming follows the devops convention
# {service}-{purpose}-{project-code-name}, in kv-ideaplaces alongside the other
# tunnel secrets — NOT in kv-zerohuman-hack, which holds this project's
# application keys, not IdeaPlaces infrastructure:
#
#   az keyvault secret set --vault-name kv-ideaplaces \
#     --name cloudflare-tunnel-secret-vira \
#     --value "$(openssl rand -base64 32)"
#
# and the matching data block where the others live:
#
#   data "azurerm_key_vault_secret" "cloudflare_tunnel_secret_vira" {
#     name         = "cloudflare-tunnel-secret-vira"
#     key_vault_id = data.azurerm_key_vault.main.id
#   }
#
# resource "cloudflare_zero_trust_tunnel_cloudflared" "vira" {
#   account_id = data.azurerm_key_vault_secret.cloudflare_account_id.value
#   name       = "vira-chipdev"
#   secret     = data.azurerm_key_vault_secret.cloudflare_tunnel_secret_vira.value
# }
#
# resource "cloudflare_zero_trust_tunnel_cloudflared_config" "vira" {
#   account_id = data.azurerm_key_vault_secret.cloudflare_account_id.value
#   tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.vira.id
#
#   config {
#     ingress_rule {
#       hostname = "vira.ideaplaces.com"
#       service  = "http://localhost:8720"
#     }
#     ingress_rule {
#       service = "http_status:404"
#     }
#   }
# }
#
# resource "cloudflare_record" "vira" {
#   zone_id = data.cloudflare_zone.ideaplaces.id
#   name    = "vira"
#   type    = "CNAME"
#   content = "${cloudflare_zero_trust_tunnel_cloudflared.vira.id}.cfargotunnel.com"
#   ttl     = 1
#   proxied = true
#   comment = "vira-engine API - dedicated Cloudflare Tunnel to chipdev:8720"
# }
#
# Then, on the box, install the second daemon under its own unit name. The
# token comes out of terraform state, never off a screen:
#
#   cd ideaplaces-devops/infrastructure/terraform/azure/environments/shared
#   TUNNEL_TOKEN=$(terraform show -json | python3 -c "
#   import sys, json
#   state = json.load(sys.stdin)
#   for r in state['values']['root_module']['resources']:
#       if r['address'] == 'cloudflare_zero_trust_tunnel_cloudflared.vira':
#           print(r['values']['tunnel_token'])
#   ")
#   sudo mkdir -p /etc/cloudflared-vira
#   printf 'TUNNEL_TOKEN=%s\n' "$TUNNEL_TOKEN" | sudo tee /etc/cloudflared-vira/token.env >/dev/null
#   sudo chmod 600 /etc/cloudflared-vira/token.env
#   sudo tee /etc/systemd/system/cloudflared-vira.service >/dev/null <<'UNIT'
#   [Unit]
#   Description=cloudflared tunnel (vira)
#   After=network-online.target
#   Wants=network-online.target
#
#   [Service]
#   Type=notify
#   EnvironmentFile=/etc/cloudflared-vira/token.env
#   ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run --token ${TUNNEL_TOKEN}
#   Restart=always
#   RestartSec=5
#
#   [Install]
#   WantedBy=multi-user.target
#   UNIT
#   sudo systemctl daemon-reload && sudo systemctl enable --now cloudflared-vira


# -----------------------------------------------------------------------------
# Hostname
# -----------------------------------------------------------------------------
# vira.ideaplaces.com. Confirmed unclaimed (NXDOMAIN as of 2026-08-15).
#
# What is already pointed at chipdev, so nothing here collides with it:
#
#   chip.ideaplaces.dev       -> tunnel `chip-dev`   -> localhost:7291 (GitPipeline)
#   luca.ideaplaces.dev       -> tunnel `luca-dev`   -> lucadev:7291
#   c3-chip.ideaplaces.com    -> tunnel `c3-chipdev` -> localhost:8347 (C3)
#
# chip.ideaplaces.COM does not exist — the record is on the .dev zone, which is
# why it did not answer. It is also occupied by GitPipeline local dev on 7291,
# so it was never a candidate.
#
# -----------------------------------------------------------------------------
# What this does NOT give you
# -----------------------------------------------------------------------------
# Authentication. The tunnel publishes the API to the entire internet with no
# gate in front of it. C3 solved this with magic-link auth in the app itself;
# the alternative that needs no application code is a Cloudflare Access policy
# on the hostname (the shared cloudflare-api-token already carries
# `Account.Access: Apps and Policies` Edit, so it is a terraform block, not a
# new credential). Decide which before the hostname goes live, not after.
#
# One behaviour to carry into the API: behind a tunnel the request URL the
# framework sees is the internal bind (127.0.0.1:8720), not the public name.
# Any absolute URL the API hands back — a /media link in a job response most of
# all — must be built from the Host / X-Forwarded-Host header. C3 hit exactly
# this and it is written up in docs.ideaplaces.com/c3/tunnel.
