# ---------------------------------------------------------------------------
# Android app build pipeline (AWS CodeBuild) -- an alternative to EAS Build
# for producing the mobile/ app's release AAB, since EAS Build/eas submit
# both require an interactive Expo account login that couldn't be completed
# non-interactively during initial setup (see MOBILE_AWS_BUILD.md).
#
# This does NOT replace the Google Play Store as the distribution channel --
# there is no AWS equivalent for that. It only replaces the BUILD step:
# CodeBuild checks out this repo, runs mobile/buildspec-android.yml (installs
# Node + the Android SDK, signs the release build with the keystore below),
# and uploads the resulting .aab to the S3 bucket below for you to download
# and upload to Play Console (Step 5/6 of the launch plan are unchanged).
#
# See MOBILE_AWS_BUILD.md for the one-time manual setup this file cannot do
# for you: populating the keystore secret's real value, and providing a
# GitHub Personal Access Token so CodeBuild can check out this repo.
# ---------------------------------------------------------------------------

variable "github_repo_url" {
  description = "HTTPS clone URL of this repo, for CodeBuild's source config"
  type        = string
  default     = "https://github.com/Magzest/HRZEST.git"
}

variable "github_pat" {
  description = <<-EOT
    A GitHub Personal Access Token (classic, 'repo' scope is enough) so
    CodeBuild can check out this repo. Pass via TF_VAR_github_pat, never
    committed. One-time: registers as an account-level CodeBuild source
    credential -- if one already exists for GitHub in this AWS account/
    region, this resource will conflict; see MOBILE_AWS_BUILD.md.
  EOT
  type        = string
  sensitive   = true
}

# ---------------------------------------------------------------------------
# S3 bucket for build output (.aab files) -- private, encrypted, short
# lifecycle since these are build artifacts, not durable data (the source
# code + keystore can always reproduce them).
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "mobile_build_artifacts" {
  bucket = "${var.project_name}-mobile-build-artifacts-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-mobile-build-artifacts"
  }
}

resource "aws_s3_bucket_public_access_block" "mobile_build_artifacts_pab" {
  bucket                  = aws_s3_bucket.mobile_build_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mobile_build_artifacts_sse" {
  bucket = aws_s3_bucket.mobile_build_artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "mobile_build_artifacts_lifecycle" {
  bucket = aws_s3_bucket.mobile_build_artifacts.id
  rule {
    id     = "expire-old-builds"
    status = "Enabled"
    filter {
      prefix = ""
    }
    expiration {
      days = 90
    }
  }
}

# ---------------------------------------------------------------------------
# Secrets Manager -- the Android upload keystore + its passwords.
#
# Same "provision the resource, populate the value out-of-band" pattern as
# security_hardening.tf's aws_secretsmanager_secret.app_secrets: this
# resource creates an EMPTY secret. Populate it once via:
#   aws secretsmanager put-secret-value \
#     --secret-id ${var.project_name}/mobile-android-signing \
#     --secret-string file://keystore-secret-payload.json
# where keystore-secret-payload.json is:
#   {"KEYSTORE_BASE64": "<base64 -w0 hrzest-upload-key.keystore>",
#    "KEYSTORE_STORE_PASSWORD": "...", "KEYSTORE_KEY_PASSWORD": "...",
#    "KEYSTORE_KEY_ALIAS": "hrzest-upload"}
# See MOBILE_AWS_BUILD.md for the exact commands using the keystore already
# generated for you.
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "mobile_android_signing" {
  name                    = "${var.project_name}/mobile-android-signing"
  description             = "Android release-signing keystore (base64) + passwords for mobile/buildspec-android.yml"
  recovery_window_in_days = 30

  tags = {
    Name = "${var.project_name}-mobile-android-signing"
  }
}

# ---------------------------------------------------------------------------
# IAM role -- CodeBuild reads only the signing secret and writes only to
# the build-artifacts bucket, same least-privilege pattern as every other
# role in this terraform/ directory.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "codebuild_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "codebuild_android_role" {
  name               = "${var.project_name}-codebuild-android-role"
  assume_role_policy = data.aws_iam_policy_document.codebuild_assume_role.json
}

