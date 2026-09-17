# ---------------------------------------------------------------------------
# Secrets Manager — replaces plaintext .env secrets in production
#
# Populate the secret's contents once (do not put a real payload in this
# file or in terraform.tfvars):
#   aws secretsmanager put-secret-value \
#     --secret-id ${var.project_name}/app-secrets \
#     --secret-string file://secrets-payload.json
# where secrets-payload.json is:
#   {"SECRET_KEY": "...", "ENCRYPTION_KEY": "...", "DB_PASS": "...",
#    "SMTP_PASSWORD": "...", "SIGNUP_SECRET": "..."}
#
# Then set on the EC2 instance (plain, non-secret env vars — just the
# secret's name, not its contents):
#   AWS_SECRET_ID=employee-attendance/app-secrets
#   AWS_REGION=ap-south-1
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "app_secrets" {
  name                    = "${var.project_name}/app-secrets"
  description             = "SECRET_KEY, ENCRYPTION_KEY, DB_PASS, SMTP creds, SIGNUP_SECRET for ${var.project_name}"
  recovery_window_in_days = 7

  tags = {
    Name = "${var.project_name}-app-secrets"
  }
}

data "aws_iam_policy_document" "secrets_read" {
  statement {
    sid       = "ReadAppSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.app_secrets.arn]
  }
}

# Attached to the SAME role the EC2 instance already assumes
# (aws_iam_role.ec2_app_role in main.tf) — there is no ECS Task Execution
# Role in this deployment; the EC2 instance role is the direct analog. Its
# trust policy (data.aws_iam_policy_document.ec2_assume_role in main.tf,
# principal = ec2.amazonaws.com) is unchanged — only the permissions
# attached to the role grow here, not who can assume it.
resource "aws_iam_role_policy" "secrets_read" {
  name   = "${var.project_name}-secrets-read"
  role   = aws_iam_role.ec2_app_role.id
  policy = data.aws_iam_policy_document.secrets_read.json
}

# ---------------------------------------------------------------------------
# KMS customer-managed key — enforces encryption on EBS volumes
#
# The app server's own EBS volume (holding dataset/ raw biometric face
# images and static/employee_docs/) is NOT Terraform-managed — it was
# provisioned outside Terraform, same as the instance itself (see main.tf
# header comment). This CMK + the region-default-encryption resource below
# force every NEW volume in this account/region to use it. The EXISTING
# volume must be migrated manually — see the CLI steps below.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "ebs_kms" {}

data "aws_iam_policy_document" "ebs_cmk_policy" {
  # Root account retains administrative control of the key — required by
  # AWS or the key becomes unmanageable if the specific grants below are
  # ever insufficient.
  statement {
    sid    = "EnableRootAccountAccess"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.ebs_kms.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  # EBS/EC2 need to use the key on behalf of the account to encrypt/decrypt
  # volume data and create the grants that back each attached volume.
  statement {
    sid    = "AllowEBSServiceUse"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.ebs_kms.account_id}:root"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid    = "AllowEBSGrants"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.ebs_kms.account_id}:root"]
    }
    actions   = ["kms:CreateGrant"]
    resources = ["*"]
    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }
}

resource "aws_kms_key" "ebs_cmk" {
  description             = "Customer-managed key for EBS volume encryption — ${var.project_name}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.ebs_cmk_policy.json

  tags = {
    Name = "${var.project_name}-ebs-cmk"
  }
}

resource "aws_kms_alias" "ebs_cmk" {
  name          = "alias/${var.project_name}-ebs"
  target_key_id = aws_kms_key.ebs_cmk.key_id
}

# Forces every NEW EBS volume created in this account/region to be
# encrypted — does not retroactively encrypt the existing app-server volume.
resource "aws_ebs_encryption_by_default" "this" {
  enabled = true
}

resource "aws_ebs_default_kms_key" "this" {
  key_arn = aws_kms_key.ebs_cmk.arn
}

# ---------------------------------------------------------------------------
# WAFv2 — OWASP Top 10 managed rules
#
# WAFv2 can only attach to CloudFront, an ALB, or API Gateway — never
# directly to an EC2 instance. This deployment has no ALB (nginx on the EC2
# instance receives internet traffic directly today), so this Web ACL is
# built for a CloudFront distribution in front of the instance. Attaching
# this means: DNS starts pointing at the CloudFront domain instead of the
# EC2 instance's IP, and CloudFront becomes the TLS termination point. That
# is a real architecture change with a DNS cutover — apply deliberately,
# not as a drive-by `terraform apply`.
# ---------------------------------------------------------------------------

resource "aws_wafv2_web_acl" "app" {
  provider    = aws.us_east_1 # CloudFront-scoped WAF must be created in us-east-1
  name        = "${var.project_name}-waf"
  description = "OWASP Top 10 protection for ${var.project_name}"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "AWS-AWSManagedRulesCommonRuleSet"
    priority = 1
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-AWSManagedRulesSQLiRuleSet"
    priority = 2
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-sqli-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-AWSManagedRulesKnownBadInputsRuleSet"
    priority = 3
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  # Rate-based rule — throttles a single IP hammering any endpoint,
  # independent of and in addition to Flask-Limiter's per-route limits
  # (which only see traffic that already reached the app).
  rule {
    name     = "RateLimitPerIP"
    priority = 4
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = 2000 # requests per 5-minute window per IP
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name = "${var.project_name}-waf"
  }
}

variable "app_origin_domain" {
  description = "Public DNS name (or Elastic IP's reverse DNS) of the existing EC2 instance that CloudFront will origin to — e.g. ec2-x-x-x-x.compute.amazonaws.com"
  type        = string
  default     = ""
}

resource "aws_cloudfront_distribution" "app" {
  count      = var.app_origin_domain == "" ? 0 : 1
  enabled    = true
  web_acl_id = aws_wafv2_web_acl.app.arn

  origin {
    domain_name = var.app_origin_domain
    origin_id   = "${var.project_name}-ec2-origin"

    custom_origin_config {
      http_port              = 8080
      https_port             = 8443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "${var.project_name}-ec2-origin"
    viewer_protocol_policy = "redirect-to-https"
    # This app is dynamic (session cookies, CSRF tokens) on nearly every
    # route — cache nothing by default. A follow-up cache behavior can carve
    # out /static/* separately once this is stable in production.
    forwarded_values {
      query_string = true
      cookies {
        forward = "all"
      }
      headers = ["*"]
    }
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
    # Replace with acm_certificate_arn (issued in us-east-1) + a custom
    # aliases block once a real domain is pointed at this distribution.
  }

  tags = {
    Name = "${var.project_name}-cdn"
  }
}

# ---------------------------------------------------------------------------
# ECR — scan on push
#
# Not currently used: compose.yaml builds the app image locally on the EC2
# instance (`build: .`), nothing is pushed to a registry today. Creating
# this repo only gets you scan-on-push if your CI/CD is changed to build,
# push here, then have the instance pull instead of building locally —
# that is a real workflow change, not a drop-in addition. Trivy in CI
# (see the deploy.yml gate suggested earlier) gets you equivalent scan
# coverage without adopting a registry at all.
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "app" {
  name                 = var.project_name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.ebs_cmk.arn
  }

  tags = {
    Name = "${var.project_name}-ecr"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 14 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 14
      }
      action = { type = "expire" }
    }]
  })
}
