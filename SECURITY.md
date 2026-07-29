# Security notes

The Bridge signs Algorand transactions with the account mnemonic configured in
`/opt/iot-bridge/config/.env`. That file must remain local and must never be committed.

Before publishing changes, check that the repository does not contain:

- `CONTROLLER_MNEMONIC` values;
- algod tokens;
- private keys or certificates;
- copied `.env` files;
- runtime cache files (`registry.json`, `state.json`, `events.json`, `last_round`);
- logs containing sensitive deployment information.

Only public Algorand addresses belong in the example configuration. The Bridge HTTP
API is intentionally bound to `127.0.0.1`, and the local algod API should also remain
bound to the loopback interface.
