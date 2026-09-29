# Security and local trust assumptions

The Bridge signs transactions using the mnemonic loaded from `/opt/iot-bridge/config/.env`. Keep that file local, mode 600, inside a credential directory accessible only to the service user. Distributed examples intentionally contain no mnemonic or node API token.

Bridge endpoints bind to `127.0.0.1` and have no separate HTTP authentication. Access therefore depends on local host access. Keep the algod API local as well. On-chain state-changing methods enforce the addresses embedded in the contract.

Building B validates lifecycle notifications against the owner public address configured in `PEER_CONTROLLER` and checks announced application creators. This describes B's validated receive path, not a general claim that both Bridges validate every note identically.

The preserved implementation disables certificate verification for local openHAB HTTPS calls and does not send an openHAB API token. Its deployment assumes that the local openHAB REST item commands are permitted. A different access policy requires compatible authentication changes; review these separately from the archived implementation.

Logical removal affects registry and UI visibility. It does not revoke the contract's existing authorisations or delete on-chain state.

Before publishing a source snapshot, check its files and any Git history being published for completed environment files, mnemonics, API tokens, private keys, and deployment secrets. Version-control exclusions help with future additions; they do not remove files already committed.

Public addresses and transaction identifiers are not private signing credentials, but any accompanying logs or datasets should be reviewed for unrelated personal or deployment information.
