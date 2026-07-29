# Implementation notes

## Roles

- **Building A** is the device owner. Its openHAB interface creates device contracts,
  displays locally owned contracts, and requests logical removal.
- **Building B** is the authorised remote controller. It discovers announcements from
  Building A and submits `set:on` and `set:off` application calls.

Both buildings run a local Bridge and a local Algorand node. There is no direct
inter-building network connection; coordination is performed through confirmed
Algorand transactions.

## Publication cleanup applied

The source files in this repository were prepared from the deployed prototype exports.
The following publication-only cleanup was applied:

1. Real `.env` files, virtual environments, local JSON caches, backups, and MQTT-era
   files were excluded.
2. The example environment files were aligned with the variable names read by the
   active Bridge (`OPENHAB_BASE`, `OPENHAB_SYNC_ITEM`, and the cache paths).
3. Obsolete MQTT variables were removed from the Building B example.
4. The installation-specific Building B controller address in the openHAB JavaScript
   was replaced with `REPLACE_WITH_BUILDING_B_CONTROLLER_ADDRESS`.
5. A compact root `requirements.txt` contains direct dependencies. The complete
   package freezes from both deployed environments are retained under
   `docs/deployed-environments/` for provenance.
6. The two exact systemd units used by the prototype are retained under
   `systemd/reference/`; a portable template is provided separately.

No lifecycle or blockchain logic in either `bridge.py` file was rewritten.

## Runtime files

The Bridge creates and maintains these non-authoritative local files under
`/opt/iot-bridge/data`:

- `registry.json`: discovered and locally tracked device contracts;
- `state.json`: most recently observed on-chain state;
- `events.json`: locally recorded lifecycle and state-change events;
- `last_round`: most recently processed Algorand round.

They are generated during execution and are intentionally excluded from version control.
