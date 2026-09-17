# ---------------------------------------------------------------------------
# Customer-managed KMS key for RDS storage encryption
#
# aws_db_instance.this (main.tf) is already encrypted at rest
# (storage_encrypted = true) but with the AWS-managed key, since no
# kms_key_id was set. AWS only applies kms_key_id at CREATION time -- it
# cannot be changed on a live instance -- so simply adding kms_key_id to
# that resource here would make Terraform plan a destroy+recreate of the
# production database the moment someone ran `terraform apply`. That is
# NOT wired up in this file for exactly that reason.
#
# What this file does:
#   1. Creates the CMK itself (harmless -- provisioning a key touches
#      nothing about the live database) and makes its ARN available via
#      output for the manual migration steps below.
#   2. Defines the REPLACEMENT instance as a template resource
#      (aws_db_instance.this_cmk), gated off by var.enable_rds_cmk_cutover
#      (default false). A plain `terraform apply` today creates only the
#      key -- the replacement instance is not created until that variable
#      is deliberately flipped to true during a planned migration window,
#      per RDS_CMK_MIGRATION.md.
#
# See RDS_CMK_MIGRATION.md at the repo root for the full step-by-step
# procedure (snapshot -> re-encrypted copy -> restore -> cutover ->
# decommission), downtime estimate, and rollback plan. Do not set
# enable_rds_cmk_cutover=true outside that documented, deliberate window.
# ---------------------------------------------------------------------------

data "aws_caller_identity" "rds_kms" {}

data "aws_iam_policy_document" "rds_cmk_policy" {
  # Root account retains administrative control of the key -- required by
  # AWS or the key becomes unmanageable if the specific grants below are
  # ever insufficient. Same statement shape as aws_kms_key.ebs_cmk's own
  # policy (security_hardening.tf) for consistency.
  statement {
    sid    = "EnableRootAccountAccess"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.rds_kms.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  # RDS needs to use the key on behalf of the account to encrypt/decrypt
  # storage and to create/manage the grants that back the DB instance and
  # its automated snapshots. Scoped to only fire when RDS is the caller
  # (kms:ViaService), same pattern as ebs_cmk_policy's AllowEBSServiceUse.
  statement {
    sid    = "AllowRDSServiceUse"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.rds_kms.account_id}:root"]
    }
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
      "kms:CreateGrant",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["rds.${var.aws_region}.amazonaws.com"]
    }
  }

  # Explicit administrative access (rotate/describe/tag, NOT full kms:*)
  # for the IAM roles that actually operate this key day to day, separate
  # from the blanket root-account statement above -- so an IAM role/user
  # that isn't the account root can still manage it without assuming
  # root. Defaults to the existing EC2 app role (main.tf) if
  # var.kms_admin_role_arns is left empty -- this repo has no dedicated
  # "human admin" IAM role today (access is via trusted_admin_cidrs at the
  # network layer, not a named IAM principal), so set this variable
  # explicitly to whatever IAM roles your operators/CI actually assume.
  statement {
    sid    = "AllowKeyAdministration"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = length(var.kms_admin_role_arns) > 0 ? var.kms_admin_role_arns : [aws_iam_role.ec2_app_role.arn]
    }
    actions = [
      "kms:Describe*",
      "kms:List*",
      "kms:Get*",
      "kms:EnableKeyRotation",
      "kms:DisableKeyRotation",
      "kms:TagResource",
      "kms:UntagResource",
    ]
    resources = ["*"]
  }
}

variable "kms_admin_role_arns" {
  description = "IAM role ARNs allowed to administer the RDS CMK (rotate/describe/tag -- not full kms:* beyond what the root-account statement already grants). Defaults to the existing EC2 app role if left empty; set explicitly to your operator/CI roles once you have dedicated ones."
  type        = list(string)
  default     = []
}

resource "aws_kms_key" "rds_cmk" {
  description             = "Customer-managed key for RDS storage encryption -- ${var.project_name}"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.rds_cmk_policy.json

  tags = {
    Name = "${var.project_name}-rds-cmk"
  }
}

resource "aws_kms_alias" "rds_cmk" {
  name          = "alias/${var.project_name}-rds"
  target_key_id = aws_kms_key.rds_cmk.key_id
}

# ---------------------------------------------------------------------------
# Cutover instance template -- INERT by default (count = 0).
#
# Only created once BOTH var.enable_rds_cmk_cutover = true AND
# var.rds_cmk_cutover_snapshot_id name a snapshot that has ALREADY been
# re-encrypted with aws_kms_key.rds_cmk (RDS_CMK_MIGRATION.md step 2 --
# `aws rds copy-db-snapshot --kms-key-id <rds_cmk_arn output>`). This is
# the one genuinely destructive-adjacent resource in this file (it stands
# up a brand-new RDS instance from a snapshot) -- everything else here
# (the key, its alias, its policy) is safe to apply at any time.
# ---------------------------------------------------------------------------

variable "enable_rds_cmk_cutover" {
  description = "Set true ONLY during the planned RDS CMK migration window (see RDS_CMK_MIGRATION.md) to create the replacement instance from rds_cmk_cutover_snapshot_id. Leave false otherwise."
  type        = bool
  default     = false
}

variable "rds_cmk_cutover_snapshot_id" {
  description = "Identifier of the RE-ENCRYPTED snapshot (see RDS_CMK_MIGRATION.md step 2) to restore the new CMK-encrypted instance from. Required when enable_rds_cmk_cutover=true; ignored otherwise."
  type        = string
  default     = ""
}

resource "aws_db_instance" "this_cmk" {
  count = var.enable_rds_cmk_cutover ? 1 : 0

  identifier          = "${var.project_name}-db-cmk"
  snapshot_identifier = var.rds_cmk_cutover_snapshot_id
  instance_class      = var.db_instance_class
  storage_type        = "gp3"

  # username/password/db_name are deliberately NOT set -- a snapshot
  # restore inherits them from the snapshot itself; setting a different
  # username here would conflict with the restored data. To rotate the
  # master password post-cutover, do it against the running instance
  # afterward (aws rds modify-db-instance --master-user-password), not by
  # adding `password` to this resource.
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  backup_retention_period = var.db_backup_retention_days
  backup_window           = "17:00-18:00"
  maintenance_window      = "sun:18:30-sun:19:30"

  # kms_key_id is deliberately NOT set here -- a snapshot restore always
  # inherits the encryption key already baked into the snapshot (set by
  # the `copy-db-snapshot --kms-key-id` step in RDS_CMK_MIGRATION.md); RDS
  # does not allow specifying a different key on restore.
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-db-cmk-final-snapshot"
  deletion_protection       = true

  tags = {
    Name = "${var.project_name}-db-cmk"
  }
}

output "rds_cmk_arn" {
  description = "Use with `aws rds copy-db-snapshot --kms-key-id <this>` during the migration -- see RDS_CMK_MIGRATION.md"
  value       = aws_kms_key.rds_cmk.arn
}

output "rds_cmk_alias" {
  value = aws_kms_alias.rds_cmk.name
}

output "rds_cmk_cutover_endpoint" {
  description = "New CMK-encrypted RDS endpoint -- only populated once enable_rds_cmk_cutover=true has actually been applied. Paste into .env as DB_HOST after validating (RDS_CMK_MIGRATION.md step 5), before decommissioning the old instance."
  value       = length(aws_db_instance.this_cmk) > 0 ? aws_db_instance.this_cmk[0].address : null
}
