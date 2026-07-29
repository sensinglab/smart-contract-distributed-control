// ============================================================================
// IoT ↔ Algorand Bridge ↔ openHAB (Building A - Owner)
// 
// A's responsibilities in UI:
//   • Show all contracts owned by A (owner_building == BUILDING_ID)
//   • Display each contract's current state (from state.json)
//   • Provide a "Remove" button per contract → POST /devices/remove
//   • Provide a "Create new IoT device" admin button → POST /devices/create
// ============================================================================

const BRIDGE_URL    = "http://127.0.0.1:8787";
const REGISTRY_PATH = "/opt/iot-bridge/data/registry.json";
const STATE_PATH    = "/opt/iot-bridge/data/state.json";
//const CRON_2S       = "0/2 * * * * ?";
const SYNC_TRIGGER_ITEM = "IoT_SyncTrigger";

// IMPORTANT: set this to this building's ID (same as .env BUILDING_ID)
const BUILDING_ID   = "A";

// If you want to include a peer controller address for B when creating devices,
// fill this in (can be empty if you call /devices/create differently):
const PEER_CONTROLLER_ADDRESS = "";  // e.g. "DLE3W...."

const LOGGER = log("iot-bridge-a");

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

// ---------------------------------------------------------------------------
// Ensure items
// ---------------------------------------------------------------------------

function ensureStateItem(appId, name) {
  const itemName = `A_IoT_State_${appId}`;
  if (!items.existsItem(itemName)) {
    items.addItem({
      type: "String",
      name: itemName,
      label: `${name} (ID: ${appId}) state [%s]`,
      groups: ["gA_IoTContracts", "gA_IoTState"],
      category: "light"
    }, true);
  }
  return items.getItem(itemName);
}

function ensureRemoveButton(appId, name) {
  const itemName = `A_IoT_Remove_${appId}`;
  if (!items.existsItem(itemName)) {
    items.addItem({
      type: "Switch",
      name: itemName,
      label: `Remove ${name}`,
      groups: ["gA_IoTContracts", "gA_IoTRemove"],
      category: "power"
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
      const ownerBuilding = rec.owner_building || rec.building || "";

      if (ownerBuilding !== BUILDING_ID) {
        return;
      }

      const name = rec.device || `app_${appId}`;
      activeIds[appIdStr] = true;

      const stateItem = ensureStateItem(appId, name);
      ensureRemoveButton(appId, name);

      const stEntry = state[appIdStr] || {};
      const gs = stEntry.global_state || rec.state || {};
      const oh = ohStateFromGlobal(gs);
      stateItem.postUpdate(oh);
    });

    try {
      const group = items.getItem("gA_IoTContracts", true);
      group.members.forEach(member => {
        const parts = member.name.split("_");
        if (parts.length < 3) return;
        const appIdStr = parts[3] || parts[2];

        if (!activeIds[appIdStr]) {
          removeItem(member.name);
        }
      });
    } catch (e) {
      LOGGER.debug("Cleanup error: {}", String(e));
    }

  } catch (e) {
    LOGGER.warn("IoT A sync error: {}", String(e));
  }
}

rules.JSRule({
  name: "IoT A: Sync registry/state on Bridge trigger",
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

// ---------------------------------------------------------------------------
// Rule 2 – Remove button → /devices/remove
// ---------------------------------------------------------------------------

rules.JSRule({
  name: "IoT A: Remove contract buttons",
  triggers: [triggers.GroupCommandTrigger("gA_IoTRemove")],
  execute: (event) => {
    try {
      const itemName = event.itemName;
      const cmd = String(event.receivedCommand || "");
      if (cmd !== "ON") return;

      const parts = itemName.split("_");
      // A_IoT_Remove_<APPID>
      if (parts.length < 4) {
        LOGGER.warn("Cannot parse app_id from {}", itemName);
        return;
      }
      const appId = toInt(parts[3]);
      if (!appId) return;

      const body = JSON.stringify({ app_id: appId });

      LOGGER.info("Requesting /devices/remove for app_id {}", appId);
      const res = actions.HTTP.sendHttpPostRequest(
        `${BRIDGE_URL}/devices/remove`,
        "application/json",
        body,
        10000
      );
      LOGGER.debug("Remove response: {}", res);

      // reset button visually
      items.getItem(itemName).postUpdate("OFF");

    } catch (e) {
      LOGGER.warn("Remove rule error: {}", String(e));
    }
  }
});

// ---------------------------------------------------------------------------
// Rule 3 – Create device button → /devices/create
// ---------------------------------------------------------------------------

rules.JSRule({
  name: "IoT A: Create new device",
  triggers: [triggers.ItemCommandTrigger("IoT_CreateDevice")],
  execute: (event) => {
    try {
      const cmd = String(event.receivedCommand || "");
      if (cmd !== "ON") return;

      // simple unique device name based on timestamp
      const ts = new Date().getTime();
      const deviceName = `app_${ts}`;

      const payload = {
        device: deviceName,
        schema: ["state", "last_actor", "updated_at", "active"],
        peers: PEER_CONTROLLER_ADDRESS ? [PEER_CONTROLLER_ADDRESS] : []
      };

      const body = JSON.stringify(payload);
      LOGGER.info("Creating new device {} via /devices/create", deviceName);

      const res = actions.HTTP.sendHttpPostRequest(
        `${BRIDGE_URL}/devices/create`,
        "application/json",
        body,
        10000
      );
      LOGGER.debug("Create response: {}", res);

      // reset button
      items.getItem("IoT_CreateDevice").postUpdate("OFF");

    } catch (e) {
      LOGGER.warn("Create device rule error: {}", String(e));
    }
  }
});
