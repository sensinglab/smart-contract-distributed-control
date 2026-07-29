#!/usr/bin/env python3
import os
import json
import time
import threading
import base64
from typing import Dict, Any, List, Optional

import logging
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from dotenv import load_dotenv
from algosdk.v2client import algod
from algosdk import mnemonic, transaction, encoding
from algosdk import account

from pyteal import *

# =========================
# Logging
# =========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] iot-bridge-b: %(message)s",
)
log = logging.getLogger("iot-bridge-b")

# =========================
# Load environment
# =========================

load_dotenv("/opt/iot-bridge/config/.env")

ALGOD_ADDRESS = os.environ.get("ALGOD_ADDRESS", "http://127.0.0.1:8080")
ALGOD_TOKEN = os.environ.get("ALGOD_TOKEN", "")

CONTROLLER_MNEMONIC = os.environ.get("CONTROLLER_MNEMONIC", "")
BUILDING_ID = os.environ.get("BUILDING_ID", "B")

USE_INDEXER = os.environ.get("USE_INDEXER", "false").lower() == "true"

OPENHAB_BASE = os.environ.get("OPENHAB_BASE", "https://127.0.0.1:8444")
BRIDGE_HTTP_PORT = int(os.environ.get("BRIDGE_HTTP_PORT", "8787"))

STATE_DB_PATH = os.environ.get("STATE_DB", "/opt/iot-bridge/data/state.json")
LAST_ROUND_FILE = os.environ.get("LAST_ROUND_FILE", "/opt/iot-bridge/data/last_round")

ANNOUNCE_AMOUNT_MICROALGO = int(os.environ.get("ANNOUNCE_AMOUNT_MICROALGO", "1000"))

DATA_DIR = "/opt/iot-bridge/data"
REGISTRY_PATH = os.path.join(DATA_DIR, "registry.json")
EVENTS_PATH = os.path.join(DATA_DIR, "events.json")

TRUSTED_OWNER_ADDRESS = os.environ.get("PEER_CONTROLLER", "").strip()

os.makedirs(DATA_DIR, exist_ok=True)

# =========================
# Simple JSON file helper
# =========================

class JsonFile:
    def __init__(self, path: str, default_value: Any):
        self.path = path
        self.default_value = default_value
        self.lock = threading.Lock()

    def load(self) -> Any:
        with self.lock:
            if not os.path.exists(self.path):
                return self.default_value
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except Exception:
                return self.default_value

    def save(self, data: Any):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)


registry_file = JsonFile(
    REGISTRY_PATH,
    {"watched_apps": {}, "last_seen_round": 0, "processed_txids": []}
)
state_file = JsonFile(STATE_DB_PATH, {})
events_file = JsonFile(EVENTS_PATH, [])

# =========================
# Device app contract (PyTeal)
# =========================

def build_device_approval_teal() -> str:
    controller_addr = os.environ.get("CONTROLLER_ADDRESS")
    peer_controller = os.environ.get("PEER_CONTROLLER", "")

    if not controller_addr:
        raise Exception("CONTROLLER_ADDRESS not set in environment")

    ctrlA = Addr(controller_addr)
    allowed = Txn.sender() == ctrlA

    if peer_controller:
        ctrlB = Addr(peer_controller)
        allowed = Or(allowed, Txn.sender() == ctrlB)

    g_state = Bytes("state")
    g_last_actor = Bytes("last_actor")
    g_updated_at = Bytes("updated_at")
    g_active = Bytes("active")

    method = Txn.application_args[0]

    on_create = Seq(
        App.globalPut(g_state, Bytes("off")),
        App.globalPut(g_active, Bytes("true")),
        App.globalPut(g_last_actor, Txn.sender()),
        App.globalPut(g_updated_at, Global.round()),
        Approve(),
    )

    guard = Seq(
        Assert(allowed),
        Assert(App.globalGet(g_active) == Bytes("true")),
    )

    do_set_on = Seq(
        guard,
        App.globalPut(g_state, Bytes("on")),
        App.globalPut(g_last_actor, Txn.sender()),
        App.globalPut(g_updated_at, Global.round()),
        Approve(),
    )

    do_set_off = Seq(
        guard,
        App.globalPut(g_state, Bytes("off")),
        App.globalPut(g_last_actor, Txn.sender()),
        App.globalPut(g_updated_at, Global.round()),
        Approve(),
    )

    curr = App.globalGet(g_state)
    next_state = If(curr == Bytes("on")).Then(Bytes("off")).Else(Bytes("on"))

    do_toggle = Seq(
        guard,
        App.globalPut(g_state, next_state),
        App.globalPut(g_last_actor, Txn.sender()),
        App.globalPut(g_updated_at, Global.round()),
        Approve(),
    )

    handle_noop = Cond(
        [Txn.application_args.length() == Int(0), Approve()],
        [method == Bytes("toggle"), do_toggle],
        [method == Bytes("set:on"), do_set_on],
        [method == Bytes("set:off"), do_set_off],
        [Int(1), Reject()],
    )

    program = Cond(
        [Txn.application_id() == Int(0), on_create],
        [Txn.on_completion() == OnComplete.NoOp, handle_noop],
        [Int(1), Reject()],
    )

    return compileTeal(program, mode=Mode.Application, version=6)


