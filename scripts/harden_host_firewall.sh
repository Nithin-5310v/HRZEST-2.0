#!/usr/bin/env bash
# Host firewall layer (docs/FIREWALL.md, layer 2) for the production EC2 host:
# Ubuntu 24.04, bare metal -- distro nginx on 80/443, gunicorn on 127.0.0.1:8000,
# PostgreSQL and Redis on the same machine.
#
#   sudo bash scripts/harden_host_firewall.sh [options]
#
#   --mode direct|edge     direct (default): 80/443 open to the internet, as now.
#                          edge: the AWS load balancer is in front
#                          (infra/terraform/edge); 80/443 only from --web-from.
#   --web-from CIDRS       edge mode: the VPC range(s) the load balancer uses
#   --ssh-from CIDRS       allow SSH only from these ranges. Default: SSH stays
#                          reachable (rate-limited) and the EC2 security group,
#                          which already allows only the admin's address, decides.
#   --no-ssh               close port 22 entirely (use AWS SSM Session Manager).
#                          Refused when run over SSH unless --i-am-on-ssm.
#   --egress open|restricted
#                          open (default): any outbound connection.
#                          restricted: outbound only to DNS, NTP, DHCP, HTTP(S)
#                          and mail submission (587/465), plus --egress-allow.
#                          Blocks the usual reverse-shell and exfiltration ports.
#   --egress-allow LIST    restricted mode extras, e.g. 3100/tcp,5432/tcp
#   --log-rate N           at most N drop-log lines a minute (default 120)
#   --docker-guard         install the DOCKER-USER guard even if Docker is not
#                          installed (it is added automatically when it is)
#   --dry-run              print what would change, change nothing
#
# What it does
#   1. ufw: deny incoming, deny routed, outgoing per --egress; allow only HTTP,
#      HTTPS and SSH (as configured). Its own rules carry the comment
#      "hrzest-fw:", and re-running converges them: a rule this script added
#      earlier and no longer wants is removed. Rules it did not add are left
#      alone and listed at the end for you to review.
#   2. Every dropped packet is logged, rate-limited, as [FW-DROP] (inbound),
#      [FW-DROP-FWD] (routed) and, in restricted mode, [FW-DROP-OUT], into the
#      kernel log and /var/log/ufw.log -- which the firewall agent ships to
#      the SOC console.
#   3. An nftables table (inet hrzest_fw) with two address sets that the
#      firewall agent fills from the SOC console's ban list: a ban made in the
#      console is dropped here within seconds ([FW-DROP-BL]).
#   4. Docker, if present: a DOCKER-USER chain so containers publishing ports
#      cannot bypass these rules (Docker's own NAT rules skip ufw otherwise).
#   5. Kernel network hardening (sysctl): SYN cookies, reverse-path filtering,
#      no ICMP redirects or source routing, martian packets logged.
#
# Safe to re-run. It never removes the rule that lets the current SSH session
# in before the replacement is active, and ufw keeps established connections.
set -euo pipefail

MODE=direct
WEB_FROM=""
SSH_FROM=""
NO_SSH=0
ON_SSM=0
EGRESS=open
EGRESS_ALLOW=""
LOG_RATE=120
DOCKER_GUARD=0
DRY=0
TAG="hrzest-fw"

while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --web-from) WEB_FROM="$2"; shift 2 ;;
    --ssh-from) SSH_FROM="$2"; shift 2 ;;
    --no-ssh) NO_SSH=1; shift ;;
    --i-am-on-ssm) ON_SSM=1; shift ;;
    --egress) EGRESS="$2"; shift 2 ;;
    --egress-allow) EGRESS_ALLOW="$2"; shift 2 ;;
    --log-rate) LOG_RATE="$2"; shift 2 ;;
    --docker-guard) DOCKER_GUARD=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,49p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "== $*"; }
run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

cidr_ok() { [[ "$1" =~ ^[0-9a-fA-F:.]+/[0-9]{1,3}$ ]]; }
split_csv() { local IFS=','; read -r -a _out <<< "$1"; printf '%s\n' "${_out[@]}"; }

