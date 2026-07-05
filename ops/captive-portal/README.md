# Open Duck captive portal

Make joining the duck's Wi-Fi automatically pop up the control UI — so people
without SSH (or the app URL) just connect and get the controls, like public wifi.

## How it works (three layers, none touch SSH / gamepad / walk / the robot's own net)

1. **Web server** (`web_control.py`) serves the app routes (`/`, `/api/*`, …) and
   **302-redirects every other HTTP GET** to the control page. The OS
   connectivity-check probes (iOS `/hotspot-detect.html`, Android `/generate_204`,
   Windows `/connecttest.txt`, …) hit this and trigger the "Sign in to network" sheet.
2. **`nft` port-80 redirect** (`duck-captive.nft`): a non-root process can't bind
   port 80, so this kernel rule sends all `tcp/80` on `wlan0` to the server on `:8080`.
   It's in its own table (`duckcaptive`) and never disturbs NetworkManager's NAT.
3. **DNS hijack** (`dnsmasq-captive.conf`): points only the OS connectivity-check
   domains at the duck, so the popup fires even when the duck has **no upstream
   internet** (its normal state as an AP). All other names resolve normally.

HTTPS is intentionally left alone (can't be transparently intercepted without cert
errors); captive detection uses HTTP, so this is enough — exactly like every café hotspot.

## Install (on the Pi)

```bash
cd ~/Open_Duck_Mini_Runtime/ops/captive-portal
sudo bash setup-captive-portal.sh
sudo reboot            # activates the DNS half; the nft rule is already live
```

The nft redirect + web 302 are live immediately. The DNS hijack activates on the
reboot (we don't bounce the AP during install, since on an AP-only duck that would
drop your SSH). The dispatcher script re-applies the nft rule automatically on every
boot / AP reconnect.

## Verify

With head-puppet or walk running (so `:8080` is up), from the duck or a connected client:

```bash
curl -s -I -H 'Host: captive.apple.com' http://10.42.0.1/hotspot-detect.html
# -> HTTP/1.1 302 Found ; Location: http://10.42.0.1:8080/
```

On a phone: forget + rejoin the duck's Wi-Fi → the control page should pop up.
(If it doesn't auto-pop on a given phone, opening any `http://` site — or
`http://10.42.0.1:8080` — still lands on the controls.)

## Uninstall / revert

```bash
sudo bash setup-captive-portal.sh --uninstall
sudo reboot     # to also clear the DNS hijack
```

Or manually: `sudo nft delete table ip duckcaptive` and remove the three files
(`/etc/duck-captive.nft`, `dispatcher.d/90-duck-captive`,
`dnsmasq-shared.d/duck-captive.conf`).

## Tuning
- Different AP interface or web port → edit the two literals in `duck-captive.nft`
  (and the interface check in `90-duck-captive`).
- Different AP IP → edit `10.42.0.1` in `dnsmasq-captive.conf`.
