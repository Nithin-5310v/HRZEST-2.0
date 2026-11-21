# The AWS edge in front of the existing EC2 host (docs/FIREWALL.md, layer 1):
#
#   internet -> ALB (TLS, ACM) + WAFv2 web ACL -> instance:443 (nginx + ModSecurity) -> gunicorn
#
# Everything here is new and sits beside the instance; nothing changes the
# instance itself. Attaching the security groups, the instance role and the
# DNS switch are deliberate runbook steps (README.md), printed as outputs.

data "aws_instance" "app" {
  instance_id = var.instance_id
}

data "aws_subnet" "instance" {
  id = data.aws_instance.app.subnet_id
}

data "aws_vpc" "this" {
  id = data.aws_subnet.instance.vpc_id
}

data "aws_caller_identity" "current" {}

locals {
  vpc_id      = data.aws_vpc.this.id
  vpc_cidrs   = [for a in data.aws_vpc.this.cidr_block_associations : a.cidr_block]
  domains     = [var.domain, "www.${var.domain}"]
  create_cert = var.certificate_arn == ""
  cert_arn    = local.create_cert ? aws_acm_certificate_validation.this[0].certificate_arn : var.certificate_arn
  role_name   = var.existing_instance_role_name != "" ? var.existing_instance_role_name : aws_iam_role.instance[0].name

  # Paths, with or without the /<company>/ prefix (utils/tenant_routing.py).
  # Same set as nginx's login location (scripts/host-hardening/nginx/
  # hrzest-site.conf); /api/v1/x is the same route as /api/x (app.py).
  # AWS WAF caps each pattern at 200 characters, hence
  # three; a request matching any of them counts.
  login_path_regexes = [
    "^/(?:[a-z0-9][a-z0-9-]*/)?(?:login|admin_login|admin-login|employee_login|hr_login|mfa_verify)$",
    "^/(?:[a-z0-9][a-z0-9-]*/)?api/(?:v1/)?(?:login|login/verify|employee/login|signin|forgot_password)$",
    "^/(?:[a-z0-9][a-z0-9-]*/)?(?:super_admin/login|socperson/login|forgot_password|admin_forgot_password|employee_forgot_password)$",
  ]
  payroll_path_regexes = [
    "^/(?:[a-z0-9][a-z0-9-]*/)?(?:api/(?:v1/)?payroll|api/(?:v1/)?hr_dashboard/payroll|lock_payroll|unlock_payroll|payroll_settings|salary)",
  ]
}
