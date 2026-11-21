# AWS edge (firewall layer 1)

An Application Load Balancer and a WAFv2 web ACL in front of the existing EC2 host, with
least-privilege security groups, VPC flow logs, WAF logs and the instance role the firewall agent
uses. Overview and the other layers: [docs/FIREWALL.md](../../../docs/FIREWALL.md).

```text
internet ─▶ ALB :443 (ACM certificate, TLS 1.2/1.3) + WAF ─▶ instance :443 nginx (ModSecurity) ─▶ gunicorn
         ─▶ ALB :80 ─▶ 301 to https, except /.well-known/acme-challenge/* ─▶ instance :80 (certbot keeps renewing)
```

**Not applied yet.** `terraform apply` creates new resources only and changes nothing about the
running site; the traffic only moves at step 5, the DNS switch, which is yours to decide.

## Before you start

* **DNS must move to Route 53.** A load balancer has no fixed IP, and the apex `hrzest.com` can only
  point at it with an alias record. Hostinger DNS has none. Route 53 also gives DNSSEC (OBS-03).
  Copy every record across (A, www, MX, SPF/DMARC TXT, both DKIM CNAMEs, CAA) before switching
  nameservers.
* **CAA**: add `0 issue "amazon.com"` beside `letsencrypt.org` so ACM can issue the certificate (OBS-04).
* **Cost** (estimate, ap-south-2, at today's traffic): about US$35-50 a month: the load balancer
  (~US$16 + capacity units), the web ACL (US$5 + US$1 per rule and managed group, ~11) + US$0.60 per
  million requests, and CloudWatch log ingestion and storage. Check the AWS pricing pages.
* Credentials: run Terraform as an IAM user/role that can manage ELB, WAFv2, ACM, EC2 security groups,
  IAM roles, CloudWatch Logs and (with a zone) Route 53. Keep the state somewhere safe and private (an
  encrypted S3 backend); `terraform.tfvars` and `*.tfstate` are gitignored.

## Runbook

1. **Configure and apply.**
   ```bash
   cd infra/terraform/edge
   cp terraform.tfvars.example terraform.tfvars    # fill in instance_id, subnets, domain, zone
   terraform init && terraform plan -out tfplan && terraform apply tfplan
   ```
   Keep `managed_rules_action = "count"` for now. Without `route53_zone_id`, add the CNAMEs from
   `terraform output certificate_validation_records` at the DNS host; the apply waits for ACM.
2. **Attach the role and the app security group** (beside the current groups, so nothing changes yet):
   run steps 1 and 2 of `terraform output -raw runbook_commands`.
3. **Real client addresses at nginx** (step 3 of the commands): re-run the nginx installer with
   `--real-ip-from <VPC CIDR>`. Keep the app's `TRUSTED_PROXY_HOPS=1`: nginx resolves the client from
   the load balancer's `X-Forwarded-For` and passes it on last, which is what one hop reads.
4. **Test through the load balancer** before any DNS change:
   ```bash
   curl -sk --resolve hrzest.com:443:$(dig +short $(terraform output -raw alb_dns_name) | head -1) https://hrzest.com/healthz
   ```
   Sign in, check in, upload a document, open payroll, all through that address. Then add the agent's
   AWS settings: `terraform output -raw agent_env >> /etc/hrzest/firewall-agent.env` (as root),
   `sudo apt-get install python3-boto3`, `sudo systemctl restart hrzest-fw-agent`. The SOC Firewall
   view's Edge WAF tile should show counts within a few minutes.
5. **Switch DNS** (Route 53): `manage_dns_records = true`, apply. Or point the records at the load
   balancer yourself. Lower the old records' TTL a day before.
6. **After the old TTL has passed**: step 4 of the commands, which leaves only the app security group
   on the instance (nothing but the load balancer reaches it) and switches the host firewall to edge mode.
7. **A week later**: if the Firewall view's Edge WAF counts show no legitimate traffic matching,
   `managed_rules_action = "block"` and apply.

## Rollback

* Before step 5: `terraform destroy` (with `enable_deletion_protection = false` first). Nothing
  depended on it.
* After step 5: point DNS back at the instance's Elastic IP, put the original security group back
  (`aws ec2 modify-instance-attribute --groups <old sg>`), and re-run
  `scripts/harden_host_firewall.sh` without `--mode edge`. nginx keeps its Let's Encrypt certificate
  throughout (renewals keep working through the load balancer), so direct traffic works immediately.

## What it does not do

* It doesn't manage the instance, RDS or DNS records unless told to (`manage_dns_records`), and never
  changes the instance's security groups or role itself: those steps are the commands above.
* The IP sets' contents belong to the firewall agent (the SOC console's ban and trusted lists);
  Terraform ignores them after creation, so an apply never wipes a ban.
* The app has no token-refresh endpoint to rate-limit separately: tokens come from the sign-in calls,
  which the sign-in rate limit covers on both `/api/...` and `/api/v1/...`.
