# Installation and verification

Run these instructions from the repository root on each building's Linux host. They assume an existing package installation of openHAB and a synchronized local Algorand TestNet node. The reference environment uses Python 3.13 and openHAB 5.1.3.

## 1. Prepare openHAB and the Algorand node

Install openHAB's **JavaScript Scripting** and **Basic UI** add-ons. The supplied file-based rules use injected openHAB namespaces. Enable namespace injection for file-based scripts in the JavaScript Scripting settings; see the [official documentation](https://www.openhab.org/addons/automation/jsscripting/).

Use distinct local ports:

| Service | Address used by this guide |
|---|---|
| Algorand algod API | `http://127.0.0.1:8080` |
| Bridge API | `http://127.0.0.1:8787` |
| openHAB HTTPS | `https://127.0.0.1:8444` |
| openHAB HTTP | Port `8081`, avoiding algod's port |

For an openHAB package installation, edit `/etc/default/openhab`. Set or update these entries while preserving the other settings:

~~~ini
OPENHAB_HTTP_PORT=8081
OPENHAB_HTTPS_PORT=8444
~~~

Restart openHAB after changing its ports:

~~~bash
sudo systemctl restart openhab
~~~

openHAB normally uses HTTP 8080 and HTTPS 8443. These are deliberate changes for this setup. For an existing deployment with different ports, set `OPENHAB_BASE` accordingly. Changing the Bridge port also requires updating `BRIDGE_URL` in the JavaScript rules.

