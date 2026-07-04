#!/usr/bin/env bash
#
# Install the Open Duck captive portal: joining the duck's Wi-Fi pops up the
# control UI (like public/coffee-shop wifi). Idempotent; run with sudo on the Pi.
#
#   sudo bash setup-captive-portal.sh            # install (nft live now; DNS on reboot)
#   sudo bash setup-captive-portal.sh --uninstall
#
# What it does (none of it touches SSH/tcp22, the gamepad, walk, or the robot's
# own internet):
#   1) /etc/duck-captive.nft            + applies it now (tcp/80 on wlan0 -> :8080)
#   2) dispatcher.d/90-duck-captive     (re-applies the nft rule on every AP up)
#   3) dnsmasq-shared.d/duck-captive.conf (captive-check domains -> the duck)
#
# The nft redirect + web-server 302 are LIVE immediately. The DNS-hijack half
# (which makes the popup fire when the duck has NO internet) activates when the
# AP's dnsmasq restarts — i.e. on the next reboot. We deliberately do NOT bounce
# the AP here, because on an AP-only duck that would drop your SSH session.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NFT_SRC="$HERE/duck-captive.nft"
DISP_SRC="$HERE/90-duck-captive"
DNS_SRC="$HERE/dnsmasq-captive.conf"

NFT_DST="/etc/duck-captive.nft"
DISP_DST="/etc/NetworkManager/dispatcher.d/90-duck-captive"
DNS_DST="/etc/NetworkManager/dnsmasq-shared.d/duck-captive.conf"

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo."; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    echo "Removing captive portal…"
    /usr/sbin/nft delete table ip duckcaptive 2>/dev/null || true
    rm -f "$NFT_DST" "$DISP_DST" "$DNS_DST"
    echo "Removed nft rule + files. Reboot (or reapply NM) to drop the DNS hijack."
    exit 0
fi

command -v nft >/dev/null 2>&1 || { echo "ERROR: nft not found"; exit 1; }
for f in "$NFT_SRC" "$DISP_SRC" "$DNS_SRC"; do
    [ -f "$f" ] || { echo "ERROR: missing source file: $f"; exit 1; }
done

echo "1) installing + applying the nft port-80 redirect…"
install -m 0644 "$NFT_SRC" "$NFT_DST"
/usr/sbin/nft -f "$NFT_DST"
echo "   applied. Current duckcaptive table:"
/usr/sbin/nft list table ip duckcaptive | sed 's/^/     /'

echo "2) installing the NetworkManager dispatcher (re-applies on AP up)…"
install -m 0755 "$DISP_SRC" "$DISP_DST"

echo "3) installing the captive-check DNS hijack (dnsmasq-shared.d)…"
install -d -m 0755 /etc/NetworkManager/dnsmasq-shared.d
install -m 0644 "$DNS_SRC" "$DNS_DST"

cat <<EOF

Done.
  • The nft port-80 redirect is LIVE now (all AP HTTP -> the control UI).
  • The DNS captive-check hijack activates on the NEXT REBOOT (we don't bounce
    the AP here so your SSH stays up). 'sudo reboot' when convenient.

Quick test (with head-puppet or walk running so :8080 is up):
  curl -s -I -H 'Host: captive.apple.com' http://10.42.0.1/hotspot-detect.html
    -> expect: HTTP/1.1 302 Found, Location: http://10.42.0.1:8080/

Uninstall: sudo bash setup-captive-portal.sh --uninstall
EOF
