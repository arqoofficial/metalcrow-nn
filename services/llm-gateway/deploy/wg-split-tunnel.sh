#!/usr/bin/env bash
# AmnesiaWG (Estonia) SPLIT TUNNEL: RU-destined traffic (incl. Yandex) DIRECT; all else via Estonia.
# immers.cloud is Russian: Yandex works from the RU IP (must stay direct); OpenRouter must tunnel.
#
# Reviewed by Account Manager (SSH-lockout history). SAFETY ORDER — do not reorder:
#   pre-fetch RU zone -> pin control-plane DIRECT -> arm rollback -> tunnel up ->
#   add RU direct routes -> VERIFY control-plane is direct -> operator confirms from a fresh session.
#
# MGMT_CIDR = space-separated IPs/CIDRs to keep DIRECT. MUST include EVERY admin source, e.g.
#   the AM/deploy box egress 195.209.216.45/32 and OSN's client 188.190.8.61/32 (if they SSH direct).
# Usage: sudo ROLLBACK=120 MGMT_CIDR="195.209.216.45/32 188.190.8.61/32" bash wg-split-tunnel.sh [estonia.conf]
set -euo pipefail
CONF="${1:-estonia.conf}"
RU_ZONE_URL="${RU_ZONE_URL:-https://www.ipdeny.com/ipblocks/data/countries/ru.zone}"
ROLLBACK="${ROLLBACK:-120}"

[ -r "$CONF" ] || { echo "conf not found: $CONF" >&2; exit 1; }
command -v awg-quick >/dev/null 2>&1 || { echo "awg-quick (AmnesiaWG) not installed" >&2; exit 1; }

# [AM #1 CRITICAL] Direct pins only beat the tunnel if awg-quick installs the
# `suppress_prefixlength 0` rule — which it does ONLY when the conf has no Table= override.
if grep -qiE '^[[:space:]]*Table[[:space:]]*=' "$CONF"; then
  echo "REFUSING: $CONF sets 'Table=' — disables suppress_prefixlength, so direct pins are IGNORED (SSH-capture risk). Remove the Table= line." >&2
  exit 1
fi

DEFGW=$(ip route show default | awk '/default/{print $3; exit}')
DEFDEV=$(ip route show default | awk '/default/{print $5; exit}')
[ -n "$DEFGW" ] && [ -n "$DEFDEV" ] || { echo "could not determine default route" >&2; exit 1; }
echo "physical default gateway: via $DEFGW dev $DEFDEV"

# [AM #4] Pre-fetch the RU zone BEFORE the tunnel is up (don't tunnel the download / race window).
RU_ZONE=$(mktemp)
curl -fsSL "$RU_ZONE_URL" -o "$RU_ZONE"
echo "pre-fetched RU zone ($(wc -l <"$RU_ZONE") CIDRs)."

# Pin the control plane DIRECT before any tunnel change.
PINS=()
[ -n "${SSH_CONNECTION:-}" ] && PINS+=("$(awk '{print $1"/32"}' <<<"$SSH_CONNECTION")")   # live SSH session
PINS+=("169.254.0.0/16")                                # [AM #5] metadata/link-local direct
for m in ${MGMT_CIDR:-}; do PINS+=("$m"); done          # [AM #3] all admin sources
for p in "${PINS[@]}"; do ip route replace "$p" via "$DEFGW" dev "$DEFDEV" && echo "  pinned DIRECT: $p"; done

# Dead-man rollback: auto-down unless confirmed.
rm -f /tmp/wg_confirmed
( sleep "$ROLLBACK"; [ -f /tmp/wg_confirmed ] || { logger -t wg-split "auto-rollback: down $CONF"; awg-quick down "$CONF" 2>/dev/null; } ) &
echo "dead-man rollback armed (${ROLLBACK}s)."

# Tunnel up (full tunnel by the conf's AllowedIPs=0.0.0.0/0).
awg-quick up "$CONF"

# RU exceptions direct, immediately after up.
n=0
while read -r cidr; do
  [ -z "$cidr" ] && continue
  ip route add "$cidr" via "$DEFGW" dev "$DEFDEV" 2>/dev/null && n=$((n+1)) || true
done < "$RU_ZONE"
rm -f "$RU_ZONE"
echo "added $n RU direct routes."

# wg iface MTU 1380 + MSS clamp (nuextract3: prevent an MTU blackhole for OpenRouter via the tunnel).
IFACE="$(basename "$CONF" .conf)"
ip link set dev "$IFACE" mtu "${WG_MTU:-1380}" 2>/dev/null || true
for chain in FORWARD OUTPUT; do
  iptables -t mangle -C "$chain" -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
  iptables -t mangle -A "$chain" -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
done
echo "wg iface $IFACE mtu=$(cat /sys/class/net/$IFACE/mtu 2>/dev/null), MSS clamp applied."

# [AM #1 verify] suppress_prefixlength rule actually present?
if ip rule | grep -q 'suppress_prefixlength 0'; then
  echo "OK: 'suppress_prefixlength 0' rule present — direct pins will win."
else
  echo "WARN: no suppress_prefixlength rule found — direct pins may be IGNORED. Do NOT confirm; investigate."
fi

# [AM #2 verify] control-plane routes DIRECT (dev == physical), not the tunnel iface.
fail=0
check_direct() {
  local ip="${1%%/*}" dev
  dev=$(ip route get "$ip" 2>/dev/null | grep -o 'dev [^ ]*' | awk '{print $2}')
  if [ "$dev" = "$DEFDEV" ]; then echo "  OK direct: $1 via $dev"
  else echo "  !! $1 routes via ${dev:-?} (NOT $DEFDEV) — control-plane at risk"; fail=1; fi
}
[ -n "${SSH_CONNECTION:-}" ] && check_direct "$(awk '{print $1}' <<<"$SSH_CONNECTION")"
for m in ${MGMT_CIDR:-}; do check_direct "$m"; done
YIP=$(getent ahostsv4 llm.api.cloud.yandex.net | awk '{print $1; exit}' || true)
[ -n "$YIP" ] && check_direct "$YIP"
echo "OpenRouter/Cloudflare (expect the awg tunnel iface):"; ip route get 104.16.0.1 | head -1 || true

if [ "$fail" = 0 ]; then
  echo ">>> control-plane looks DIRECT. Open a FRESH SSH session, confirm it works, THEN: touch /tmp/wg_confirmed"
else
  echo ">>> CONTROL-PLANE AT RISK — do NOT touch /tmp/wg_confirmed; let it roll back in ${ROLLBACK}s."
fi
echo ">>> teardown anytime: awg-quick down $CONF"
