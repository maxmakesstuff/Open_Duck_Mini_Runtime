<#
  windows_transfer.ps1 — deploy the Open Duck Mini runtime to a duck from Windows.

  The macOS/Linux twin is mac_transfer.command. This Windows version uses only tools
  that ship with Windows 10 (1809+) / 11: the OpenSSH client (ssh, scp) and tar. It
  packs the runtime files into one archive, copies it to the robot, and extracts it —
  backing up each existing file once as <file>.orig, exactly like the mac script. It
  deliberately does NOT touch ~/duck_config.json (per-robot offsets / IMU trim / flags).

  Requirements:
    * Windows 10 1809+ / 11 with the "OpenSSH Client" (on by default; otherwise
      Settings > Apps > Optional features > Add > "OpenSSH Client").  tar ships with
      Windows 10 1803+.
    * Your PC joined to the duck's "Openduck" Wi-Fi.

  Run it:
      Right-click this file  >  "Run with PowerShell"
    or from a terminal:
      powershell -ExecutionPolicy Bypass -File .\windows_transfer.ps1

  You'll be asked for the duck password TWICE (once to copy, once to extract) — that's
  normal on Windows (no SSH connection-sharing). Set up an SSH key to skip the prompts.

  For a different host/user, edit $RemoteUser / $RemoteHost below.
#>

$ErrorActionPreference = 'Stop'

$RemoteUser = 'bdxv2'
$RemoteHost = 'bdxv2.local'
$Target     = "$RemoteUser@$RemoteHost"
# Files to ship. Each path is relative to the repo root AND is its path on the robot
# (the robot mirrors this layout under ~/Open_Duck_Mini_Runtime).
$Files = @(
  # --- scripts (entrypoints) ---
  'scripts/head_puppet.py',
  'scripts/v2_rl_walk_mujoco.py',
  'scripts/probe_battery.py',
  'scripts/find_soft_offsets.py',
  'scripts/calibrate_imu.py',
  'scripts/imu_health_check.py',
  'scripts/apply_stability_defaults.py',
  'scripts/gamepad_probe.py',
  'scripts/first_time_setup.py',
  # --- runtime library (mini_bdx_runtime package) ---
  'mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py',
  'mini_bdx_runtime/mini_bdx_runtime/buttons.py',
  'mini_bdx_runtime/mini_bdx_runtime/face_tracker.py',
  'mini_bdx_runtime/mini_bdx_runtime/scanner_sound.py',
  'mini_bdx_runtime/mini_bdx_runtime/control_bus.py',
  'mini_bdx_runtime/mini_bdx_runtime/web_control.py',
  'mini_bdx_runtime/mini_bdx_runtime/telemetry.py',
  'mini_bdx_runtime/mini_bdx_runtime/battery.py',
  'mini_bdx_runtime/mini_bdx_runtime/fall_detector.py',
  'mini_bdx_runtime/mini_bdx_runtime/walk_record.py',
  'mini_bdx_runtime/mini_bdx_runtime/duck_config.py',
  'mini_bdx_runtime/mini_bdx_runtime/rustypot_position_hwi.py',
  'mini_bdx_runtime/mini_bdx_runtime/raw_imu.py',
  'mini_bdx_runtime/mini_bdx_runtime/imu_trim.py',
  'mini_bdx_runtime/mini_bdx_runtime/stability_governor.py',
  'mini_bdx_runtime/mini_bdx_runtime/walk_defaults.py',
  'mini_bdx_runtime/mini_bdx_runtime/antennas.py',
  'mini_bdx_runtime/mini_bdx_runtime/antenna_anim.py',
  # --- Web UI page (served by web_control.py) ---
  'mini_bdx_runtime/mini_bdx_runtime/webui/index.html',
  'mini_bdx_runtime/mini_bdx_runtime/webui/mock_server.py',
  # --- scanner sounds ---
  'mini_bdx_runtime/assets/scanner/lamp.wav',
  'mini_bdx_runtime/assets/scanner/lamp2.wav',
  'mini_bdx_runtime/assets/scanner/lamp3.wav',
  # --- Xbox Bluetooth auto-reconnect kit ---
  'ops/bluetooth/README.md',
  'ops/bluetooth/setup-bluetooth-reconnect.sh',
  'ops/bluetooth/duck-pad-reconnect.sh',
  'ops/bluetooth/duck-pad-reconnect.service',
  'ops/bluetooth/install-xpadneo.md',
  # --- Captive portal ---
  'ops/captive-portal/README.md',
  'ops/captive-portal/setup-captive-portal.sh',
  'ops/captive-portal/duck-captive.nft',
  'ops/captive-portal/90-duck-captive',
  'ops/captive-portal/dnsmasq-captive.conf',
  # --- Antenna jitter fix (pigpio) ---
  'ops/pigpio/README.md',
  'ops/pigpio/setup-pigpio.sh',
  'ops/pigpio/pigpiod-override.conf'
)

