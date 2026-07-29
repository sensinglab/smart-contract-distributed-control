# Distributed Control for Building Automation with Smart Contracts

Implementation accompanying the MSc dissertation **“Distributed Control for Building
Automation with Smart Contracts”** by Guilherme Gregório Mendes, Iscte – Instituto
Universitário de Lisboa (2026).

The prototype uses device-specific Algorand smart contracts as authoritative control
components for IoT devices. Two physically separated building environments coordinate
through Algorand TestNet without direct inter-building network communication.

## Architecture

Each building contains:

- an **openHAB** automation interface;
- a local **Python/FastAPI Bridge**;
- a local **Algorand node**;
- non-authoritative local JSON cache files maintained by the Bridge.

**Building A** acts as the device owner and creates/removes device contracts.
**Building B** acts as the authorised remote controller and changes device state.
Device announcements, state updates, and removal notifications are observed through
confirmed blockchain transactions.

## Repository structure

```text
bridge/
  building-a/bridge.py       Owner-side Bridge
  building-b/bridge.py       Remote-controller Bridge
config/
  building-a.env.example
  building-b.env.example
openhab/
  building-a/                Items, sitemap, and JavaScript automation
  building-b/                Items, sitemap, and JavaScript automation
systemd/
  iot-bridge.service.example Portable service template
  reference/                 Exact units used in the prototype
requirements.txt             Direct Python dependencies
docs/
  IMPLEMENTATION_NOTES.md
  deployed-environments/     Complete package freezes from both nodes
```

## Main lifecycle operations

1. **Device creation and discovery**
   - Building A calls `POST /devices/create`.
   - The Bridge deploys an Algorand application for the device.
   - Building A sends a payment transaction containing a `device_announce` JSON note.
   - Building B validates the sender and application creator, adds the device to its local
     registry, and refreshes openHAB.

2. **Remote state control**
   - Building B calls `POST /appcall` with `set:on` or `set:off`.
   - The smart contract checks the caller’s address and updates authoritative on-chain state.
   - Each Bridge watcher observes the confirmed application call and refreshes local state.

3. **Logical removal**
   - Building A calls `POST /devices/remove`.
   - The local operational entry is removed and a `device_remove` note is sent to Building B.
   - Building B removes the device from its local registry and openHAB interface.
   - The Algorand application remains on-chain for traceability.

## Prerequisites

- Linux host or Raspberry Pi;
- Python 3.13 (the prototype was deployed with Python 3.13 environments);
- a synchronized local Algorand TestNet node exposing algod on `127.0.0.1:8080`;
- funded TestNet controller accounts for both buildings;
- openHAB with JavaScript Scripting support;
- local filesystem access from openHAB to `/opt/iot-bridge/data`.

## 1. Install the Bridge

Run these steps independently on both buildings:

```bash
sudo mkdir -p /opt/iot-bridge/{config,data}
sudo chown -R "$USER":"$USER" /opt/iot-bridge

python3 -m venv /opt/iot-bridge/venv
/opt/iot-bridge/venv/bin/python -m pip install --upgrade pip
/opt/iot-bridge/venv/bin/pip install -r requirements.txt
```

Copy the correct Bridge implementation:

```bash
# Building A
cp bridge/building-a/bridge.py /opt/iot-bridge/bridge.py

# Building B
cp bridge/building-b/bridge.py /opt/iot-bridge/bridge.py
```

## 2. Configure each building

Copy the appropriate example and fill in the local values:

```bash
# Building A
cp config/building-a.env.example /opt/iot-bridge/config/.env

# Building B
cp config/building-b.env.example /opt/iot-bridge/config/.env

chmod 600 /opt/iot-bridge/config/.env
```

Important relationships:

- On Building A, `CONTROLLER_ADDRESS` is A’s public address and `PEER_CONTROLLER` is
  B’s public address.
- On Building B, `CONTROLLER_ADDRESS` is B’s public address and `PEER_CONTROLLER` is
  A’s public address.
- `CONTROLLER_MNEMONIC` is always the mnemonic for the local building account.
- Never commit the completed `.env` file.

The active Bridge reads these variables:

| Variable | Purpose |
|---|---|
| `ALGOD_ADDRESS` | Local algod API endpoint |
| `ALGOD_TOKEN` | Local algod API token |
| `CONTROLLER_MNEMONIC` | Local signing account mnemonic |
| `CONTROLLER_ADDRESS` | Public address embedded in newly created contract authorisation logic |
| `PEER_CONTROLLER` | Peer public address; on Building B, also the trusted owner address |
| `BUILDING_ID` | Local role identifier (`A` or `B`) |
| `OPENHAB_BASE` | Local openHAB REST endpoint |
| `OPENHAB_SYNC_ITEM` | Item triggered when openHAB should resynchronize |
| `BRIDGE_HTTP_PORT` | Local FastAPI port, normally `8787` |
| `ANNOUNCE_AMOUNT_MICROALGO` | Payment amount used for coordination notes |
| `STATE_DB` | Path of `state.json` |
| `LAST_ROUND_FILE` | Path of the last processed round file |

## 3. Install the openHAB configuration

Copy the files for the corresponding building:

```bash
# Example for Building A
sudo cp openhab/building-a/automation/iot-bridge-sync.js /etc/openhab/automation/js/
sudo cp openhab/building-a/items/iot-bridge.items /etc/openhab/items/
sudo cp openhab/building-a/sitemaps/iotbridge.sitemap /etc/openhab/sitemaps/
```

Use `openhab/building-b/` on Building B.

Before copying the Building B JavaScript file, replace:

```javascript
const MY_CONTROLLER_ADDRESS = "REPLACE_WITH_BUILDING_B_CONTROLLER_ADDRESS";
```

with Building B’s public Algorand controller address. This is a public address, not a
mnemonic or private key.

The JavaScript rules call the Bridge locally at `http://127.0.0.1:8787` and read the
Bridge cache from `/opt/iot-bridge/data`.

## 4. Install the systemd service

Copy the portable template and edit the local user:

```bash
sudo cp systemd/iot-bridge.service.example /etc/systemd/system/iot-bridge.service
sudo nano /etc/systemd/system/iot-bridge.service
```

Replace `REPLACE_WITH_LOCAL_USER`, then enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now iot-bridge
sudo systemctl status iot-bridge
```

The exact service units used in the evaluated prototype are kept under
`systemd/reference/`.

## REST API

The Bridge binds to `127.0.0.1` and exposes:

| Method and path | Purpose |
|---|---|
| `POST /devices/create` | Deploy a device application and announce it |
| `POST /devices/remove` | Perform logical removal and notify the peer |
| `POST /appcall` | Submit `set:on`, `set:off`, or another application argument |
| `GET /state?app_id=<id>` | Return cached state for one application |
| `GET /events?since=<round>` | Return locally recorded events after a round |
| `GET /health` | Return Bridge, algod, registry, and watcher status |

Example health check:

```bash
curl -s http://127.0.0.1:8787/health | python3 -m json.tool
```

Example state update from Building B:

```bash
curl -s -X POST http://127.0.0.1:8787/appcall \
  -H 'Content-Type: application/json' \
  -d '{"app_id": 123456789, "method": "set:on"}'
```

## Local state and authority

`registry.json`, `state.json`, `events.json`, and `last_round` are local synchronization
artifacts. They improve local interface behaviour but are not authoritative. The device
contract state confirmed on Algorand remains the source of authority for device state and
access-control decisions.

## Security

- Keep `.env` readable only by the service user.
- Never publish account mnemonics, algod tokens, or private keys.
- Keep the Bridge and algod APIs bound to the loopback interface.
- Building B validates lifecycle notes against the Building A public address configured in
  `PEER_CONTROLLER` and checks the creator of announced applications.
- Review `SECURITY.md` before publishing deployment files or logs.

## Notes on the deployed environments

The two Raspberry Pi environments contained slightly different complete package sets.
The root `requirements.txt` lists only the direct packages required by the Bridge, while
full `pip freeze` outputs are retained in `docs/deployed-environments/` for transparency.
The active implementation does not use MQTT; MQTT-era files and dependencies are not
part of the runtime source published here.