case "$MODE" in direct|edge) ;; *) die "--mode must be direct or edge" ;; esac
case "$EGRESS" in open|restricted) ;; *) die "--egress must be open or restricted" ;; esac
[[ "$LOG_RATE" =~ ^[0-9]+$ ]] && [ "$LOG_RATE" -ge 1 ] || die "--log-rate must be a positive number"
if [ "$MODE" = edge ]; then
  [ -n "$WEB_FROM" ] || die "--mode edge needs --web-from (the VPC CIDR the load balancer connects from)"
fi
for c in $(split_csv "$WEB_FROM") $(split_csv "$SSH_FROM"); do cidr_ok "$c" || die "not a CIDR: $c"; done
for p in $(split_csv "$EGRESS_ALLOW"); do [[ "$p" =~ ^[0-9]{1,5}/(tcp|udp)$ ]] || die "--egress-allow entries look like 3100/tcp: $p"; done
[ "$NO_SSH" = 0 ] || [ -z "$SSH_FROM" ] || die "--no-ssh and --ssh-from contradict each other"
[ "$DRY" = 1 ] || [ "$(id -u)" = 0 ] || die "run as root (or with --dry-run)"

# ── lock-out guards ──────────────────────────────────────────────────────────
SSH_CLIENT_IP="${SSH_CONNECTION:-}"
SSH_CLIENT_IP="${SSH_CLIENT_IP%% *}"
if [ -n "$SSH_CLIENT_IP" ]; then
  if [ "$NO_SSH" = 1 ] && [ "$ON_SSM" = 0 ]; then
    die "you are connected over SSH ($SSH_CLIENT_IP) and --no-ssh closes port 22. Connect with SSM Session Manager first, then re-run with --no-ssh --i-am-on-ssm."
  fi
  if [ -n "$SSH_FROM" ]; then
    inside=$(python3 - "$SSH_CLIENT_IP" "$SSH_FROM" <<'EOF'
import ipaddress, sys
ip = ipaddress.ip_address(sys.argv[1])
print(int(any(ip in ipaddress.ip_network(c.strip(), strict=False) for c in sys.argv[2].split(",") if c.strip())))
EOF
)
    [ "$inside" = 1 ] || die "your SSH session comes from $SSH_CLIENT_IP, which is not in --ssh-from $SSH_FROM: you would be locked out."
  fi
fi

BEFORE=/etc/ufw/before.rules
if [ -f "$BEFORE" ] && grep -qF "# BEGIN hrzest port redirects" "$BEFORE"; then
  die "$BEFORE contains the container stack's NAT redirects (80->8080, 443->8443) from scripts/host_hardening.sh. On this bare-metal host nothing listens on 8080/8443, so they would break the site. Remove the block between '# BEGIN hrzest port redirects' and '# END hrzest port redirects' first."
fi

# ── packages ─────────────────────────────────────────────────────────────────
say "packages"
for p in ufw nftables rsyslog; do
  if ! dpkg -s "$p" >/dev/null 2>&1; then run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$p"; fi
done

# ── 1. ufw policy and rules ──────────────────────────────────────────────────
say "ufw policy (incoming deny, routed deny, outgoing $([ "$EGRESS" = open ] && echo allow || echo deny))"
run ufw --force default deny incoming
run ufw --force default deny routed
if [ "$EGRESS" = open ]; then run ufw --force default allow outgoing; else run ufw --force default deny outgoing; fi
# Logging is done by this script's own rate-limited [FW-DROP] rules (below);
# ufw's [UFW BLOCK] lines would only duplicate them.
run ufw logging off

# The rules this script wants, as `ufw show added` prints them (without the comment).
desired=()
if [ "$MODE" = direct ]; then
  desired+=("allow 80/tcp|http")
  desired+=("allow 443/tcp|https")
else
  for c in $(split_csv "$WEB_FROM"); do
    desired+=("allow from $c to any port 80,443 proto tcp|web from load balancer")
  done
fi
if [ "$NO_SSH" = 0 ]; then
  if [ -n "$SSH_FROM" ]; then
    for c in $(split_csv "$SSH_FROM"); do desired+=("allow from $c to any port 22 proto tcp|ssh"); done
  else
    desired+=("limit 22/tcp|ssh (rate-limited; the security group decides who)")
  fi
