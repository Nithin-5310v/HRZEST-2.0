# The instance's role: SSM Session Manager (so port 22 can close), and exactly
# what the firewall agent needs -- read/update the four WAF IP sets, read the
# two log groups, read WAF metrics. Nothing else.

data "aws_iam_policy_document" "instance_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  count              = var.existing_instance_role_name == "" ? 1 : 0
  name               = "${var.name}-app-host"
  assume_role_policy = data.aws_iam_policy_document.instance_assume.json
}

resource "aws_iam_instance_profile" "instance" {
  count = var.existing_instance_role_name == "" ? 1 : 0
  name  = "${var.name}-app-host"
  role  = aws_iam_role.instance[0].name
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = local.role_name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "agent" {
  statement {
    sid     = "SyncBanListToWaf"
    actions = ["wafv2:GetIPSet", "wafv2:UpdateIPSet"]
    resources = [
      aws_wafv2_ip_set.block_v4.arn, aws_wafv2_ip_set.block_v6.arn,
      aws_wafv2_ip_set.trusted_v4.arn, aws_wafv2_ip_set.trusted_v6.arn,
    ]
  }
  statement {
    sid     = "ReadEdgeDecisions"
    actions = ["logs:FilterLogEvents"]
    resources = [
      aws_cloudwatch_log_group.waf.arn, "${aws_cloudwatch_log_group.waf.arn}:*",
      aws_cloudwatch_log_group.flow.arn, "${aws_cloudwatch_log_group.flow.arn}:*",
    ]
  }
  statement {
    sid       = "ReadEdgeCounts"
    actions   = ["cloudwatch:GetMetricStatistics"]
    resources = ["*"] # this action has no resource-level permissions
  }
}

resource "aws_iam_role_policy" "agent" {
  name   = "${var.name}-firewall-agent"
  role   = local.role_name
  policy = data.aws_iam_policy_document.agent.json
}
