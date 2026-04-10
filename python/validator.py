import os
import json
import hmac
import hashlib
import logging
import time
import uuid
import requests

from datetime           import datetime, timezone
from dotenv             import load_dotenv
from pydantic           import BaseModel, Field, ValidationError, validator
from cachetools         import TTLCache
from typing             import Optional
import paho.mqtt.client as mqtt

# ────────────────────────────────────────
# LOAD .env FILE
# ────────────────────────────────────────
load_dotenv()

MQTT_BROKER             = os.getenv("MQTT_BROKER",             "broker.hivemq.com")
MQTT_PORT               = int(os.getenv("MQTT_PORT",           "1883"))
MQTT_API_KEY            = os.getenv("MQTT_API_KEY",            "")
MQTT_TOPIC_DATA         = os.getenv("MQTT_TOPIC_DATA",         "agri/sensor/data")
MQTT_TOPIC_HEARTBEAT    = os.getenv("MQTT_TOPIC_HEARTBEAT",    "agri/sensor/heartbeat")
HMAC_SECRET_KEY         = os.getenv("HMAC_SECRET_KEY",         "")
NODEJS_API_URL          = os.getenv("NODEJS_API_URL",          "http://localhost:3000/api/sensor")
NODEJS_API_KEY          = os.getenv("NODEJS_API_KEY",          "")
NONCE_TTL_SECONDS       = int(os.getenv("NONCE_TTL_SECONDS",   "300"))
HEARTBEAT_TIMEOUT       = int(os.getenv("HEARTBEAT_TIMEOUT_SECONDS", "60"))
BATTERY_WARNING_PERCENT = int(os.getenv("BATTERY_WARNING_PERCENT",   "20"))

# ────────────────────────────────────────
# LOGGING SETUP
# ────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s | %(levelname)s | %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("agri_validator")

def log_rejection(reason, device_id, raw_data):
    logger.warning(json.dumps({
        "event"     : "REJECTED",
        "reason"    : reason,
        "device_id" : device_id,
        "timestamp" : datetime.now(timezone.utc).isoformat(),
        "raw_data"  : raw_data
    }))

def log_accepted(device_id, reading_count, record_uuid):
    logger.info(json.dumps({
        "event"         : "ACCEPTED",
        "device_id"     : device_id,
        "reading_count" : reading_count,
        "record_uuid"   : record_uuid,
        "timestamp"     : datetime.now(timezone.utc).isoformat()
    }))



#  FEATURE 1 — SCHEMA VALIDATION WITH PYDANTIC
#  Defines exactly what a valid payload looks like
#  Wrong type, missing field, out of range → auto rejected
# ================================================================
class SensorPayload(BaseModel):
    device_id     : str           = Field(..., min_length=3, max_length=50)
    temperature   : float         = Field(..., ge=-10.0, le=60.0)
    soil_raw      : int           = Field(..., ge=500, le=4095)
    soil_percent  : int           = Field(..., ge=0, le=100)
    temp_valid    : bool
    soil_valid    : bool
    nonce         : str           = Field(..., min_length=5)
    timestamp     : int           = Field(..., gt=0)
    reading_count : int           = Field(..., ge=1)
    battery_level : Optional[int] = Field(None, ge=0, le=100)
    record_uuid   : Optional[str] = None

    @validator("device_id")
    def device_id_format(cls, v):
        if not v.startswith("AGRI_"):
            raise ValueError("device_id must start with AGRI_")
        return v

    @validator("temperature")
    def temp_not_nan(cls, v):
        if v != v:
            raise ValueError("temperature is NaN")
        return round(v, 2)

    class Config:
        extra = "ignore"


class HeartbeatPayload(BaseModel):
    device_id     : str           = Field(..., min_length=3)
    status        : str
    uptime_ms     : int           = Field(..., ge=0)
    readings_sent : int           = Field(..., ge=0)
    battery_level : Optional[int] = Field(None, ge=0, le=100)

    @validator("status")
    def status_alive(cls, v):
        if v != "alive":
            raise ValueError("status must be alive")
        return v

    class Config:
        extra = "ignore"