data "aws_iam_policy_document" "codebuild_android_policy" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/codebuild/${var.project_name}-mobile-android:*"]
  }
  statement {
    sid       = "ArtifactsRW"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:GetBucketLocation", "s3:ListBucket"]
    resources = [aws_s3_bucket.mobile_build_artifacts.arn, "${aws_s3_bucket.mobile_build_artifacts.arn}/*"]
  }
  statement {
    sid       = "ReadSigningSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.mobile_android_signing.arn]
  }
}

resource "aws_iam_role_policy" "codebuild_android_policy" {
  name   = "${var.project_name}-codebuild-android-policy"
  role   = aws_iam_role.codebuild_android_role.id
  policy = data.aws_iam_policy_document.codebuild_android_policy.json
}

# ---------------------------------------------------------------------------
# GitHub source credential -- ONE PER AWS ACCOUNT/REGION for CodeBuild.
# If this account already has a GitHub source credential registered
# (via console or a prior `aws codebuild import-source-credentials`), this
# resource will fail to create a second one -- see MOBILE_AWS_BUILD.md.
# ---------------------------------------------------------------------------

resource "aws_codebuild_source_credential" "github" {
  auth_type   = "PERSONAL_ACCESS_TOKEN"
  server_type = "GITHUB"
  token       = var.github_pat
}

# ---------------------------------------------------------------------------
# The CodeBuild project itself.
#
# Uses CodeBuild's standard managed image (no custom Docker image/ECR repo
# needed) -- mobile/buildspec-android.yml installs the Android SDK
# command-line tools itself in the install phase, at the cost of a slower
# per-build install step than a purpose-built image would have. Reasonable
# tradeoff for infrequent release builds; revisit with a custom image in
# ECR (this repo already has aws_ecr_repository.app for the backend, see
# security_hardening.tf) if build time ever becomes a real problem.
# ---------------------------------------------------------------------------

resource "aws_codebuild_project" "mobile_android_build" {
  name          = "${var.project_name}-mobile-android"
  description   = "Builds mobile/'s release AAB, signed, uploaded to S3 -- see MOBILE_AWS_BUILD.md"
  service_role  = aws_iam_role.codebuild_android_role.arn
  build_timeout = 60 # minutes -- Android SDK install + Gradle build is slow on a fresh environment

  artifacts {
    type      = "S3"
    location  = aws_s3_bucket.mobile_build_artifacts.bucket
    packaging = "ZIP"
    name      = "build-output"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_MEDIUM"
    image                       = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type                        = "LINUX_CONTAINER"
    image_pull_credentials_type = "CODEBUILD"

    environment_variable {
      name  = "SIGNING_SECRET_ID"
      value = aws_secretsmanager_secret.mobile_android_signing.name
    }
    environment_variable {
      name  = "AWS_DEFAULT_REGION"
      value = var.aws_region
    }
  }

  source {
    type            = "GITHUB"
    location        = var.github_repo_url
    buildspec       = "mobile/buildspec-android.yml"
    git_clone_depth = 1
  }

  depends_on = [aws_codebuild_source_credential.github]

  tags = {
    Name = "${var.project_name}-mobile-android"
  }
}

output "mobile_android_codebuild_project" {
  value       = aws_codebuild_project.mobile_android_build.name
  description = "Trigger a build with: aws codebuild start-build --project-name <this>"
}

output "mobile_build_artifacts_bucket" {
  value = aws_s3_bucket.mobile_build_artifacts.bucket
}

output "mobile_android_signing_secret_arn" {
  description = "Populate this secret's value before the first build -- see MOBILE_AWS_BUILD.md"
  value       = aws_secretsmanager_secret.mobile_android_signing.arn
}
