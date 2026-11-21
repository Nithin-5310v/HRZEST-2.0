# ── where ────────────────────────────────────────────────────────────────────
variable "region" {
  description = "AWS region of the existing EC2 host (production: ap-south-2, Hyderabad)."
  type        = string
  default     = "ap-south-2"
}

variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "hrzest"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.name))
    error_message = "Lowercase letters, digits and '-', 2-21 characters."
  }
}

variable "instance_id" {
  description = "The existing EC2 instance that runs nginx + the app. Read only: this module never changes the instance."
  type        = string
}

variable "public_subnet_ids" {
  description = "At least two public subnets, in different availability zones, of the instance's VPC (the load balancer needs two)."
  type        = list(string)
  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "An application load balancer needs subnets in at least two availability zones."
  }
}

variable "ip_address_type" {
  description = "ipv4, or dualstack when the VPC and subnets have IPv6."
  type        = string
  default     = "ipv4"
  validation {
    condition     = contains(["ipv4", "dualstack"], var.ip_address_type)
    error_message = "ipv4 or dualstack."
  }
}

# ── names and certificate ────────────────────────────────────────────────────
variable "domain" {
  description = "Apex domain served (hrzest.com); www.<domain> is covered as well."
  type        = string
}

variable "certificate_arn" {
  description = "An existing ACM certificate (in var.region) for domain and www.domain. Empty: one is requested here, validated by DNS."
  type        = string
  default     = ""
}

variable "route53_zone_id" {
  description = "Route 53 hosted zone of the domain. Set it to have the certificate validated automatically; empty means the validation records are printed (outputs) to add by hand at the DNS host (Hostinger today)."
  type        = string
  default     = ""
}

variable "manage_dns_records" {
  description = "Point domain and www at the load balancer (Route 53 alias records). This IS the cutover: leave false until the runbook step that says to switch."
  type        = bool
  default     = false
}

# ── security groups ──────────────────────────────────────────────────────────
variable "ssh_cidrs" {
  description = "Addresses allowed to SSH to the instance (/32 each). Empty: no SSH at all; use SSM Session Manager (the instance role allows it)."
  type        = list(string)
  default     = []
}

variable "decoy_ports" {
  description = "TCP ports left open to the internet on the instance for the honeypot decoys (utils/honeypot.py), e.g. [21, 23, 25, 1433, 3306, 3389]. Empty closes them: behind the edge nothing else reaches the instance directly."
  type        = list(number)
  default     = []
}

variable "create_db_security_group" {
  description = "Create a database security group (5432 from the app only) for when PostgreSQL moves to RDS. Today it runs on the instance, reachable on loopback only."
  type        = bool
  default     = false
}

# ── WAF ──────────────────────────────────────────────────────────────────────
variable "managed_rules_action" {
  description = "block: the AWS managed rule groups refuse what they match. count: they only record it (WAF logs, SOC Firewall view). Run count for the first week after the cutover, then switch to block."
  type        = string
  default     = "block"
  validation {
    condition     = contains(["block", "count"], var.managed_rules_action)
    error_message = "block or count."
  }
}

variable "login_rate_limit" {
  description = "Requests to the sign-in endpoints per client address per 5 minutes before the edge blocks it. Customers share office addresses: sized for a shift-start rush."
  type        = number
  default     = 300
}

variable "payroll_rate_limit" {
  description = "Requests to payroll endpoints per client address per 5 minutes."
  type        = number
  default     = 600
}

variable "global_rate_limit" {
  description = "Requests of any kind per client address per 5 minutes."
  type        = number
  default     = 6000
}

variable "geo_mode" {
  description = "off, allow_only (refuse every country not in geo_countries) or block (refuse those in geo_countries). Trusted addresses are exempt."
  type        = string
  default     = "off"
  validation {
    condition     = contains(["off", "allow_only", "block"], var.geo_mode)
    error_message = "off, allow_only or block."
  }
}

variable "geo_countries" {
  description = "ISO 3166-1 alpha-2 country codes for geo_mode, e.g. [\"IN\"]."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "CloudWatch retention for the WAF and VPC flow logs."
  type        = number
  default     = 30
}

variable "flow_log_traffic_type" {
  description = "VPC flow logs: ALL, REJECT or ACCEPT. The SOC console reads the REJECT records; ALL keeps the accepted ones for investigations too."
  type        = string
  default     = "ALL"
}

# ── the instance role ────────────────────────────────────────────────────────
variable "existing_instance_role_name" {
  description = "If the instance already has an IAM role, its name: the agent and SSM policies are attached to it. Empty: a new role and instance profile are created (attach it with the command in the outputs)."
  type        = string
  default     = ""
}

variable "enable_deletion_protection" {
  description = "Protect the load balancer from deletion."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
