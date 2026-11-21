# AWS WAFv2 web ACL on the load balancer. Rule order (lowest priority first):
#
#   0  blocklist        the SOC console's ban list (IP sets the firewall agent keeps in sync)
#   1  trusted          labels trusted addresses (customer offices, monitors): exempt from 10-20
#   10 rate-login       sign-in endpoints, per client address
#   11 rate-payroll     payroll endpoints, per client address
#   12 rate-all         everything, per client address
#   20 geo              optional country allow / block list
#   30-34 AWS managed   IP reputation, Common (OWASP), Known bad inputs, SQLi, Linux
#
# The IP sets' contents are managed by the firewall agent from the SOC console
# (scripts/firewall_agent/), never by Terraform: `addresses` is ignored after
# creation so an apply can't wipe a ban.

locals {
  managed_groups = [
    { name = "AWSManagedRulesAmazonIpReputationList", priority = 30, overrides = [] },
    # SizeRestrictions_BODY refuses bodies over 8 KB -- every photo check-in and
    # document upload -- so it only counts here; nginx caps bodies at 20 MB.
    { name = "AWSManagedRulesCommonRuleSet", priority = 31, overrides = ["SizeRestrictions_BODY"] },
    { name = "AWSManagedRulesKnownBadInputsRuleSet", priority = 32, overrides = [] },
    { name = "AWSManagedRulesSQLiRuleSet", priority = 33, overrides = [] },
    { name = "AWSManagedRulesLinuxRuleSet", priority = 34, overrides = [] },
  ]
  trusted_label = "hrzest:trusted"
  rate_rules = [
    { name = "rate-login", priority = 10, limit = var.login_rate_limit, paths = aws_wafv2_regex_pattern_set.login.arn },
    { name = "rate-payroll", priority = 11, limit = var.payroll_rate_limit, paths = aws_wafv2_regex_pattern_set.payroll.arn },
  ]
}

# ── IP sets (contents: the firewall agent) ───────────────────────────────────
resource "aws_wafv2_ip_set" "block_v4" {
  name               = "${var.name}-blocklist-v4"
  description        = "SOC console ban list (IPv4), synced by the firewall agent"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = []
  lifecycle {
    ignore_changes = [addresses]
  }
}

resource "aws_wafv2_ip_set" "block_v6" {
  name               = "${var.name}-blocklist-v6"
  description        = "SOC console ban list (IPv6), synced by the firewall agent"
  scope              = "REGIONAL"
  ip_address_version = "IPV6"
  addresses          = []
  lifecycle {
    ignore_changes = [addresses]
  }
}

resource "aws_wafv2_ip_set" "trusted_v4" {
  name               = "${var.name}-trusted-v4"
  description        = "Trusted addresses (SOC Ban review: Trust; WAF_NEVER_BAN_CIDRS), synced by the firewall agent"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = []
  lifecycle {
    ignore_changes = [addresses]
  }
}

resource "aws_wafv2_ip_set" "trusted_v6" {
  name               = "${var.name}-trusted-v6"
  description        = "Trusted addresses (IPv6), synced by the firewall agent"
  scope              = "REGIONAL"
  ip_address_version = "IPV6"
  addresses          = []
  lifecycle {
    ignore_changes = [addresses]
  }
}

# ── path sets ────────────────────────────────────────────────────────────────
resource "aws_wafv2_regex_pattern_set" "login" {
  name  = "${var.name}-login-paths"
  scope = "REGIONAL"
  dynamic "regular_expression" {
    for_each = local.login_path_regexes
    content {
      regex_string = regular_expression.value
    }
  }
}

resource "aws_wafv2_regex_pattern_set" "payroll" {
  name  = "${var.name}-payroll-paths"
  scope = "REGIONAL"
  dynamic "regular_expression" {
    for_each = local.payroll_path_regexes
    content {
      regex_string = regular_expression.value
    }
  }
}

