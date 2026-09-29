# Smart-contract-based distributed building control

Prototype accompanying the MSc dissertation **“Design and Experimental Evaluation of Smart-Contract-Based Distributed Building Control on Resource-Constrained Edge Platforms”**, by Guilherme Gregório Mendes, Iscte – Instituto Universitário de Lisboa, September 2026. Supervisor: Rui Neto Marinheiro.

Source repository: [sensinglab/smart-contract-distributed-control](https://github.com/sensinglab/smart-contract-distributed-control).

Two building environments coordinate logical device lifecycle operations through Algorand TestNet. Each runs openHAB, a Python Bridge, and a local Algorand node. The evaluated platforms were a Raspberry Pi 5 (8 GB RAM, Building A) and a Raspberry Pi 4 Model B (4 GB RAM, Building B). The devices are logical representations; physical actuators were not evaluated.

## Architecture and workflow

| Component | Implementation |
|---|---|
| Automation layer | openHAB interface and JavaScript rules |
| Integration layer | Python/FastAPI Bridge |
| Blockchain access layer | Local Algorand node in each building |
| Shared blockchain layer | Algorand TestNet; one application per logical device |
| Local state storage | JSON registry, state cache, events, and last processed round |

Building A owns devices and initiates creation and logical removal. Building B discovers devices and submits authorised state changes. REST calls and filesystem access stay local to each building; cross-building coordination uses blockchain transactions.

1. **Creation:** A deploys an application and sends a `device_announce` note. B validates the note version, sender, required fields, and application creator.
2. **Control:** B submits `set:on` or `set:off` using `POST /appcall`. The contract checks the sender and updates its global state.
3. **Logical removal:** A removes its operational entry and sends a `device_remove` note. B removes its registry entry and interface items. The application remains on-chain. This workflow does not change the on-chain `active` value or revoke the contract's existing on-chain authorisations.

The PyTeal approval and clear-state programs are embedded in each `bridge.py`, in `build_device_approval_teal()` and `build_device_clear_teal()`.

The watcher checks for new rounds, with a two-second wait between checks, and processes every unprocessed round sequentially. **openHAB does not periodically poll the Bridge or cache files.** The Bridge commands the local `IoT_SyncTrigger` item; its rule reads the updated files. The rules also synchronize at openHAB startup.

## Contents

| Path | Contents |
|---|---|
| `bridge/building-a/`, `bridge/building-b/` | Owner and controller Bridges, including contract templates |
| `openhab/building-a/`, `openhab/building-b/` | JavaScript rules, Items, and sitemaps |
| `config/*.env.example` | Configuration templates without credentials |
| `systemd/iot-bridge.service.example` | Portable service template |
| `systemd/reference/` | Original deployment units |
| `requirements.txt` | Pinned direct Python dependencies |
| `docs/deployed-environments/` | Original package freezes from both nodes |
| `docs/INSTALLATION.md` | Installation and functional verification |
| `docs/IMPLEMENTATION_NOTES.md` | Behaviour, limitations, and publication changes |
| `CITATION.cff` | Software metadata and preferred dissertation citation |

## Installation

Follow [the installation guide](docs/INSTALLATION.md) on each host separately.

The reference environment uses Python 3.13 and openHAB 5.1.3. The root requirements file uses Building A's direct dependency versions. The full freezes document both deployed Python environments.

## Payment amounts and fees

The examples explicitly configure:

~~~ini
ANNOUNCE_AMOUNT_MICROALGO=0
~~~

This sets the transferred amount of announcement/removal transactions to zero. Network transaction fees still apply. This variable controls the **payment amount**, not the fee.

The Bridge retains its original fallback of **1000 microAlgos** if the variable is absent, so set it explicitly. The examples configure zero-value signalling. Configuration provenance and the distinction from historical measurements are documented in the [implementation notes](docs/IMPLEMENTATION_NOTES.md).

## Local REST API

The supplied configuration binds the Bridge to `127.0.0.1:8787`.

| Endpoint | Use in the evaluated workflow |
|---|---|
| `POST /devices/create` | A: deploy and announce a device |
| `POST /devices/remove` | A: remove the operational entry and notify B |
| `POST /appcall` | B: submit a permitted contract operation |
| `GET /state?app_id=<id>` | Read cached application state |
| `GET /events?since=<round>` | Read locally recorded events after a round |
| `GET /health` | Read Bridge/node status and registry progress |

Both Bridges expose these endpoints; the roles above describe the evaluated interface. Local API access is not a separate role-authentication mechanism. Contract state changes are checked on-chain.

~~~bash
curl -fsS http://127.0.0.1:8787/health | python3 -m json.tool
~~~

A successful response validates the status checks it reports, not the complete openHAB-to-blockchain workflow.

## Research data and citation

This repository contains the prototype implementation. Experimental datasets and evaluation scripts are being prepared for a separate Zenodo record. A link to that record will be added when it is available.

Use [CITATION.cff](CITATION.cff) to cite the dissertation and identify the software version used. A software DOI belongs to the software record; a separate dataset DOI should be linked as a related output, not substituted for it.

## Security and licence

See [SECURITY.md](SECURITY.md) for the prototype's local trust assumptions.

A reuse licence has not yet been selected for this repository.

## Documentation references

- [openHAB on Linux](https://www.openhab.org/docs/installation/linux.html)
- [openHAB ports and access settings](https://www.openhab.org/docs/installation/security)
- [JavaScript Scripting](https://www.openhab.org/addons/automation/jsscripting/)
- [Algorand node configuration](https://dev.algorand.co/nodes/reference/config-settings/)
- [Algorand TEAL compilation](https://dev.algorand.co/reference/rest-api/algod/operations/tealcompile/)