# ================================================================
#  FEATURE 3 — REPLAY DETECTION WITH TTL EVICTION
#  Same nonce within 5 minutes = replay attack = rejected
#  Old nonces expire automatically — no memory leak
# ================================================================
nonce_cache = TTLCache(maxsize=10000, ttl=NONCE_TTL_SECONDS)

def is_replay(nonce, device_id):
    key = f"{device_id}:{nonce}"
    if key in nonce_cache:
        return True
    nonce_cache[key] = True
    return False


# ================================================================
#  FEATURE 4 — DEDUPLICATION WITH UUID
#  Same device + same reading_count = duplicate = rejected
#  UUID assigned HERE by Python — not by ESP32
#  UUID becomes primary key on blockchain
# ================================================================
seen_readings = TTLCache(maxsize=5000, ttl=600)

def is_duplicate(device_id, reading_count):
    key = f"{device_id}:{reading_count}"
    if key in seen_readings:
        return True
    seen_readings[key] = True
    return False

def assign_uuid(payload):
    payload.record_uuid = str(uuid.uuid4())
    return payload


# ================================================================
#  FEATURE 2 — HMAC HASH VERIFICATION
def verify_hmac(payload_str, received_hmac):
    # Python recomputes the same hash using same secret key
    expected = hmac.new(
        key      = HMAC_SECRET_KEY.encode("utf-8"),
        msg      = payload_str.encode("utf-8"),
        digestmod= hashlib.sha256
    ).hexdigest()

    # compare_digest is timing-attack safe
    return hmac.compare_digest(expected, received_hmac)


# ================================================================
#  FEATURE 5 — TIMESTAMP EXPIRY CHECK
#  Rejects very old messages
#  Prevents delayed replay attacks
# ================================================================
def is_expired(timestamp_ms):
    if timestamp_ms <= 0:
        return True
    SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
    return timestamp_ms > SEVEN_DAYS_MS


# ================================================================
#  FEATURE 6 — HEARTBEAT MONITORING
#  Tracks when each device last sent alive ping
#  Logs error if device goes silent too long
# ================================================================
device_heartbeats = {}

def update_heartbeat(device_id):
    device_heartbeats[device_id] = time.time()

def check_missed_heartbeats():
    now = time.time()
    for device_id, last in list(device_heartbeats.items()):
        if now - last > HEARTBEAT_TIMEOUT:
            logger.error(json.dumps({
                "event"     : "HEARTBEAT_MISSED",
                "device_id" : device_id,
                "last_seen" : datetime.fromtimestamp(
                                last, tz=timezone.utc).isoformat()
            }))


# ================================================================
#  FEATURE 7 — BATTERY WARNING
#  Logs alert when battery below threshold
# ================================================================
def check_battery(device_id, battery_level):
    if battery_level is not None:
        if battery_level <= BATTERY_WARNING_PERCENT:
            logger.warning(json.dumps({
                "event"         : "BATTERY_LOW",
                "device_id"     : device_id,
                "battery_level" : battery_level
            }))


# ================================================================
#  FEATURE 11 — FORWARD TO NODE.JS
#  Sends verified + UUID stamped data to Node.js
#  Node.js stores on Hyperledger Fabric blockchain
# ================================================================
def forward_to_nodejs(payload):
    try:
        response = requests.post(
            url     = NODEJS_API_URL,
            json    = payload.dict(),
            headers = {
                "Content-Type" : "application/json",
                "x-api-key"    : NODEJS_API_KEY
            },
            timeout = 10
        )
        if response.status_code == 200:
            logger.info(json.dumps({
                "event"       : "FORWARDED",
                "device_id"   : payload.device_id,
                "record_uuid" : payload.record_uuid
            }))
        else:
            logger.error(json.dumps({
                "event"  : "NODEJS_ERROR",
                "status" : response.status_code
            }))
    except requests.exceptions.RequestException as e:
        logger.error(json.dumps({
            "event" : "NODEJS_UNREACHABLE",
            "error" : str(e)
        }))


