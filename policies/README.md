# Policy slots

Every policy the duck can run is one ONNX file plus one small JSON file describing it.
The walk loop lists them in the Web UI (TUNE → Policy); you pick one there and the duck
remembers the choice.

The stock walk is not in this folder. It is the file passed to the walk script with
`--onnx_model_path` and always appears as the slot `stock`.

## Adding a policy

1. Copy the `.onnx` file here.
2. Add `<id>.json` next to it:

```json
{
  "id": "my-walk",
  "name": "My walk",
  "kind": "walk",
  "file": "my_walk.onnx",
  "obs_size": 101,
  "action_size": 14,
  "license": "…",
  "source": "…",
  "summary": "One sentence shown in the Web UI.",
  "defaults": {"action_scale": 0.25, "phase_frequency_factor_offset": 0.0}
}
```

`kind` is `walk` (101 inputs, the stock layout) or `getup` (92 inputs: the walk layout
without the 7 commands and the 2 gait-clock values). Other layouts need runtime code and
are refused. `sha256` is optional; if present, a file that does not match is refused.
A policy on the duck only (not in git) can go in `~/duck_policies/` in the same format.

Add new files to `FILES` in `mac_transfer.command` and `$Files` in `windows_transfer.ps1`.

## Files here

| File | From | Licence |
|---|---|---|
| `dk5426_walker_258M.onnx` | dk5426/open-duck-curation, `policies/ours_walker_full300M_258M.onnx` | Apache-2.0 |
| `dk5426_getup_real2_80M.onnx` | same repo, `policies/getup_real2_80M.onnx` | Apache-2.0 |
| `dk5426_getup_real_41M.onnx` | same repo, `policies/getup_real_41M.onnx` | Apache-2.0 |

Copied unmodified at commit `20639cf` (2026-11-24). Authors: Dhawal Kabra and team.
The Apache-2.0 licence text is in `LICENSE-dk5426.txt`.