fi
if [ "$EGRESS" = restricted ]; then
  desired+=("allow out 53|dns")
  desired+=("allow out 123/udp|ntp")
  desired+=("allow out 67/udp|dhcp")
  desired+=("allow out 80/tcp|http out (apt, instance metadata)")
  desired+=("allow out 443/tcp|https out (apt, APIs, SSM, CloudWatch)")
  desired+=("allow out 587/tcp|mail submission")
  desired+=("allow out 465/tcp|mail submission (tls)")
  for p in $(split_csv "$EGRESS_ALLOW"); do desired+=("allow out $p|operator egress"); done
fi

current=()
if command -v ufw >/dev/null 2>&1; then
  while IFS= read -r line; do
    case "$line" in
      "ufw "*"comment '$TAG:"*) rule="${line#ufw }"; current+=("${rule% comment *}") ;;
    esac
  done < <(ufw show added 2>/dev/null || true)
fi

in_list() { local needle="$1"; shift; local x; for x in "$@"; do [ "$x" = "$needle" ] && return 0; done; return 1; }
want_rules=(); for d in "${desired[@]}"; do want_rules+=("${d%%|*}"); done

say "ufw rules"
for d in "${desired[@]}"; do
  rule="${d%%|*}" note="${d#*|}"
  if in_list "$rule" "${current[@]+"${current[@]}"}"; then
    echo "keep    $rule"
  else
    echo "add     $rule"
    # shellcheck disable=SC2086 # the rule is a list of ufw words on purpose
    run ufw $rule comment "$TAG: $note" >/dev/null
  fi
done
for rule in "${current[@]+"${current[@]}"}"; do
  if ! in_list "$rule" "${want_rules[@]}"; then
    echo "remove  $rule"
    # shellcheck disable=SC2086
    run ufw delete $rule >/dev/null
  fi
done

# ── 2. drop logging ──────────────────────────────────────────────────────────
say "drop logging ([FW-DROP], at most $LOG_RATE lines/min)"
# One backup of the files this run edits, taken before it edits any of them:
# restored below if ufw refuses the result.
RULE_FILES="/etc/ufw/after.rules /etc/ufw/after6.rules /etc/ufw/sysctl.conf"
if [ "$DRY" = 0 ]; then for f in $RULE_FILES; do [ -f "$f" ] && cp -a "$f" "$f.$TAG.bak"; done; fi
BURST=$((LOG_RATE * 2))
put_block() {  # put_block <file> <name> <content>: replace the marked block, or add it before the last COMMIT
  local file="$1" name="$2" content="$3"
  local begin="# BEGIN $TAG $name" end="# END $TAG $name"
  if [ "$DRY" = 1 ]; then echo "[dry-run] $file: $name block"; printf '%s\n' "$content" | sed 's/^/[dry-run]   /'; return; fi
  [ -f "$file" ] || die "$file is missing (is ufw installed?)"
  python3 - "$file" "$begin" "$end" "$content" <<'EOF'
import sys
path, begin, end, content = sys.argv[1:5]
text = open(path).read()
block = f"{begin}\n{content}\n{end}\n"
if begin in text and end in text:
    head, rest = text.split(begin, 1)
    tail = rest.split(end, 1)[1].lstrip("\n")
    text = head + block + tail
else:
    idx = text.rfind("\nCOMMIT")
    if idx < 0:
        sys.exit(f"{path}: no COMMIT line to insert before")
    text = text[:idx + 1] + block + text[idx + 1:]
open(path, "w").write(text)
EOF
}
drop_block() {  # drop_block <file> <name>: remove a block an earlier run added
  local file="$1" name="$2"
  local begin="# BEGIN $TAG $name" end="# END $TAG $name"
  [ -f "$file" ] && grep -qF "$begin" "$file" || return 0
  if [ "$DRY" = 1 ]; then echo "[dry-run] $file: remove $name block"; return; fi
  python3 - "$file" "$begin" "$end" <<'EOF'
import sys
path, begin, end = sys.argv[1:4]
text = open(path).read()
head, rest = text.split(begin, 1)
text = head + rest.split(end, 1)[1].lstrip("\n")
open(path, "w").write(text)
EOF
}
LOG_ARGS="-m limit --limit ${LOG_RATE}/min --limit-burst ${BURST} -j LOG --log-level 4"
v4="-A ufw-after-input $LOG_ARGS --log-prefix \"[FW-DROP] \"
-A ufw-after-forward $LOG_ARGS --log-prefix \"[FW-DROP-FWD] \""
v6="-A ufw6-after-input $LOG_ARGS --log-prefix \"[FW-DROP] \"
-A ufw6-after-forward $LOG_ARGS --log-prefix \"[FW-DROP-FWD] \""
if [ "$EGRESS" = restricted ]; then
  # Only with a deny-outgoing policy: under "allow outgoing" every packet that
  # reaches this chain is about to be accepted, and would be logged as a drop.
  v4="$v4
