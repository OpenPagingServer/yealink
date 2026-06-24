import os
import threading
import time
import urllib3
import xml.sax.saxutils as saxutils
from datetime import datetime
from pathlib import Path

import pymysql
import requests
from dotenv import load_dotenv
from requests.auth import HTTPDigestAuth
from active_broadcast_store import fetch_active_broadcast
from broadcasts import legacy_type
from endpoints import MODULE_LOG_DIR, connect_endpoint_ipc

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent.parent / ".env"
load_dotenv(ENV_PATH)

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"
LOG_FILE = MODULE_LOG_DIR / "yealink" / "module.log"
PUSH_AFTER_AUDIO_DELAY = float(
    os.getenv("YEALINK_PUSH_AFTER_AUDIO_DELAY", os.getenv("POLYCOM_PUSH_AFTER_AUDIO_DELAY", "0.6"))
)
PUSH_TABLE = "endpoints-output-yealink-push"


def debug_log(message):
    if not DEBUG:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def send_ready_signal(module_name, stream_id):
    try:
        with connect_endpoint_ipc(timeout=1) as sock:
            sock.sendall(f"READY {module_name} {stream_id}\n".encode("utf-8"))
            sock.recv(16)
        debug_log(f"READY sent module={module_name} stream={stream_id}")
    except Exception as exc:
        debug_log(f"READY failed module={module_name} stream={stream_id} error={exc}")


def fetch_message(message_id):
    row = fetch_active_broadcast(message_id)
    if row:
        row["name"] = row.get("name") or "Broadcast"
        row["type"] = legacy_type(row.get("type"))
        return row
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, shortmessage, longmessage, type, color, icon FROM messages WHERE messageid=%s", (message_id,))
            row = cur.fetchone()
            if row:
                row["type"] = legacy_type(row.get("type"))
            return row
    finally:
        conn.close()


def parse_targets(targets):
    target_info = {
        "push_ips": [],
        "all": False,
    }
    for target in targets:
        token = str(target).strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered == "all":
            target_info["all"] = True
            continue
        if lowered.startswith("push-"):
            value = token[5:].strip()
            if value and value not in target_info["push_ips"]:
                target_info["push_ips"].append(value)
    return target_info


def fetch_push_targets(target_info):
    conn = db()
    try:
        with conn.cursor() as cur:
            if target_info["all"]:
                cur.execute(
                    f"SELECT ipv4, status, username, password "
                    f"FROM `{PUSH_TABLE}` "
                    "WHERE ipv4 IS NOT NULL AND ipv4 <> ''"
                )
                rows = cur.fetchall()
            elif target_info["push_ips"]:
                placeholders = ",".join(["%s"] * len(target_info["push_ips"]))
                cur.execute(
                    f"SELECT ipv4, status, username, password "
                    f"FROM `{PUSH_TABLE}` "
                    f"WHERE ipv4 IN ({placeholders})",
                    tuple(target_info["push_ips"]),
                )
                rows = cur.fetchall()
            else:
                rows = []
    finally:
        conn.close()
    return rows


def build_push_xml_for_device(page_title, short_message, long_message):
    safe_title = saxutils.escape("" if page_title is None else str(page_title))
    safe_short = "" if short_message is None else str(short_message).strip()
    safe_long = "" if long_message is None else str(long_message).strip()
    text_parts = []
    if safe_short:
        text_parts.append(safe_short)
    if safe_long:
        text_parts.append(safe_long)
    safe_text = saxutils.escape("\n\n".join(text_parts))
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<YealinkIPPhoneTextScreen Beep=\"yes\">"
        f"<Title>{safe_title}</Title>"
        f"<Text>{safe_text}</Text>"
        "</YealinkIPPhoneTextScreen>"
    )


def push_to_device(device, xml_data, verify=False):
    ip = device.get("ipv4")
    username = device.get("username")
    password = device.get("password")
    if not ip or not username or not password:
        return False
    try:
        response = requests.post(
            f"http://{ip}/servlet?push=xml",
            data=f"xml={xml_data}".encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            auth=HTTPDigestAuth(username, password),
            timeout=5,
            verify=verify,
        )
        preview = (response.text or "")[:200].replace("\r", " ").replace("\n", " ")
        debug_log(f"PUSH {ip} status={response.status_code} body={preview}")
        return response.status_code == 200
    except requests.exceptions.RequestException as exc:
        debug_log(f"PUSH {ip} failed error={exc}")
        return False


def push_text_parallel(jobs):
    threads = []
    for device, xml_data in jobs:
        thread = threading.Thread(target=push_to_device, args=(device, xml_data), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


def delayed_push_text(jobs, delay_seconds):
    if not jobs:
        return

    def worker():
        debug_log(f"delayed_push_text sleeping delay={delay_seconds} devices={[device.get('ipv4') for device, _ in jobs]}")
        time.sleep(delay_seconds)
        push_text_parallel(jobs)

    threading.Thread(target=worker, daemon=True).start()


def handle_dispatch(action, stream_id, message_id, targets):
    normalized_targets = []
    for target in targets:
        token = str(target).strip()
        if token and token not in normalized_targets:
            normalized_targets.append(token)
    if not normalized_targets:
        if action == "prepare_audio":
            send_ready_signal("yealink", stream_id)
        return
    debug_log(f"handle_dispatch action={action} stream={stream_id} msg={message_id} targets={normalized_targets}")
    message = fetch_message(message_id)
    if not message:
        if action == "prepare_audio":
            send_ready_signal("yealink", stream_id)
        debug_log(f"message_not_found msg={message_id}")
        return
    target_info = parse_targets(normalized_targets)
    push_targets = [
        device
        for device in fetch_push_targets(target_info)
        if device.get("ipv4") and device.get("status") in ("Unchecked", "Online")
    ]
    msg_type = message.get("type", "text+audio")
    name = message.get("name", "")
    shortmessage = message.get("shortmessage", "") or ""
    longmessage = message.get("longmessage", "") or ""
    debug_log(
        f"push_targets={[(device.get('ipv4'), device.get('status')) for device in push_targets]} "
        f"msg_type={msg_type}"
    )
    push_jobs = []
    for device in push_targets:
        push_jobs.append((device, build_push_xml_for_device(name, shortmessage, longmessage)))
    if msg_type == "text" and push_jobs:
        push_text_parallel(push_jobs)
    elif msg_type == "text+audio" and action != "prepare_audio" and push_jobs:
        push_text_parallel(push_jobs)
    if action == "prepare_audio":
        if msg_type == "text+audio" and push_jobs:
            delayed_push_text(push_jobs, PUSH_AFTER_AUDIO_DELAY)
        send_ready_signal("yealink", stream_id)


def handle_api(command_string):
    parts = str(command_string).strip().split()
    if len(parts) < 4:
        return
    handle_dispatch(parts[0], parts[2], parts[3], [parts[1]])


def receive_audio(chunk, stream_id):
    debug_log(f"receive_audio ignored stream={stream_id} bytes={len(chunk)}")


def end_stream(stream_id):
    debug_log(f"end_stream ignored stream={stream_id}")
