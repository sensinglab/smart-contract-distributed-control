// ============================================================================
// IoT ↔ Algorand Bridge ↔ openHAB (Building B - Controller)
//
//
// B's responsibilities in UI:
//   • Show all contracts relevant to B
//   • Display each contract's current state (from state.json)
//   • Provide a "Set ON" button per contract  → POST /appcall method=set:on
//   • Provide a "Set OFF" button per contract → POST /appcall method=set:off
// ============================================================================

const BRIDGE_URL    = "http://127.0.0.1:8787";
const REGISTRY_PATH = "/opt/iot-bridge/data/registry.json";
const STATE_PATH    = "/opt/iot-bridge/data/state.json";
//const CRON_2S       = "0/2 * * * * ?";

const SYNC_TRIGGER_ITEM = "IoT_SyncTrigger";

const BUILDING_ID   = "B";
const MY_CONTROLLER_ADDRESS = "REPLACE_WITH_BUILDING_B_CONTROLLER_ADDRESS";

const LOGGER = log("iot-bridge-b");

const Paths = Java.type("java.nio.file.Paths");
const Files = Java.type("java.nio.file.Files");
const StandardCharsets = Java.type("java.nio.charset.StandardCharsets");

function toInt(v) {
  let n = parseInt(String(v));
  return isNaN(n) ? 0 : n;
}

function ohStateFromGlobal(gs) {
  if (!gs) return "UNKNOWN";
  let s = String(gs.state || "").toLowerCase();
  if (s === "on" || s === "true") return "ON";
  if (s === "off" || s === "false") return "OFF";
  return "UNKNOWN";
}

function ensureStateItem(appId, name) {
  const itemName = `B_IoT_State_${appId}`;
  if (!items.existsItem(itemName)) {
    items.addItem({
      type: "String",
      name: itemName,
      label: `Contract ${appId} (${name}) state [%s]`,
      groups: ["gB_IoTContracts", "gB_IoTState"],
      category: "light"
    }, true);
  }
  return items.getItem(itemName);
}

function ensureOnButton(appId, name) {
  const itemName = `B_IoT_On_${appId}`;
  if (!items.existsItem(itemName)) {
    items.addItem({
      type: "Switch",
      name: itemName,
      label: `Set ON ${name}`,
      groups: ["gB_IoTContracts", "gB_IoTOn"],
      category: "switch"
    }, true);
  }
  return items.getItem(itemName);
}

function ensureOffButton(appId, name) {
  const itemName = `B_IoT_Off_${appId}`;
  if (!items.existsItem(itemName)) {
    items.addItem({
      type: "Switch",
      name: itemName,
      label: `Set OFF ${name}`,
      groups: ["gB_IoTContracts", "gB_IoTOff"],
      category: "switch"
    }, true);
  }
  return items.getItem(itemName);
}

function removeItem(name) {
  if (items.existsItem(name)) {
    LOGGER.info("Removing item {}", name);
    items.removeItem(name);
  }
}

// ---------------------------------------------------------------------------
// Sync from registry/state.json
// Triggered by the Bridge through IoT_SyncTrigger
// ---------------------------------------------------------------------------

