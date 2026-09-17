variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Short name used to prefix/tag all resources"
  type        = string
  default     = "employee-attendance"
}

variable "vpc_id" {
  description = "VPC that the existing EC2 instance lives in"
  type        = string
}

variable "subnet_ids" {
  description = "At least two subnet IDs (different AZs) in that VPC, for the RDS subnet group"
  type        = list(string)
}

variable "ec2_security_group_id" {
  description = "Security group ID attached to the existing EC2 instance — RDS access is restricted to this SG only"
  type        = string
}

variable "trusted_admin_cidrs" {
  description = <<-EOT
    CIDR blocks allowed to reach management/filtered ports (SSH, RDP, direct
    DB access, alternate HTTP ports) on the app server — e.g. your office IP,
    home IP, or VPN range as "x.x.x.x/32" entries. Intentionally has no
    default: a filtered port open to 0.0.0.0/0 isn't filtered at all.
  EOT
  type        = list(string)
}

variable "db_name" {
  description = "Initial database name"
  type        = string
  default     = "employee_attendance"
}

variable "db_username" {
  description = "Master username for RDS"
  type        = string
  default     = "attendance_admin"
}

variable "db_password" {
  description = "Master password for RDS (do not commit a real value — pass via TF_VAR_db_password or terraform.tfvars which is gitignored)"
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS instance size"
  type        = string
  default     = "db.t3.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GB"
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = "Automated RDS backup retention window"
  type        = number
  default     = 7
}

variable "alert_email" {
  description = "Email address to receive CloudWatch alarm notifications"
  type        = string
}

variable "ec2_instance_id" {
  description = "Instance ID of the existing EC2 instance, used to scope CloudWatch alarms and the DLM backup policy"
  type        = string
}
