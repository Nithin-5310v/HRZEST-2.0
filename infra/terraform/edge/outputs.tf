output "alb_dns_name" {
  description = "Point the domain here at the cutover (an alias record; Route 53 can alias the apex, Hostinger cannot)."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  value = aws_lb.this.zone_id
}

output "certificate_validation_records" {
  description = "CNAMEs to add at the DNS host when the certificate is requested here without a Route 53 zone."
  value = local.create_cert ? [
    for o in aws_acm_certificate.this[0].domain_validation_options : {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  ] : []
}

output "security_group_ids" {
  value = {
    alb = aws_security_group.alb.id
    app = aws_security_group.app.id
    db  = var.create_db_security_group ? aws_security_group.db[0].id : null
  }
}

output "web_acl_arn" {
  value = aws_wafv2_web_acl.this.arn
}

output "vpc_cidrs" {
  description = "The ranges the load balancer connects from: --web-from for harden_host_firewall.sh, --real-ip-from for install_nginx_waf.sh."
  value       = join(",", local.vpc_cidrs)
}

output "runbook_commands" {
  description = "The cutover steps that change the running host, in order (README.md explains each)."
  value       = <<-EOT
    # 1. Instance role (SSM + firewall agent):
    ${var.existing_instance_role_name == "" ? "aws ec2 associate-iam-instance-profile --region ${var.region} --instance-id ${var.instance_id} --iam-instance-profile Name=${aws_iam_instance_profile.instance[0].name}" : "# existing role ${var.existing_instance_role_name}: policies attached by Terraform"}

    # 2. Add the app security group NEXT TO the current ones (keeps direct access during the test):
    aws ec2 modify-instance-attribute --region ${var.region} --instance-id ${var.instance_id} --groups ${aws_security_group.app.id} ${join(" ", data.aws_instance.app.vpc_security_group_ids)}

    # 3. On the host: real client address from the load balancer, and the agent's AWS settings:
    sudo bash scripts/host-hardening/install_nginx_waf.sh --domain ${var.domain} --real-ip-from ${join(",", local.vpc_cidrs)}

    # 4. After the DNS switch and its TTL: the app security group ALONE, then the host firewall in edge mode:
    aws ec2 modify-instance-attribute --region ${var.region} --instance-id ${var.instance_id} --groups ${aws_security_group.app.id}
    sudo bash scripts/harden_host_firewall.sh --mode edge --web-from ${join(",", local.vpc_cidrs)}${length(var.ssh_cidrs) > 0 ? " --ssh-from ${join(",", var.ssh_cidrs)}" : " --no-ssh --i-am-on-ssm"}
  EOT
}

output "agent_env" {
  description = "Lines for /etc/hrzest/firewall-agent.env on the host (then: sudo systemctl restart hrzest-fw-agent)."
  value       = <<-EOT
    HRZEST_FW_AWS_REGION=${var.region}
    HRZEST_FW_WAF_SCOPE=REGIONAL
    HRZEST_FW_WAF_BLOCK_V4=${aws_wafv2_ip_set.block_v4.name}/${aws_wafv2_ip_set.block_v4.id}
    HRZEST_FW_WAF_BLOCK_V6=${aws_wafv2_ip_set.block_v6.name}/${aws_wafv2_ip_set.block_v6.id}
    HRZEST_FW_WAF_TRUSTED_V4=${aws_wafv2_ip_set.trusted_v4.name}/${aws_wafv2_ip_set.trusted_v4.id}
    HRZEST_FW_WAF_TRUSTED_V6=${aws_wafv2_ip_set.trusted_v6.name}/${aws_wafv2_ip_set.trusted_v6.id}
    HRZEST_FW_WAF_LOG_GROUP=${aws_cloudwatch_log_group.waf.name}
    HRZEST_FW_FLOW_LOG_GROUP=${aws_cloudwatch_log_group.flow.name}
    HRZEST_FW_WAF_ACL_NAME=${aws_wafv2_web_acl.this.name}
  EOT
}
