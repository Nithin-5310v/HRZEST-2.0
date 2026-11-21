# Least privilege, chained by security-group reference:
#   alb  <- 80/443 from anywhere;   -> 80/443 to app only
#   app  <- 80/443 from alb only (+ SSH from ssh_cidrs, + decoys if kept)
#   db   <- 5432 from app only      (only when PostgreSQL moves to RDS)

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "Load balancer: HTTP/HTTPS from the internet, to the app instance only"
  vpc_id      = local.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each          = toset(var.ip_address_type == "dualstack" ? ["0.0.0.0/0", "::/0"] : ["0.0.0.0/0"])
  security_group_id = aws_security_group.alb.id
  description       = "HTTP (redirected to HTTPS; ACME challenges)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  for_each          = toset(var.ip_address_type == "dualstack" ? ["0.0.0.0/0", "::/0"] : ["0.0.0.0/0"])
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  for_each                     = toset(["80", "443"])
  security_group_id            = aws_security_group.alb.id
  description                  = "To the app instance only"
  ip_protocol                  = "tcp"
  from_port                    = tonumber(each.value)
  to_port                      = tonumber(each.value)
  referenced_security_group_id = aws_security_group.app.id
}

resource "aws_security_group" "app" {
  name        = "${var.name}-app"
  description = "App instance: web from the load balancer only; no direct internet ingress"
  vpc_id      = local.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  for_each                     = toset(["80", "443"])
  security_group_id            = aws_security_group.app.id
  description                  = "From the load balancer"
  ip_protocol                  = "tcp"
  from_port                    = tonumber(each.value)
  to_port                      = tonumber(each.value)
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_ingress_rule" "app_ssh" {
  for_each          = toset(var.ssh_cidrs)
  security_group_id = aws_security_group.app.id
  description       = "SSH from an admin address"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = strcontains(each.value, ":") ? null : each.value
  cidr_ipv6         = strcontains(each.value, ":") ? each.value : null
}

resource "aws_vpc_security_group_ingress_rule" "app_decoys" {
  for_each          = toset([for p in var.decoy_ports : tostring(p)])
  security_group_id = aws_security_group.app.id
  description       = "Honeypot decoy port (utils/honeypot.py)"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  cidr_ipv4         = "0.0.0.0/0"
}

# Outbound: the app calls Microsoft Graph, Razorpay, the Anthropic API, Have I
# Been Pwned, AWS APIs (SSM, CloudWatch, WAF) and apt mirrors -- all HTTPS --
# plus DNS/NTP from the VPC resolver. The host firewall can narrow this
# further (harden_host_firewall.sh --egress restricted).
resource "aws_vpc_security_group_egress_rule" "app_out" {
  security_group_id = aws_security_group.app.id
  description       = "Outbound (see the comment in security_groups.tf)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_security_group" "db" {
  count       = var.create_db_security_group ? 1 : 0
  name        = "${var.name}-db"
  description = "PostgreSQL: from the app instance only; no public access"
  vpc_id      = local.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  count                        = var.create_db_security_group ? 1 : 0
  security_group_id            = aws_security_group.db[0].id
  description                  = "PostgreSQL from the app instance"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.app.id
}