On the contract-deploying node (A), merge `"EnableDeveloperAPI": true` into its existing `config.json` and restart the node using its normal management method. This enables the TEAL compilation endpoint used by the Bridge. Preserve the rest of the node configuration. See [node settings](https://dev.algorand.co/nodes/reference/config-settings/) and [the compile endpoint](https://dev.algorand.co/reference/rest-api/algod/operations/tealcompile/).

Use two funded **TestNet** accounts. Application creation requires fees and enough available balance for the application minimum-balance requirement. Zero-value notes still incur a network fee.

## 2. Install the Bridge for one building

Set one role in the current terminal: `a` on A or `b` on B.

~~~bash
BRIDGE_ROLE=a
~~~

Then run:

~~~bash
sudo install -d -m 755 /opt/iot-bridge
sudo chown "$USER":"$(id -gn)" /opt/iot-bridge
mkdir -p /opt/iot-bridge/config /opt/iot-bridge/data
chmod 700 /opt/iot-bridge/config
chmod 755 /opt/iot-bridge/data

python3.13 -m venv /opt/iot-bridge/venv
/opt/iot-bridge/venv/bin/python -m pip install -r requirements.txt

cp "bridge/building-$BRIDGE_ROLE/bridge.py" /opt/iot-bridge/bridge.py
cp "config/building-$BRIDGE_ROLE.env.example" /opt/iot-bridge/config/.env
chmod 600 /opt/iot-bridge/config/.env
~~~

Python 3.13 and venv support must already be installed. The full dependency freezes under `docs/deployed-environments/` are original deployment records; the root requirements file pins the direct dependencies from Building A.

## 3. Configure the accounts and local endpoints

Edit `/opt/iot-bridge/config/.env`, which is the path loaded by the Bridge.

| Setting | Building A | Building B |
|---|---|---|
| `BUILDING_ID` | `A` | `B` |
| `CONTROLLER_MNEMONIC` | A's local mnemonic | B's local mnemonic |
| `CONTROLLER_ADDRESS` | A's public address | B's public address |
| `PEER_CONTROLLER` | B's public address | A's public address |
| `ALGOD_TOKEN` | A's local node token | B's local node token |
| `ALGOD_ADDRESS` | Local algod URL | Local algod URL |
| `OPENHAB_BASE` | Local openHAB URL | Local openHAB URL |

The public controller address must match the mnemonic. Quote a mnemonic containing spaces. Keep the completed environment local; credential fields in the repository examples are intentionally empty.

Keep the explicit `ANNOUNCE_AMOUNT_MICROALGO=0` for zero-value signalling. Omitting the variable activates the original 1000-microAlgo fallback. It does not control the fee. A historical replication must use the configuration evidenced for those tests.

`STATE_DB` and `LAST_ROUND_FILE` are configurable. Registry and event paths remain fixed under `/opt/iot-bridge/data`. The JavaScript rules also read that directory. `USE_INDEXER` is retained for compatibility; the watcher reads algod directly.

## 4. Install the openHAB integration

On B, edit `openhab/building-b/automation/iot-bridge-sync.js` and replace `REPLACE_WITH_BUILDING_B_CONTROLLER_ADDRESS` with B's public address.

On A, the empty `PEER_CONTROLLER_ADDRESS` in JavaScript can remain empty: the Bridge falls back to its configured `PEER_CONTROLLER`. Keep the JavaScript `BUILDING_ID` consistent with the environment.

With `BRIDGE_ROLE` still set for this host:

~~~bash
sudo install -d /etc/openhab/automation/js /etc/openhab/items /etc/openhab/sitemaps
sudo install -m 644 "openhab/building-$BRIDGE_ROLE/items/iot-bridge.items" /etc/openhab/items/iot-bridge.items
sudo install -m 644 "openhab/building-$BRIDGE_ROLE/sitemaps/iotbridge.sitemap" /etc/openhab/sitemaps/iotbridge.sitemap
sudo install -m 644 "openhab/building-$BRIDGE_ROLE/automation/iot-bridge-sync.js" /etc/openhab/automation/js/iot-bridge-sync.js
~~~

For an existing installation, retain its configuration before replacing the named files. The openHAB service account needs to read the generated JSON data, but does not need access to the credential directory.

## 5. Start the Bridge

~~~bash
sudo cp systemd/iot-bridge.service.example /etc/systemd/system/iot-bridge.service
sudo nano /etc/systemd/system/iot-bridge.service
~~~

Replace `REPLACE_WITH_LOCAL_USER` with the Linux user owning `/opt/iot-bridge`. The portable template explicitly sets umask 0022 for readable cache files; the credential file remains mode 600 in a mode 700 directory.

~~~bash
sudo systemctl daemon-reload
sudo systemctl enable --now iot-bridge
sudo systemctl status iot-bridge --no-pager
~~~

The units under `systemd/reference/` retain original usernames and Python paths. Use the portable template for a new installation.

## 6. Verify the installation

~~~bash
curl -fsS http://127.0.0.1:8787/health | python3 -m json.tool
sudo journalctl -u iot-bridge -n 50 --no-pager
~~~

Confirm that `last_seen_round` advances as the node advances. Once files have been generated, check read access:

~~~bash
sudo -u openhab test -r /opt/iot-bridge/data/registry.json
sudo -u openhab test -r /opt/iot-bridge/data/state.json
~~~

A successful `test` produces no output and exits with status zero. `state.json` may only appear after creation or a monitored state change.

Open A's Basic UI sitemap at `/basicui/app?sitemap=iotbridge`. Create a device; confirm its appearance on B; change its state on B; confirm both interfaces; remove it on A; confirm both operational entries disappear. These checks create new TestNet transactions and are distinct from the archived performance experiments.

## Troubleshooting

| Symptom | Check |
|---|---|
| Connection refused on 8080 | Node status, algod address, port ownership |
| TEAL compilation rejected | A node's `EnableDeveloperAPI` setting |
| Bridge fails to start | Environment path, mnemonic, token, service user, Python environment |
| No discovery on B | Node synchronization, peer addresses, B's validation logs |
| Failed openHAB trigger | HTTPS port, `OPENHAB_BASE`, `IoT_SyncTrigger`, REST access |
| openHAB HTTP 401/403 | Local REST authorisation; the preserved Bridge sends no openHAB API token |
| Undefined JavaScript namespaces | Scripting add-on and injection for file-based scripts |
| No dynamic B items | Public-address placeholder, cache contents, read permissions |
| UI request timeout | Check Bridge logs before retrying; the 10-second UI timeout can expire before an operation completes |

The preserved Bridge disables TLS certificate verification for local openHAB requests. See [security notes](../SECURITY.md).
