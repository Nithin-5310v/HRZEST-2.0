# ---------------------------------------------------------------------------
# Standalone log-analysis server (Grafana + Loki) — Wave 3 observability work.
#
# Deliberately a SEPARATE EC2 instance from the app server, so a runaway
# Grafana query or a log storm never competes with the HRMS app for CPU,
# memory, or disk I/O. See observability/README.md for the full deploy
# walkthrough; this file only provisions the AWS-side infrastructure
# (network access, storage, IAM) -- it does not install Docker or run the
# containers themselves.
# ---------------------------------------------------------------------------

variable "log_server_instance_type" {
  description = "Log server EC2 size -- t3.small comfortably handles Loki+Grafana for a single small app's log volume; resize up only if Grafana queries start feeling slow"
  type        = string
  default     = "t3.small"
}

variable "log_server_subnet_id" {
  description = <<-EOT
    A PUBLIC subnet (has a route to an internet gateway) in the same VPC as
    var.vpc_id -- deliberately NOT one of var.subnet_ids, which main.tf
    documents as being for the RDS subnet group and are typically private.
    This instance needs: (a) outbound internet for its own package
    installs, and (b) a public IP so you can reach Grafana directly from a
    trusted_admin_cidrs IP -- the same access model main.tf's app_firewall
    already uses for the app server itself (public IP, SG-filtered
    management ports), not a VPN/bastion setup. If your VPC only has
    private subnets with no NAT gateway, you'll need one of those two
    instead before this instance can install Docker.
  EOT
  type        = string
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

# ---------------------------------------------------------------------------
# S3 bucket for Loki's chunk/index storage
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "loki" {
  bucket = "${var.project_name}-loki-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-loki"
  }
}

resource "aws_s3_bucket_public_access_block" "loki_pab" {
  bucket                  = aws_s3_bucket.loki.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "loki_sse" {
  bucket = aws_s3_bucket.loki.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "loki_lifecycle" {
  bucket = aws_s3_bucket.loki.id

  rule {
    id     = "expire-per-retention-policy"
    status = "Enabled"
    filter {
      prefix = ""
    }
    # Belt-and-suspenders alongside Loki's own retention_period
    # (loki-config.yaml, currently 90 days) -- if Loki's compactor is ever
    # down/misconfigured, objects still age out here instead of growing
    # (and costing) forever. Set comfortably longer than Loki's own
    # retention so this is a backstop, not the primary control.
    expiration {
      days = 120
    }
  }
}

# ---------------------------------------------------------------------------
# IAM role -- log server reads/writes only its own Loki bucket, nothing else
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "log_server_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "log_server_role" {
  name               = "${var.project_name}-log-server-role"
  assume_role_policy = data.aws_iam_policy_document.log_server_assume_role.json
}

data "aws_iam_policy_document" "loki_s3_rw" {
  statement {
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.loki.arn, "${aws_s3_bucket.loki.arn}/*"]
  }
}

resource "aws_iam_role_policy" "loki_s3_rw" {
  name   = "${var.project_name}-loki-s3-rw"
  role   = aws_iam_role.log_server_role.id
  policy = data.aws_iam_policy_document.loki_s3_rw.json
}

resource "aws_iam_instance_profile" "log_server_profile" {
  name = "${var.project_name}-log-server-profile"
  role = aws_iam_role.log_server_role.name
}

# ---------------------------------------------------------------------------
# Security group
#
#   3100 (Loki push API) — reachable ONLY from the app server's own security
#                           group (var.ec2_security_group_id), never from
#                           the internet or even other hosts in the VPC.
#                           This is the one port that matters most: it's
#                           how HR-system log data reaches this box.
#   3000 (Grafana UI)     — reachable only from var.trusted_admin_cidrs,
#                           same policy already used for the app server's
#                           own management ports (main.tf's app_firewall).
#   22   (SSH)             — same trusted_admin_cidrs restriction.
#
# Nothing else has a rule at all -- security groups default-deny.
# ---------------------------------------------------------------------------

resource "aws_security_group" "log_server" {
  name        = "${var.project_name}-log-server-sg"
  description = "log-server-security-group"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Loki push API - app server only"
    from_port       = 3100
    to_port         = 3100
    protocol        = "tcp"
    security_groups = [var.ec2_security_group_id]
  }

  ingress {
    description = "Grafana UI - trusted admin IPs only"
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = var.trusted_admin_cidrs
  }

  ingress {
    description = "SSH - trusted admin IPs only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.trusted_admin_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-log-server-sg"
  }
}

# ---------------------------------------------------------------------------
# The log server itself
# ---------------------------------------------------------------------------

resource "aws_instance" "log_server" {
  ami                         = data.aws_ami.al2023.id
  instance_type               = var.log_server_instance_type
  subnet_id                   = var.log_server_subnet_id
  vpc_security_group_ids      = [aws_security_group.log_server.id]
  iam_instance_profile        = aws_iam_instance_profile.log_server_profile.name
  associate_public_ip_address = true

  # Same CMK the app server's own volumes use (security_hardening.tf) --
  # one key to manage/rotate, not a second one just for this box.
  root_block_device {
    volume_size = 30
    encrypted   = true
    kms_key_id  = aws_kms_key.ebs_cmk.arn
  }

  # user_data installs Docker + Compose only -- it deliberately does NOT
  # pull/start the Loki/Grafana containers itself. Copy observability/
  # log-server/ to this instance (scp/rsync/git clone), fill in its .env,
  # then `docker compose up -d` by hand -- see observability/README.md.
  # Keeping that step manual means a `terraform apply` never silently
  # restarts your log stack (and briefly loses in-flight log data) just
  # because an unrelated variable changed.
  user_data = <<-EOF
    #!/bin/bash
    set -euo pipefail
    dnf install -y docker
    systemctl enable --now docker
    usermod -aG docker ec2-user
    curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
      -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
  EOF

  tags = {
    Name = "${var.project_name}-log-server"
  }
}

output "log_server_private_ip" {
  description = "Put this in observability/promtail/promtail-config.yaml's clients[0].url on the APP server (replacing LOKI_SERVER_PRIVATE_IP)"
  value       = aws_instance.log_server.private_ip
}

output "log_server_public_ip" {
  description = "For your own SSH/Grafana-UI access from a trusted_admin_cidrs IP -- the app server never uses this, only the private IP above"
  value       = aws_instance.log_server.public_ip
}

output "loki_s3_bucket" {
  value = aws_s3_bucket.loki.bucket
}