# ================================================================
#  MAIN VALIDATION PIPELINE
#  Every MQTT message goes through all steps in order
#  First failure = rejected with reason logged
# ================================================================
def validate_and_process(raw_message):

    # STEP 1: Parse JSON
    try:
        # ESP32 sends: {"payload":{...},"hmac":"abc123..."}
        # We split payload from hmac first
        outer = json.loads(raw_message)
        data  = outer.get("payload", outer)
        if isinstance(data, str):
            data = json.loads(data)
        received_hmac = outer.get("hmac", data.pop("hmac", None))
    except (json.JSONDecodeError, Exception):
        logger.warning("[REJECTED] Invalid JSON")
        return

    device_id = data.get("device_id", "UNKNOWN")

    # STEP 2: Check HMAC exists
    if not received_hmac:
        log_rejection("MISSING_HMAC", device_id, data)
        return

    # Build clean payload string for HMAC verification
    payload_str = json.dumps(data, separators=(',', ':'), sort_keys=True)

    # STEP 3: Schema validation
    try:
        payload = SensorPayload(**data)
    except ValidationError as e:
        log_rejection("SCHEMA_FAILED: " + str(e), device_id, data)
        return

    # STEP 4: HMAC verification ← HASH IS CHECKED HERE
    if not verify_hmac(payload_str, received_hmac):
        log_rejection("HMAC_INVALID", device_id, data)
        return

    # STEP 5: Timestamp check
    if is_expired(payload.timestamp):
        log_rejection("TIMESTAMP_EXPIRED", device_id, data)
        return

    # STEP 6: Replay detection
    if is_replay(payload.nonce, payload.device_id):
        log_rejection("REPLAY_DETECTED", device_id, data)
        return

    # STEP 7: Deduplication
    if is_duplicate(payload.device_id, payload.reading_count):
        log_rejection("DUPLICATE", device_id, data)
        return

    # STEP 8: Battery check
    check_battery(payload.device_id, payload.battery_level)

    # STEP 9: Assign UUID
    payload = assign_uuid(payload)

    # STEP 10: All checks passed → forward to Node.js
    log_accepted(payload.device_id, payload.reading_count, payload.record_uuid)
    forward_to_nodejs(payload)


# ================================================================
#  MQTT HANDLERS — FEATURE 9 TOPIC SEPARATION
# ================================================================
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("[MQTT] Connected to broker")
        client.subscribe(MQTT_TOPIC_DATA)
        client.subscribe(MQTT_TOPIC_HEARTBEAT)
        logger.info(f"[MQTT] Listening on: {MQTT_TOPIC_DATA}")
        logger.info(f"[MQTT] Listening on: {MQTT_TOPIC_HEARTBEAT}")
    else:
        logger.error(f"[MQTT] Failed — code {rc}")

def on_disconnect(client, userdata, rc):
    logger.warning(f"[MQTT] Disconnected — code {rc}")

def on_message(client, userdata, msg):
    topic   = msg.topic
    message = msg.payload.decode("utf-8")
    logger.info(f"[MQTT] Received on: {topic}")

    if topic == MQTT_TOPIC_DATA:
        validate_and_process(message)

    elif topic == MQTT_TOPIC_HEARTBEAT:
        try:
            hb_data = json.loads(message)
            hb      = HeartbeatPayload(**hb_data)
            update_heartbeat(hb.device_id)
            logger.info(json.dumps({
                "event"     : "HEARTBEAT",
                "device_id" : hb.device_id,
                "uptime_ms" : hb.uptime_ms
            }))
            check_battery(hb.device_id, hb.battery_level)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[HEARTBEAT] Invalid: {e}")


# ================================================================
#  ENTRY POINT
# ================================================================
def main():
    logger.info("============================================")
    logger.info("  Smart Agriculture Validator — Starting   ")
    logger.info("============================================")
    logger.info(f"Broker    : {MQTT_BROKER}:{MQTT_PORT}")
    logger.info(f"Data topic: {MQTT_TOPIC_DATA}")
    logger.info(f"Nonce TTL : {NONCE_TTL_SECONDS}s")
    logger.info("============================================")

    client = mqtt.Client(client_id="agri_validator_001")
    if MQTT_API_KEY:
        client.username_pw_set(MQTT_API_KEY, "")
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    client.loop_start()

    logger.info("[SYSTEM] Running. Waiting for messages...")

    last_check = time.time()
    while True:
        time.sleep(5)
        if time.time() - last_check > 30:
            check_missed_heartbeats()
            last_check = time.time()

if __name__ == "__main__":
    main()