# ---------------------------------------------------------------------------
# AWS Network Firewall — genuine NGFW: stateful deep packet inspection,
# Suricata-compatible IDS/IPS rule evaluation, protocol filtering.
#
# Closes the NGFW gap noted in FIREWALL_ARCHITECTURE.md #4 (previously
# covered only piecemeal by WAF + ClamAV + Flask-Limiter, none of which do
# real packet inspection). Real ongoing cost: ~$0.395/hr per firewall
# endpoint (~$285/month) + $0.065/GB processed, billed continuously once
# applied, independent of traffic volume — confirm this is actually wanted
# before running terraform apply.
#
# RESOURCE-COMPLETE, TRAFFIC-PATH-INCOMPLETE: this file creates the
# firewall, its rule group, and its policy, and associates it with
# var.firewall_subnet_ids. It does NOT create or modify VPC route tables.
# AWS Network Firewall only inspects traffic that a route table explicitly
# sends through its endpoint (app subnet -> firewall subnet -> IGW, and
# back) — this project's vpc_id/subnet_ids are pre-existing, externally
# managed infrastructure (see main.tf's header comment), and this Terraform
# config has never managed route tables anywhere. Wiring the real traffic
# path means editing the existing route tables for the app's subnet and
# the IGW-facing subnet, which isn't captured here — a real network change
# to plan deliberately against your actual VPC layout, not a mechanical
# follow-on to `terraform apply`.
# ---------------------------------------------------------------------------

variable "firewall_subnet_ids" {
  description = "Subnet ID(s) dedicated to Network Firewall endpoints — AWS requires these to be SEPARATE from the app's own subnet(s), one per AZ you want coverage in. Leave empty ([]) to provision the firewall/policy/rule-group without any subnet association (and therefore inspecting no traffic) until you've planned the route-table changes above."
  type        = list(string)
  default     = []
}

resource "aws_networkfirewall_rule_group" "app_stateful" {
  count    = length(var.firewall_subnet_ids) > 0 ? 1 : 0
  name     = "${var.project_name}-stateful-rules"
  type     = "STATEFUL"
  capacity = 100

  rule_group {
    rules_source {
      # Suricata-compatible rules. This is a STARTER skeleton, not a
      # complete IDS/IPS ruleset — mirrors the app_firewall Security
      # Group's "closed" list (21/23/25, plaintext protocols this app
      # never speaks) plus one basic anomaly signature. Extend with AWS
      # Managed Rule Groups for Network Firewall or a subscribed
      # threat-intel feed before relying on this for real coverage.
      rules_string = <<-EOT
        drop tcp any any -> any 21 (msg:"Block FTP - not used by this app"; sid:1000001; rev:1;)
        drop tcp any any -> any 23 (msg:"Block Telnet - not used by this app"; sid:1000002; rev:1;)
        alert tcp any any -> any any (msg:"Suspiciously long URI (possible injection attempt)"; content:"HTTP"; http.uri; dsize:>2000; sid:1000003; rev:1;)
      EOT
    }
  }

  tags = {
    Name = "${var.project_name}-stateful-rules"
  }
}

resource "aws_networkfirewall_firewall_policy" "app" {
  count = length(var.firewall_subnet_ids) > 0 ? 1 : 0
  name  = "${var.project_name}-fw-policy"

  firewall_policy {
    stateless_default_actions          = ["aws:forward_to_sfe"]
    stateless_fragment_default_actions = ["aws:forward_to_sfe"]

    stateful_rule_group_reference {
      resource_arn = aws_networkfirewall_rule_group.app_stateful[0].arn
    }

    stateful_engine_options {
      rule_order = "STRICT_ORDER"
    }

    # STRICT_ORDER requires an explicit default for traffic that matches
    # no stateful rule. "aws:alert_strict" (monitor, don't block) on
    # purpose — this is a brand-new firewall with a starter ruleset;
    # switch to "aws:drop_strict" only after watching CloudWatch alerts
    # for a while and confirming nothing legitimate gets flagged.
    stateful_default_actions = ["aws:alert_strict"]
  }

  tags = {
    Name = "${var.project_name}-fw-policy"
  }
}

resource "aws_networkfirewall_firewall" "app" {
  count               = length(var.firewall_subnet_ids) > 0 ? 1 : 0
  name                = "${var.project_name}-network-firewall"
  vpc_id              = var.vpc_id
  firewall_policy_arn = aws_networkfirewall_firewall_policy.app[0].arn
  delete_protection   = true

  dynamic "subnet_mapping" {
    for_each = var.firewall_subnet_ids
    content {
      subnet_id = subnet_mapping.value
    }
  }

  tags = {
    Name = "${var.project_name}-network-firewall"
  }
}

resource "aws_cloudwatch_log_group" "network_firewall" {
  count             = length(var.firewall_subnet_ids) > 0 ? 1 : 0
  name              = "/aws/network-firewall/${var.project_name}"
  retention_in_days = 30
}

resource "aws_networkfirewall_logging_configuration" "app" {
  count        = length(var.firewall_subnet_ids) > 0 ? 1 : 0
  firewall_arn = aws_networkfirewall_firewall.app[0].arn

  logging_configuration {
    log_destination_config {
      log_destination = {
        logGroup = aws_cloudwatch_log_group.network_firewall[0].name
      }
      log_destination_type = "CloudWatchLogs"
      log_type             = "ALERT"
    }
  }
}