-A ufw-after-output $LOG_ARGS --log-prefix \"[FW-DROP-OUT] \""
  v6="$v6
-A ufw6-after-output $LOG_ARGS --log-prefix \"[FW-DROP-OUT] \""
fi
put_block /etc/ufw/after.rules logging "$v4"
put_block /etc/ufw/after6.rules logging "$v6"

RSYSLOG=/etc/rsyslog.d/19-hrzest-fw.conf
RSYSLOG_CONTENT='# Installed by scripts/harden_host_firewall.sh: [FW-DROP*] lines go to
# /var/log/ufw.log (read by the firewall agent and fail2ban) and nowhere else.
:msg, contains, "[FW-DROP" /var/log/ufw.log
& stop'
if [ "$DRY" = 1 ]; then echo "[dry-run] write $RSYSLOG"
else printf '%s\n' "$RSYSLOG_CONTENT" > "$RSYSLOG"; touch /var/log/ufw.log; chown syslog:adm /var/log/ufw.log 2>/dev/null || true; chmod 0640 /var/log/ufw.log
  if [ -d /run/systemd/system ]; then systemctl restart rsyslog 2>/dev/null || true; fi
fi

# ── 4. Docker cannot publish around the firewall ─────────────────────────────
if [ "$DOCKER_GUARD" = 1 ] || command -v dockerd >/dev/null 2>&1 || [ -e /etc/docker ]; then
  say "DOCKER-USER guard"
  # Docker's NAT rules send published-port traffic through FORWARD, past
  # ufw's INPUT rules. DOCKER-USER runs first: only replies, private-network
  # traffic (container to container, the load balancer) and what a
  # `ufw route allow` rule permits get through; everything else new is dropped.
  # ufw loads after.rules before user.rules, so ufw-user-forward is declared
  # here too; user.rules then fills it (the approach of the ufw-docker project).
  docker_rules=":ufw-user-forward - [0:0]
:DOCKER-USER - [0:0]
-A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DOCKER-USER -s 10.0.0.0/8 -j RETURN
-A DOCKER-USER -s 172.16.0.0/12 -j RETURN
-A DOCKER-USER -s 192.168.0.0/16 -j RETURN
-A DOCKER-USER -j ufw-user-forward
-A DOCKER-USER -m conntrack --ctstate NEW $LOG_ARGS --log-prefix \"[FW-DROP-DOCKER] \"
-A DOCKER-USER -m conntrack --ctstate NEW -j DROP
-A DOCKER-USER -j RETURN"
  put_block /etc/ufw/after.rules docker "$docker_rules"
else
  echo "Docker not installed: no DOCKER-USER guard needed (use --docker-guard to add it anyway)"
  drop_block /etc/ufw/after.rules docker
fi

# ── 5. kernel network hardening ──────────────────────────────────────────────
say "sysctl network hardening"
SYSCTL_LINES="net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_max_syn_backlog = 4096
net.ipv4.tcp_synack_retries = 2
net.ipv4.tcp_rfc1337 = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
net.ipv4.conf.all.log_martians = 1
net.ipv4.conf.default.log_martians = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1"
SYSCTL_FILE=/etc/sysctl.d/60-hrzest-net-hardening.conf
if [ "$DRY" = 1 ]; then
  echo "[dry-run] write $SYSCTL_FILE and the same values at the end of /etc/ufw/sysctl.conf"