# ── the web ACL ──────────────────────────────────────────────────────────────
resource "aws_wafv2_web_acl" "this" {
  name        = "${var.name}-edge"
  description = "HRZEST edge firewall (docs/FIREWALL.md)"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  # Same shape as the app's own refusals ({"ok": false, "msg": ...}), so the
  # web and mobile clients show a message instead of failing to parse.
  custom_response_body {
    key          = "blocked"
    content      = "{\"ok\":false,\"msg\":\"Request blocked by security policy.\"}"
    content_type = "APPLICATION_JSON"
  }
  custom_response_body {
    key          = "slow_down"
    content      = "{\"ok\":false,\"msg\":\"Too many requests. Please slow down and try again shortly.\"}"
    content_type = "APPLICATION_JSON"
  }

  rule {
    name     = "blocklist"
    priority = 0
    action {
      block {
        custom_response {
          response_code            = 403
          custom_response_body_key = "blocked"
        }
      }
    }
    statement {
      or_statement {
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.block_v4.arn
          }
        }
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.block_v6.arn
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-blocklist"
      sampled_requests_enabled   = true
    }
  }

  # Count + label: marks trusted addresses for the rules below without
  # deciding anything itself. They still go through the managed rules.
  rule {
    name     = "trusted"
    priority = 1
    action {
      count {}
    }
    rule_label {
      name = local.trusted_label
    }
    statement {
      or_statement {
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.trusted_v4.arn
          }
        }
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.trusted_v6.arn
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-trusted"
      sampled_requests_enabled   = false
    }
  }

  dynamic "rule" {
    for_each = local.rate_rules
    content {
      name     = rule.value.name
      priority = rule.value.priority
      action {
        block {
          custom_response {
            response_code            = 429
            custom_response_body_key = "slow_down"
          }
        }
      }
      statement {
        rate_based_statement {
          limit                 = rule.value.limit
          evaluation_window_sec = 300
          aggregate_key_type    = "IP"
          scope_down_statement {
            and_statement {
              statement {
                regex_pattern_set_reference_statement {
                  arn = rule.value.paths
                  field_to_match {
                    uri_path {}
                  }
                  text_transformation {
                    priority = 0
                    type     = "LOWERCASE"
                  }
                }
              }
              statement {
                not_statement {
                  statement {
                    label_match_statement {
                      scope = "LABEL"
                      key   = local.trusted_label
                    }
                  }
                }
              }
            }
          }
        }
      }
      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name}-${rule.value.name}"
        sampled_requests_enabled   = true
      }
    }
  }

  rule {
    name     = "rate-all"
    priority = 12
    action {
      block {
        custom_response {
          response_code            = 429
          custom_response_body_key = "slow_down"
        }
      }
    }
    statement {
      rate_based_statement {
        limit                 = var.global_rate_limit
        evaluation_window_sec = 300
        aggregate_key_type    = "IP"
        scope_down_statement {
          not_statement {
            statement {
              label_match_statement {
                scope = "LABEL"
                key   = local.trusted_label
              }
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-all"
      sampled_requests_enabled   = true
    }
  }

  dynamic "rule" {
    for_each = var.geo_mode == "off" || length(var.geo_countries) == 0 ? [] : [var.geo_mode]
    content {
      name     = "geo"
      priority = 20
      action {
        block {
          custom_response {
            response_code            = 403
            custom_response_body_key = "blocked"
          }
        }
      }
      statement {
        and_statement {
          statement {
            dynamic "geo_match_statement" {
              for_each = rule.value == "block" ? [1] : []
              content {
                country_codes = var.geo_countries
              }
            }
            dynamic "not_statement" {
              for_each = rule.value == "allow_only" ? [1] : []
              content {
                statement {
                  geo_match_statement {
                    country_codes = var.geo_countries
                  }
                }
              }
            }
          }
          statement {
            not_statement {
              statement {
                label_match_statement {
                  scope = "LABEL"
                  key   = local.trusted_label
                }
              }
            }
          }
        }
      }
      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name}-geo"
        sampled_requests_enabled   = true
      }
    }
  }

  dynamic "rule" {
    for_each = local.managed_groups
    content {
      name     = rule.value.name
      priority = rule.value.priority
      override_action {
        dynamic "none" {
          for_each = var.managed_rules_action == "block" ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.managed_rules_action == "count" ? [1] : []
          content {}
        }
      }
      statement {
        managed_rule_group_statement {
          vendor_name = "AWS"
          name        = rule.value.name
          dynamic "rule_action_override" {
            for_each = rule.value.overrides
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }
      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name}-${rule.value.name}"
        sampled_requests_enabled   = true
      }
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-edge"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}

# ── WAF logs -> CloudWatch -> firewall agent -> SOC console ──────────────────
# Only decisions are logged (block, count, CAPTCHA, challenge); allowed
# requests are counted by the WAF's own AllowedRequests metric, which the
# agent reports instead. Authorization and Cookie are redacted at the source.
resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.name}" # the prefix AWS WAF requires
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_resource_policy" "waf" {
  policy_name = "${var.name}-waf-log-delivery"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AWSWAFLogDelivery"
      Effect    = "Allow"
      Principal = { Service = "delivery.logs.amazonaws.com" }
      Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource  = "${aws_cloudwatch_log_group.waf.arn}:*"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*" }
      }
    }]
  })
}

resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]

  redacted_fields {
    single_header {
      name = "authorization"
    }
  }
  redacted_fields {
    single_header {
      name = "cookie"
    }
  }

  logging_filter {
    default_behavior = "DROP"
    filter {
      behavior    = "KEEP"
      requirement = "MEETS_ANY"
      condition {
        action_condition {
          action = "BLOCK"
        }
      }
      condition {
        action_condition {
          action = "CAPTCHA"
        }
      }
      condition {
        action_condition {
          action = "CHALLENGE"
        }
      }
      condition {
        action_condition {
          action = "COUNT"
        }
      }
      condition {
        action_condition {
          action = "EXCLUDED_AS_COUNT"
        }
      }
    }
  }

  depends_on = [aws_cloudwatch_log_resource_policy.waf]
}
