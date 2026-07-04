#!/usr/bin/env bash
# AmnesiaWG (Estonia) FULL TUNNEL: ALL egress exits via Estonia.
# Chosen because the direct immers->Yandex-Cloud path is an unreliable upstream route (peering
# quirk to 158.160/16, confirmed by the immers owner). Yandex auth is Api-Key (not IP-geo-locked),
# so an Estonian exit works — and it fixes OpenRouter (RU-blocked) in the same move.
#
# SAFETY (remote box, AM-reviewed): pin control-plane DIRECT -> arm a systemd dead-man rollback
# that SURVIVES SSH disconnect -> tunnel up -> wg MTU 1380 + MSS clamp -> verify -> operator confirms.
#
# MGMT_CIDR = space-list of admin sources to keep DIRECT (fleet box 195.209.216.45, OSN 188.190.8.61).
# Run INSIDE your ssh session. Under sudo, pass SSH_CONNECTION through explicitly:
#   sudo SSH_CONNECTION="$SSH_CONNECTION" ROLLBACK=120 \
#        MGMT_CIDR="195.209.216.45/32 188.190.8.61/32" bash wg-full-tunnel.sh estonia.conf
set -euo pipefail
CONF="${1:-estonia.conf}"
ROLLBACK="${ROLLBACK:-120}"
WG_MTU="${WG_MTU:-1380}"

[ -r "$CONF" ] || { echo "conf not found: $CONF" >&2; exit 1; }
command -v awg-quick >/dev/null 2>&1 || { echo "awg-quick not installed" >&2; exit 1; }
CONF_ABS="$(readlink -f "$CONF")"
IFACE="$(basename "$CONF" .conf)"

# [AM] direct pins only beat the tunnel if awg-quick installs suppress_prefixlength 0 (no Table= override)
grep -qiE '^[[:space:]]*Table[[:space:]]*=' "$CONF" && { echo "REFUSING: $CONF sets Table= (would ignore direct pins)"; exit 1; }

DEFGW=$(ip route show default | awk '/default/{print $3; exit}')
DEFDEV=$(ip route show default | awk '/default/{print $5; exit}')
[ -n "$DEFGW" ] && [ -n "$DEFDEV" ] || { echo "no physical default route" >&2; exit 1; }
echo "physical default: via $DEFGW dev $DEFDEV"

# Pin control-plane DIRECT before any tunnel change.
PINS=()
[ -n "${SSH_CONNECTION:-}" ] && PINS+=("$(awk '{print $1"/32"}' <<<"$SSH_CONNECTION")")
PINS+=("169.254.0.0/16")
for m in ${MGMT_CIDR:-}; do PINS+=("$m"); done
for p in "${PINS[@]}"; do ip route replace "$p" via "$DEFGW" dev "$DEFDEV" && echo "  pinned DIRECT: $p"; done

# Dead-man rollback via systemd (survives SSH disconnect — a backgrounded subshell would die on SIGHUP).
rm -f /tmp/wg_confirmed
systemctl stop wg-deadman.timer 2>/dev/null || true
systemd-run --unit=wg-deadman --on-active="${ROLLBACK}" \
  /bin/sh -c "[ -f /tmp/wg_confirmed ] || { logger -t wg-deadman rolling-back; awg-quick down '$CONF_ABS'; ip route flush cache; }" >/dev/null
echo "dead-man rollback armed via systemd (${ROLLBACK}s) — survives disconnect."

# Bring up the full tunnel.
awg-quick up "$CONF_ABS"

# wg iface MTU 1380 + MSS clamp (nuextract3: don't reintroduce the MTU blackhole through the tunnel).
ip link set dev "$IFACE" mtu "$WG_MTU"
for chain in FORWARD OUTPUT; do
  iptables -t mangle -C "$chain" -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
  iptables -t mangle -A "$chain" -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
done
echo "wg iface $IFACE mtu=$(cat /sys/class/net/$IFACE/mtu), MSS clamp on FORWARD+OUTPUT."

# Verify: suppress_prefixlength present + control-plane still DIRECT.
ip rule | grep -q 'suppress_prefixlength 0' && echo "OK: suppress_prefixlength 0 present (pins win)" || echo "WARN: no suppress rule — pins may be ignored; do NOT confirm"
fail=0
checkdirect(){ local ip="${1%%/*}" d; d=$(ip route get "$ip" 2>/dev/null | grep -o 'dev [^ ]*'|awk '{print $2}'); if [ "$d" = "$DEFDEV" ]; then echo "  OK direct: $1 ($d)"; else echo "  !! $1 via ${d:-?} NOT $DEFDEV"; fail=1; fi; }
[ -n "${SSH_CONNECTION:-}" ] && checkdirect "$(awk '{print $1}' <<<"$SSH_CONNECTION")"
for m in ${MGMT_CIDR:-}; do checkdirect "$m"; done
echo "  tunnel exit IP (via tunnel): $(timeout 10 curl -s --max-time 9 https://api.ipify.org 2>/dev/null || echo '?')"
if [ "$fail" = 0 ]; then
  echo ">>> control-plane DIRECT. Now verify a real Yandex call + OpenRouter THROUGH the tunnel,"
  echo ">>> confirm SSH from a FRESH session, THEN: touch /tmp/wg_confirmed   (cancels dead-man)"
else
  echo ">>> CONTROL-PLANE AT RISK — do NOT confirm; dead-man rolls back in ${ROLLBACK}s."
fi
echo ">>> teardown: awg-quick down $CONF_ABS"