else
  { echo "# Installed by scripts/harden_host_firewall.sh"; printf '%s\n' "$SYSCTL_LINES"; } > "$SYSCTL_FILE"
  sysctl -q -p "$SYSCTL_FILE" || echo "WARNING: some sysctl values could not be applied now (they apply at boot)" >&2
  # ufw re-applies /etc/ufw/sysctl.conf every time it starts, which would
  # undo some of these (it sets log_martians=0). Its file uses '/' paths.
  if [ -f /etc/ufw/sysctl.conf ]; then
    put_block_plain() {
      python3 - /etc/ufw/sysctl.conf "# BEGIN $TAG" "# END $TAG" "$1" <<'EOF'
import sys
path, begin, end, content = sys.argv[1:5]
text = open(path).read()
if begin in text and end in text:
    text = text.split(begin, 1)[0].rstrip("\n") + "\n" + text.split(end, 1)[1].lstrip("\n")
text = text.rstrip("\n") + f"\n{begin}\n{content}\n{end}\n"
open(path, "w").write(text)
EOF
    }
    put_block_plain "$(printf '%s\n' "$SYSCTL_LINES" | sed -e 's/ = /=/' -e 's#\.#/#g')"
  fi
fi

# ── 3. the ban-list table the firewall agent fills ───────────────────────────
say "nftables ban list (inet hrzest_fw, filled by the firewall agent)"
NFT_FILE=/etc/hrzest-fw/blocklist.nft
NFT_RULES="# Installed by scripts/harden_host_firewall.sh. Loaded by it and by the
# firewall agent (which also fills the sets from the SOC console's ban list).
# Recreating the table empties the sets; the agent refills them within seconds.
table inet hrzest_fw
delete table inet hrzest_fw
table inet hrzest_fw {
    set block4 { type ipv4_addr; flags interval; auto-merge; }
    set block6 { type ipv6_addr; flags interval; auto-merge; }
    chain input {
        type filter hook input priority -10; policy accept;
        ip saddr @block4 limit rate ${LOG_RATE}/minute log prefix \"[FW-DROP-BL] \" level warn
        ip saddr @block4 counter drop
        ip6 saddr @block6 limit rate ${LOG_RATE}/minute log prefix \"[FW-DROP-BL] \" level warn
        ip6 saddr @block6 counter drop
    }
}"
if [ "$DRY" = 1 ]; then
  echo "[dry-run] write $NFT_FILE and load it if the table is missing"
else
  install -d -m 0755 /etc/hrzest-fw
  printf '%s\n' "$NFT_RULES" > "$NFT_FILE"
  nft -f "$NFT_FILE"
fi

# ── enable and report ────────────────────────────────────────────────────────
say "enable"
if [ "$DRY" = 1 ]; then
  echo "[dry-run] ufw --force enable && ufw reload"
elif ! { ufw --force enable >/dev/null && ufw reload >/dev/null; } 2>/tmp/hrzest-fw-ufw.err; then
  cat /tmp/hrzest-fw-ufw.err >&2
  for f in $RULE_FILES; do [ -f "$f.$TAG.bak" ] && cp -a "$f.$TAG.bak" "$f"; done
  ufw --force enable >/dev/null 2>&1 || true
  die "ufw refused the new rules; the previous after.rules / after6.rules / sysctl.conf were put back"
fi

if [ "$DRY" = 0 ]; then
  say "result"
  ufw status verbose | sed -n '1,6p'
  echo
  echo "Rules not added by this script (review them; they stay as they are):"
  ufw show added 2>/dev/null | grep '^ufw ' | grep -v "comment '$TAG:" | sed 's/^/    /' || true
  echo
  echo "Listening on a non-loopback address (now reachable only if a rule above allows it):"
  ss -H -ltnu 2>/dev/null | awk '{print $1, $5}' | grep -vE ' (127\.|\[::1\]|::1)' | sed 's/^/    /' || true
fi
echo "done."
