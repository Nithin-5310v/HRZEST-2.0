# ── certificate ──────────────────────────────────────────────────────────────
resource "aws_acm_certificate" "this" {
  count                     = local.create_cert ? 1 : 0
  domain_name               = var.domain
  subject_alternative_names = ["www.${var.domain}"]
  validation_method         = "DNS"
  key_algorithm             = "EC_prime256v1"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = local.create_cert && var.route53_zone_id != "" ? {
    for o in aws_acm_certificate.this[0].domain_validation_options : o.domain_name => o
  } : {}
  zone_id         = var.route53_zone_id
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  records         = [each.value.resource_record_value]
  ttl             = 300
  allow_overwrite = true
}

# Without a Route 53 zone this waits (up to its timeout) until the CNAMEs in
# the output `certificate_validation_records` exist at the DNS host.
resource "aws_acm_certificate_validation" "this" {
  count                   = local.create_cert ? 1 : 0
  certificate_arn         = aws_acm_certificate.this[0].arn
  validation_record_fqdns = var.route53_zone_id != "" ? [for r in aws_route53_record.cert_validation : r.fqdn] : null
  timeouts {
    create = "2h"
  }
}

# ── load balancer ────────────────────────────────────────────────────────────
resource "aws_lb" "this" {
  name                       = "${var.name}-edge"
  load_balancer_type         = "application"
  internal                   = false
  ip_address_type            = var.ip_address_type
  subnets                    = var.public_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  idle_timeout               = 120
  enable_deletion_protection = var.enable_deletion_protection
  enable_http2               = true
}

# nginx keeps terminating TLS on the instance (its Let's Encrypt certificate
# stays valid for direct access and rollback); the load balancer does not
# verify the target's certificate, so this hop needs no certificate change.
resource "aws_lb_target_group" "https" {
  name                 = "${var.name}-https"
  vpc_id               = local.vpc_id
  target_type          = "instance"
  port                 = 443
  protocol             = "HTTPS"
  protocol_version     = "HTTP1"
  deregistration_delay = 30
  health_check {
    protocol            = "HTTPS"
    path                = "/healthz"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# Port 80 on the instance: only ACME HTTP-01 challenges, so certbot renewals
# keep working after DNS points at the load balancer.
resource "aws_lb_target_group" "acme" {
  name                 = "${var.name}-acme"
  vpc_id               = local.vpc_id
  target_type          = "instance"
  port                 = 80
  protocol             = "HTTP"
  deregistration_delay = 10
  health_check {
    protocol = "HTTP"
    path     = "/.well-known/acme-challenge/"
    matcher  = "200-404"
  }
}

resource "aws_lb_target_group_attachment" "https" {
  target_group_arn = aws_lb_target_group.https.arn
  target_id        = var.instance_id
  port             = 443
}

resource "aws_lb_target_group_attachment" "acme" {
  target_group_arn = aws_lb_target_group.acme.arn
  target_id        = var.instance_id
  port             = 80
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      protocol    = "HTTPS"
      port        = "443"
      host        = var.domain
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener_rule" "acme" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.acme.arn
  }
  condition {
    path_pattern {
      values = ["/.well-known/acme-challenge/*"]
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  # TLS 1.3 + TLS 1.2 with forward-secret AEAD suites only.
  ssl_policy      = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
  certificate_arn = local.cert_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.https.arn
  }
}

# ── DNS (the cutover; off by default) ────────────────────────────────────────
resource "aws_route53_record" "alias" {
  for_each = var.manage_dns_records && var.route53_zone_id != "" ? toset(flatten([
    for d in local.domains : var.ip_address_type == "dualstack" ? ["${d}|A", "${d}|AAAA"] : ["${d}|A"]
  ])) : toset([])
  zone_id = var.route53_zone_id
  name    = split("|", each.value)[0]
  type    = split("|", each.value)[1]
  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}