# This script lives in the repo root; work from there regardless of where it's launched.
Set-Location -LiteralPath $PSScriptRoot

Write-Host "================================================="
Write-Host " Open Duck Mini deploy (Windows)  ->  $Target"
Write-Host "================================================="
Write-Host ""

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }

# 1) tools present?
foreach ($t in 'ssh', 'scp', 'tar') {
  if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
    Fail "'$t' not found. Enable the OpenSSH Client (Settings > Apps > Optional features). 'tar' ships with Windows 10 1803+."
  }
}

# 2) every local file exists?
$missing = $Files | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) { Write-Host "Missing local files:"; $missing | ForEach-Object { Write-Host "  $_" }; Fail "run this from the repo root." }

# 3) pack the files into one tar (relative paths preserved; PowerShell splats the array)
$tar = Join-Path $env:TEMP 'duck_transfer.tar'
if (Test-Path $tar) { Remove-Item -LiteralPath $tar -Force }
tar -cf $tar $Files
if ($LASTEXITCODE -ne 0) { Fail "tar failed to pack the files." }
Write-Host ("Packed {0} files." -f $Files.Count)

# 4) copy the archive to the robot  (password prompt #1)
Write-Host "`nCopying to $Target  — enter the duck password ..."
scp -o StrictHostKeyChecking=no $tar "${Target}:/tmp/duck_transfer.tar"
if ($LASTEXITCODE -ne 0) { Fail "scp failed. Is the duck on and is your PC on the 'Openduck' Wi-Fi?" }

# 5) extract on the robot, backing up each existing file once as .orig  (password prompt #2)
$remote = @'
set -e
cd "$HOME/Open_Duck_Mini_Runtime"
for f in $(tar -tf /tmp/duck_transfer.tar); do
  case "$f" in */) continue ;; esac
  if [ -f "$f" ] && [ ! -f "$f.orig" ]; then cp "$f" "$f.orig"; fi
  mkdir -p "$(dirname "$f")"
done
tar -xf /tmp/duck_transfer.tar -C "$HOME/Open_Duck_Mini_Runtime"
rm -f /tmp/duck_transfer.tar
echo "  extracted; existing files backed up once as <file>.orig"
'@
Write-Host "Deploying on the robot — enter the duck password again ..."
ssh -o StrictHostKeyChecking=no $Target $remote
if ($LASTEXITCODE -ne 0) { Fail "remote extract failed." }

Remove-Item -LiteralPath $tar -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host (" Done. Deployed {0} files." -f $Files.Count) -ForegroundColor Green
Write-Host @"

 NEXT (on the robot, once):
   1. First time only:  cd ~/Open_Duck_Mini_Runtime && pip install -e .
   2. Guided bring-up:  python scripts/first_time_setup.py
        (motors, offsets, IMU calibrate+trim, walk tuning, features, ops kits)
   3. Antenna jitter fix:  sudo bash ops/pigpio/setup-pigpio.sh
   4. Captive portal:      sudo bash ops/captive-portal/setup-captive-portal.sh  &&  sudo reboot
   5. Xbox auto-reconnect: bash ops/bluetooth/setup-bluetooth-reconnect.sh

 This did NOT touch ~/duck_config.json (per-robot). Seed walk tuning with
 python scripts/apply_stability_defaults.py, then calibrate + trim per robot.

 Roll back a file on the robot: restore <file>.orig (existing) or delete it (new).
"@
Read-Host "Press Enter to close"