def build_device_clear_teal() -> str:
    return compileTeal(Approve(), mode=Mode.Application, version=6)

# =========================
# Algorand client wrapper
# =========================

class AlgoClient:
    def __init__(self):
        if not CONTROLLER_MNEMONIC:
            raise RuntimeError("CONTROLLER_MNEMONIC not set")
        self.algod = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)

        self.controller_private_key = mnemonic.to_private_key(CONTROLLER_MNEMONIC)
        self.controller_address = account.address_from_private_key(self.controller_private_key)

    def suggested_params(self):
        return self.algod.suggested_params()

    def wait_for_confirmation(self, txid: str, timeout: int = 30) -> Dict[str, Any]:
        start = time.time()
        while True:
            try:
                pending = self.algod.pending_transaction_info(txid)
                if pending.get("confirmed-round", 0) > 0:
                    return pending
            except Exception:
                pass
            if time.time() - start > timeout:
                raise TimeoutError(f"Tx {txid} not confirmed after {timeout}s")
            time.sleep(1)

    @staticmethod
    def approval_teal() -> str:
        return build_device_approval_teal()

    @staticmethod
    def clear_teal() -> str:
        return build_device_clear_teal()

    def compile_teal(self, source: str) -> bytes:
        compiled = self.algod.compile(source)
        return base64.b64decode(compiled["result"])

    def create_device_app(self, device_type: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        sp = self.suggested_params()
        approval = self.compile_teal(self.approval_teal())
        clear = self.compile_teal(self.clear_teal())

        global_schema = transaction.StateSchema(num_uints=4, num_byte_slices=4)
        local_schema = transaction.StateSchema(num_uints=0, num_byte_slices=0)

        app_args = [
            device_type.encode("utf-8"),
            json.dumps(metadata).encode("utf-8"),
        ]

        txn = transaction.ApplicationCreateTxn(
            sender=self.controller_address,
            sp=sp,
            on_complete=transaction.OnComplete.NoOpOC.real,
            approval_program=approval,
            clear_program=clear,
            global_schema=global_schema,
            local_schema=local_schema,
            app_args=app_args,
        )

        signed = txn.sign(self.controller_private_key)
        txid = self.algod.send_transaction(signed)
        pending = self.wait_for_confirmation(txid)
        app_id = pending.get("application-index")
        if not app_id:
            raise RuntimeError("ApplicationCreate did not return app_id")

        return {"txid": txid, "pending": pending, "app_id": app_id}

    def app_call(self, app_id: int, args: List[str]) -> Dict[str, Any]:
        sp = self.suggested_params()
        app_args = [a.encode("utf-8") for a in args]

        txn = transaction.ApplicationNoOpTxn(
            sender=self.controller_address,
            sp=sp,
            index=app_id,
            app_args=app_args,
        )

        signed = txn.sign(self.controller_private_key)
        txid = self.algod.send_transaction(signed)
        pending = self.wait_for_confirmation(txid)
        return {"txid": txid, "pending": pending}

    def payment_with_note(self, note_obj: Dict[str, Any], receiver: Optional[str] = None) -> Dict[str, Any]:
        sp = self.suggested_params()
        note_bytes = json.dumps(note_obj).encode("utf-8")

        recv = receiver or self.controller_address

        txn = transaction.PaymentTxn(
            sender=self.controller_address,
            sp=sp,
            receiver=recv,
            amt=ANNOUNCE_AMOUNT_MICROALGO,
            note=note_bytes,
        )

        signed = txn.sign(self.controller_private_key)
        txid = self.algod.send_transaction(signed)
        pending = self.wait_for_confirmation(txid)
        return {"txid": txid, "pending": pending}

    def get_app_info(self, app_id: int) -> Dict[str, Any]:
        return self.algod.application_info(app_id)

    def get_app_global_state(self, app_id: int) -> Dict[str, Any]:
        info = self.algod.application_info(app_id)
        gstate = info["params"].get("global-state", [])
        decoded: Dict[str, Any] = {}

        for kv in gstate:
            key = base64.b64decode(kv["key"]).decode("utf-8")
            val = kv["value"]

            if val["type"] == 1:
                raw = base64.b64decode(val["bytes"])

                if key == "last_actor":
                    decoded[key] = encoding.encode_address(raw)
                else:
                    decoded[key] = raw.decode("utf-8", errors="ignore")
            else:
                decoded[key] = val["uint"]

        return decoded

    def status(self) -> Dict[str, Any]:
        return self.algod.status()

    def block_info(self, round_num: int) -> Dict[str, Any]:
        return self.algod.block_info(round_num)


algo = AlgoClient()

# =========================
# Registry / state / events
# =========================

def get_registry() -> Dict[str, Any]:
    return registry_file.load()


def save_registry(reg: Dict[str, Any]):
    registry_file.save(reg)


def get_state_cache() -> Dict[str, Any]:
    return state_file.load()


def save_state_cache(st: Dict[str, Any]):
    state_file.save(st)


def append_event(event: Dict[str, Any]):
    events = events_file.load()
    events.append(event)
    events_file.save(events)


def get_events_since(since_round: int) -> List[Dict[str, Any]]:
    events = events_file.load()
    return [e for e in events if e.get("round", 0) > since_round]

# =========================
# OpenHAB integration (unused on B, but kept for symmetry)
# =========================

def update_openhab_item(item_name: str, value: str):
    url = f"{OPENHAB_BASE}/rest/items/{item_name}"
    headers = {
        "Content-Type": "text/plain",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(url, data=value, headers=headers, verify=False, timeout=5)
        resp.raise_for_status()
        log.info("[openhab] Updated %s -> %s", item_name, value)
    except Exception as e:
        log.warning("[openhab] Failed to update %s: %s", item_name, e)

def trigger_openhab_sync(reason: str = ""):
    item_name = os.environ.get("OPENHAB_SYNC_ITEM", "IoT_SyncTrigger")
    url = f"{OPENHAB_BASE}/rest/items/{item_name}"
    headers = {
        "Content-Type": "text/plain",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(
            url,
            data="ON",
            headers=headers,
            verify=False,
            timeout=5
        )
        resp.raise_for_status()
        log.info("[openhab] Triggered sync item %s reason=%s", item_name, reason)
    except Exception as e:
        log.warning("[openhab] Failed to trigger sync item %s: %s", item_name, e)
# =========================
# Watcher: blockchain → state
# =========================

def process_payment_tx(tx: Dict[str, Any], round_num: int):
    txn = tx.get("txn", {})

    note_b64 = txn.get("note")
    if not note_b64:
        return

    try:
        note_bytes = base64.b64decode(note_b64)
        note_json = json.loads(note_bytes.decode("utf-8"))
    except Exception:
        return

    ev_type = note_json.get("type")
    if ev_type not in ("device_announce", "device_remove"):
        return

    version = note_json.get("version")
    if version != 1:
        log.warning("[announce] reject: unsupported version=%s round=%s", version, round_num)
        return

    sender = txn.get("snd", "").strip()

    if not TRUSTED_OWNER_ADDRESS:
        log.warning("[announce] reject: TRUSTED_OWNER_ADDRESS not configured")
        return

    if sender != TRUSTED_OWNER_ADDRESS:
        log.warning("[announce] reject: untrusted sender=%s round=%s", sender, round_num)
        return

    reg = get_registry()
    watched = reg.get("watched_apps", {})

    if ev_type == "device_announce":
        app_id_raw = note_json.get("app_id")
        if app_id_raw is None:
            log.warning("[announce] reject: missing app_id round=%s sender=%s", round_num, sender)
            return

        app_id = str(app_id_raw)

        try:
            app_info = algo.get_app_info(int(app_id))
            app_creator = app_info.get("params", {}).get("creator", "").strip()
        except Exception as e:
            log.warning(
                "[announce] reject: failed to fetch app info for app_id=%s round=%s sender=%s error=%s",
                app_id, round_num, sender, e
            )
            return

        if app_creator != TRUSTED_OWNER_ADDRESS:
            log.warning(
                "[announce] reject: creator mismatch app_id=%s creator=%s expected=%s round=%s sender=%s",
                app_id, app_creator, TRUSTED_OWNER_ADDRESS, round_num, sender
            )
            return

        device = note_json.get("device")
        if not device:
            log.warning("[announce] reject: missing device round=%s sender=%s", round_num, sender)
            return

        owner_building = note_json.get("owner_building")
        if not owner_building:
            log.warning("[announce] reject: missing owner_building round=%s sender=%s", round_num, sender)
            return

        peer_controller = note_json.get("peer_controller", "")
        if not peer_controller:
            log.warning("[announce] reject: missing peer_controller round=%s sender=%s", round_num, sender)
            return

        if app_id not in watched:
            watched[app_id] = {
                "device": device,
                "owner_building": owner_building,
                "active": True,
                "peer_controller": peer_controller,
                "state": {
                    "state": "off",
                    "last_actor": "",
                    "updated_at": 0,
                    "active": "true",
                },
            }
            log.info(
                "[announce] new app_id=%s device=%s owner=%s round=%s sender=%s",
                app_id, device, owner_building, round_num, sender
            )
        else:
            log.info(
                "[announce] app_id=%s already in registry (round=%s sender=%s)",
                app_id, round_num, sender
            )

        append_event({
            "round": round_num,
            "type": "device_announce",
            "app_id": int(app_id),
            "device": device,
            "owner_building": owner_building,
        })

    elif ev_type == "device_remove":
        app_id_raw = note_json.get("app_id")
        if app_id_raw is None:
            log.warning("[remove] reject: missing app_id round=%s sender=%s", round_num, sender)
            return

        app_id = str(app_id_raw)

        owner_building = note_json.get("owner_building")
        if not owner_building:
            log.warning("[remove] reject: missing owner_building round=%s sender=%s", round_num, sender)
            return

        peer_controller = note_json.get("peer_controller", "")
        if not peer_controller:
            log.warning("[remove] reject: missing peer_controller round=%s sender=%s", round_num, sender)
            return

        if app_id in watched:
            log.info(
                "[remove] device_remove for app_id=%s - removing from registry (round=%s sender=%s)",
                app_id, round_num, sender
            )
            watched.pop(app_id, None)
        else:
            log.warning(
                "[remove] device_remove for unknown app_id=%s (round=%s sender=%s)",
                app_id, round_num, sender
            )

        append_event({
            "round": round_num,
            "type": "device_remove",
            "app_id": int(app_id),
        })

    reg["watched_apps"] = watched
    reg["last_seen_round"] = max(reg.get("last_seen_round", 0), round_num)
    save_registry(reg)

    try:
        with open(LAST_ROUND_FILE, "w") as f:
            f.write(str(reg["last_seen_round"]))
    except Exception:
        pass

    trigger_openhab_sync(ev_type)

def refresh_app_state(app_id: int, round_num: int):
    global_state = algo.get_app_global_state(app_id)

    st_cache = get_state_cache()
    st_cache[str(app_id)] = {
        "global_state": global_state,
        "updated_at": round_num,
    }
    save_state_cache(st_cache)

    reg = get_registry()
    app_key = str(app_id)
    if app_key in reg.get("watched_apps", {}):
        reg["watched_apps"][app_key]["state"].update({
            "state": global_state.get("state", reg["watched_apps"][app_key]["state"].get("state", "unknown")),
            "last_actor": global_state.get("last_actor", reg["watched_apps"][app_key]["state"].get("last_actor", "")),
            "updated_at": round_num,
            "active": "true" if reg["watched_apps"][app_key].get("active", True) else "false",
        })
        save_registry(reg)

    append_event({
        "round": round_num,
        "type": "app_state_change",
        "app_id": app_id,
        "global_state": global_state,
    })

    log.info(
        "[state] app_id=%s state=%s last_actor=%s round=%s",
        app_id,
        global_state.get("state"),
        global_state.get("last_actor"),
        round_num,
    )

    trigger_openhab_sync(f"app_state_change:{app_id}")

def process_block(round_num: int):
    blk = algo.block_info(round_num)
    txns = blk.get("block", {}).get("txns", [])
    reg = get_registry()
    watched_ids = set(int(k) for k in reg.get("watched_apps", {}).keys())

    for wrapped_tx in txns:
        tx = wrapped_tx.get("txn", {})
        ttype = tx.get("type")

        if ttype == "pay":
            if tx.get("rcv") == algo.controller_address:
                process_payment_tx(wrapped_tx, round_num)

        if ttype == "appl":
            app_id = tx.get("apid")
            if app_id and app_id in watched_ids:
                refresh_app_state(app_id, round_num)


def watcher_loop():
    log.info("[watcher] starting watcher loop")
    while True:
        try:
            reg = get_registry()
            last_seen = reg.get("last_seen_round", 0)

            if last_seen == 0:
                if os.path.exists(LAST_ROUND_FILE):
                    try:
                        with open(LAST_ROUND_FILE, "r") as f:
                            last_seen = int(f.read().strip() or "0")
                    except Exception:
                        last_seen = 0
                if last_seen == 0:
                    status = algo.status()
                    last_seen = status.get("last-round", 0)
                reg["last_seen_round"] = last_seen
                save_registry(reg)

            status = algo.status()
            current_round = status["last-round"]

            if last_seen < current_round:
                for r in range(last_seen + 1, current_round + 1):
                    process_block(r)
                    reg = get_registry()
                    reg["last_seen_round"] = r
                    save_registry(reg)
                    with open(LAST_ROUND_FILE, "w") as f:
                        f.write(str(r))

                    log.info("[watcher] processed round %s", r)

            time.sleep(2)
        except Exception as e:
            log.exception("[watcher] error: %s", e)
            time.sleep(5)

# =========================
# HTTP API
# =========================

app = FastAPI(title="IoT-Algorand Bridge", version="0.1.0")


class CreateDeviceRequest(BaseModel):
    device_type: Optional[str] = None
    metadata: Dict[str, Any] = {}

    device: Optional[str] = None
    schema: Optional[List[str]] = None
    peers: Optional[List[str]] = None


class RemoveDeviceRequest(BaseModel):
    app_id: int


class AppCallRequest(BaseModel):
    app_id: int
    arguments: List[str] = []
    method: Optional[str] = None


@app.post("/devices/create")
def devices_create(req: CreateDeviceRequest):
    try:
        device_name = req.device or req.device_type
        if not device_name:
            raise ValueError("Must provide 'device' or 'device_type'")

        schema = req.schema or ["state", "last_actor", "updated_at", "active"]

        default_peer = os.environ.get("PEER_CONTROLLER", "")
        peers = req.peers or ([default_peer] if default_peer else [])

        log.info("[create] (B) request device=%s schema=%s peers=%s", device_name, schema, peers)

        metadata = req.metadata or {}
        metadata.setdefault("schema", schema)
        if peers:
            metadata.setdefault("peers", peers)

        result = algo.create_device_app(device_name, metadata)
        app_id = result["app_id"]

        reg = get_registry()
        watched = reg.get("watched_apps", {})
        watched[str(app_id)] = {
            "device": device_name,
            "owner_building": BUILDING_ID,
            "active": True,
            "peer_controller": default_peer,
            "schema": schema,
            "peers": peers,
            "state": {
                "state": "off",
                "last_actor": algo.controller_address,
                "updated_at": 0,
                "active": "true",
            },
        }
        reg["watched_apps"] = watched
        save_registry(reg)

        announce_receiver = peers[0] if peers else algo.controller_address

        note = {
            "version": 1,
            "type": "device_announce",
            "app_id": app_id,
            "device": device_name,
            "owner_building": BUILDING_ID,
            "peer_controller": announce_receiver,
            "schema": schema,
            "peers": peers,
            "ts": int(time.time()),
        }

        announce_result = algo.payment_with_note(note_obj=note, receiver=announce_receiver)

        log.info("[create] (B) DONE app_id=%s", app_id)

        return {
            "status": "ok",
            "app_id": app_id,
            "tx_create": result["txid"],
            "tx_announce": announce_result["txid"],
        }
    except Exception as e:
        log.exception("[create] (B) failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/devices/remove")
def devices_remove(req: RemoveDeviceRequest):
    try:
        reg = get_registry()
        watched = reg.get("watched_apps", {})
        key = str(req.app_id)

        peers = []
        default_peer = os.environ.get("PEER_CONTROLLER", "")
        if key in watched:
            peers = watched[key].get("peers", [])
            watched[key]["active"] = False
            watched[key]["state"]["active"] = "false"
            reg["watched_apps"] = watched
            save_registry(reg)

        receiver = peers[0] if peers else (default_peer or algo.controller_address)

        note = {
            "version": 1,
            "type": "device_remove",
            "app_id": req.app_id,
            "owner_building": BUILDING_ID,
            "peer_controller": receiver,
            "ts": int(time.time()),
        }

        remove_result = algo.payment_with_note(note_obj=note, receiver=receiver)

        log.info("[remove] (HTTP) app_id=%s tx=%s", req.app_id, remove_result["txid"])

        return {
            "status": "ok",
            "app_id": req.app_id,
            "tx_remove": remove_result["txid"],
        }
    except Exception as e:
        log.exception("[remove] (HTTP) failed for app_id=%s: %s", req.app_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/appcall")
def app_call(req: AppCallRequest):
    start = time.monotonic()
    try:
        if req.method:
            args = [req.method]
        elif req.arguments:
            args = req.arguments
        else:
            args = []

        log.info("[appcall] app_id=%s args=%s", req.app_id, args)

        result = algo.app_call(req.app_id, args)
        elapsed = time.monotonic() - start

        log.info("[appcall] DONE app_id=%s tx=%s took=%.3fs",
                 req.app_id, result["txid"], elapsed)

        return {
            "status": "ok",
            "app_id": req.app_id,
            "txid": result["txid"],
        }
    except Exception as e:
        log.exception("[appcall] failed for app_id=%s: %s", req.app_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/state")
def get_state(app_id: int):
    st_cache = get_state_cache()
    reg = get_registry()
    last_round = reg.get("last_seen_round", 0)
    app_state = st_cache.get(str(app_id))
    if not app_state:
        raise HTTPException(status_code=404, detail="No cached state for app_id")
    return {
        "app_id": app_id,
        "global_state": app_state["global_state"],
        "updated_at": app_state["updated_at"],
        "last_seen_round": last_round,
    }


@app.get("/events")
def get_events(since: int = 0):
    evs = get_events_since(since)
    return {"events": evs}


@app.get("/health")
def health():
    try:
        status = algo.status()
        reg = get_registry()
        return {
            "ok": True,
            "openhab_base": OPENHAB_BASE,
            "port": BRIDGE_HTTP_PORT,
            "state_db_exists": os.path.exists(STATE_DB_PATH),
            "last_round_exists": os.path.exists(LAST_ROUND_FILE),
            "algod": {
                "last_round": status.get("last-round"),
                "time_since_last_round": status.get("time-since-last-round"),
            },
            "last_seen_round": reg.get("last_seen_round", 0),
            "watched_apps_count": len(reg.get("watched_apps", {})),
            "controller_address": algo.controller_address,
            "building_id": BUILDING_ID,
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "detail": str(e)},
        )

# =========================
# Main
# =========================

def main():
    reg = get_registry()
    if reg.get("last_seen_round", 0) == 0:
        try:
            if os.path.exists(LAST_ROUND_FILE):
                with open(LAST_ROUND_FILE, "r") as f:
                    reg["last_seen_round"] = int(f.read().strip() or "0")
            if reg["last_seen_round"] == 0:
                status = algo.status()
                reg["last_seen_round"] = status.get("last-round", 0)
            save_registry(reg)
        except Exception:
            pass

    t = threading.Thread(target=watcher_loop, daemon=True)
    t.start()

    uvicorn.run(app, host="127.0.0.1", port=BRIDGE_HTTP_PORT)


if __name__ == "__main__":
    main()