function syncFromBridgeState(reason) {
  try {
    LOGGER.info("Running sync from Bridge state. reason={}", reason);

    const regPath = Paths.get(REGISTRY_PATH);
    if (!Files.exists(regPath)) return;

    const regData = new java.lang.String(Files.readAllBytes(regPath), StandardCharsets.UTF_8);
    if (!regData) return;

    const reg = JSON.parse(regData);
    const watched = reg.watched_apps || {};
    const activeIds = {};

    let state = {};
    try {
      const stPath = Paths.get(STATE_PATH);
      if (Files.exists(stPath)) {
        const stData = new java.lang.String(Files.readAllBytes(stPath), StandardCharsets.UTF_8);
        state = JSON.parse(stData);
      }
    } catch (e) {
      LOGGER.warn("Failed to read/parse state.json: {}", String(e));
    }

    Object.keys(watched).forEach(appIdStr => {
      const appId = toInt(appIdStr);
      if (!appId) return;

      const rec = watched[appIdStr] || {};
      const peerController = String(rec.peer_controller || "");

      if (peerController !== MY_CONTROLLER_ADDRESS) {
        return;
      }

      const name = rec.device || `app_${appId}`;
      activeIds[appIdStr] = true;

      const stateItem = ensureStateItem(appId, name);
      ensureOnButton(appId, name);
      ensureOffButton(appId, name);

      const stEntry = state[appIdStr] || {};
      const gs = stEntry.global_state || rec.state || {};
      const oh = ohStateFromGlobal(gs);
      stateItem.postUpdate(oh);
    });

    try {
      const group = items.getItem("gB_IoTContracts", true);
      group.members.forEach(member => {
        const parts = member.name.split("_");
        if (parts.length < 4) return;
        const appIdStr = parts[3];

        if (!activeIds[appIdStr]) {
          removeItem(member.name);
        }
      });
    } catch (e) {
      LOGGER.debug("Cleanup error: {}", String(e));
    }

  } catch (e) {
    LOGGER.warn("IoT B sync error: {}", String(e));
  }
}

rules.JSRule({
  name: "IoT B: Sync registry/state on Bridge trigger",
  triggers: [
    triggers.ItemCommandTrigger(SYNC_TRIGGER_ITEM),
    triggers.SystemStartlevelTrigger(100)
  ],
  execute: (event) => {
    const cmd = String(event.receivedCommand || "STARTUP");

    if (cmd !== "ON" && cmd !== "STARTUP") {
      return;
    }

    syncFromBridgeState(cmd);

    if (cmd === "ON") {
      items.getItem(SYNC_TRIGGER_ITEM).postUpdate("OFF");
    }
  }
});

rules.JSRule({
  name: "IoT B: Set ON buttons",
  triggers: [triggers.GroupCommandTrigger("gB_IoTOn")],
  execute: (event) => {
    try {
      const itemName = event.itemName;
      const cmd = String(event.receivedCommand || "");
      if (cmd !== "ON") return;

      const parts = itemName.split("_");
      if (parts.length < 4) {
        LOGGER.warn("Cannot parse app_id from {}", itemName);
        return;
      }

      const appId = toInt(parts[3]);
      if (!appId) return;

      const body = JSON.stringify({
        app_id: appId,
        method: "set:on"
      });

      items.getItem(`B_IoT_State_${appId}`).postUpdate("PENDING_ON");

      LOGGER.info("Calling /appcall set:on for app_id {}", appId);
      const res = actions.HTTP.sendHttpPostRequest(
        `${BRIDGE_URL}/appcall`,
        "application/json",
        body,
        10000
      );
      LOGGER.debug("Set ON response: {}", res);

      items.getItem(itemName).postUpdate("OFF");

    } catch (e) {
      LOGGER.warn("Set ON rule error: {}", String(e));
    }
  }
});

rules.JSRule({
  name: "IoT B: Set OFF buttons",
  triggers: [triggers.GroupCommandTrigger("gB_IoTOff")],
  execute: (event) => {
    try {
      const itemName = event.itemName;
      const cmd = String(event.receivedCommand || "");
      if (cmd !== "ON") return;

      const parts = itemName.split("_");
      if (parts.length < 4) {
        LOGGER.warn("Cannot parse app_id from {}", itemName);
        return;
      }

      const appId = toInt(parts[3]);
      if (!appId) return;

      const body = JSON.stringify({
        app_id: appId,
        method: "set:off"
      });

      items.getItem(`B_IoT_State_${appId}`).postUpdate("PENDING_OFF");

      LOGGER.info("Calling /appcall set:off for app_id {}", appId);
      const res = actions.HTTP.sendHttpPostRequest(
        `${BRIDGE_URL}/appcall`,
        "application/json",
        body,
        10000
      );
      LOGGER.debug("Set OFF response: {}", res);

      items.getItem(itemName).postUpdate("OFF");

    } catch (e) {
      LOGGER.warn("Set OFF rule error: {}", String(e));
    }
  }
});
