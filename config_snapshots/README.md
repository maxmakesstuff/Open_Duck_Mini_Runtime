# Config snapshots — known-good `~/duck_config.json` restore points

The live per-robot config lives at `~/duck_config.json` ON the duck and is **not**
otherwise tracked (transfer.command deliberately never touches it). These snapshots
are durable, git-tracked copies of a *known-good* state so we can always restore.

To revert the duck to a snapshot:
```bash
scp config_snapshots/<file>.json bdxv2@bdxv2.local:~/duck_config.json
```

| Snapshot | State |
|----------|-------|
| `duck_config.baseline-action_scale-0.20.json` | **The "steady as hell" baseline** (2026-07-04): `action_scale=0.20` live, live-tuned `imu_trim` (pitch +0.0195, roll −0.0283), `phase_frequency_factor_offset=−0.1`, all 14 joint offsets. Captured BEFORE enabling `velocity_clip` + `stability_governor`. Restore this to undo both of those levers at once. |
