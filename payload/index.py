import os
import sys
import time
import threading
import subprocess
import importlib.util
import pymysql
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PUSH_TABLE = "endpoints-output-yealink-push"


def load_message_send():
    module_name = "yealink_message_send_runtime"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    module_path = BASE_DIR / "message_send.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


message_send = load_message_send()


def load_page_handler():
    module_path = BASE_DIR / "page_handler.py"
    spec = importlib.util.spec_from_file_location("yealink_page_handler", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


page_handler = load_page_handler()

ENV_PATH = BASE_DIR.parent.parent / ".env"
load_dotenv(ENV_PATH)

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

core = None
running = False
thread = None
INTERVAL = 60


def init(core_obj):
    global core, running, thread
    core = core_obj
    running = True
    ensure_database_schema()
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()


def log(msg):
    if core and hasattr(core, "log"):
        core.log(msg)
    else:
        print(msg)


def db():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
    )


def split_sql_statements(sql):
    statements = []
    current = []
    quote = None
    escape = False
    for char in sql:
        current.append(char)
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement[:-1].strip())
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def table_column_defs(cur, table):
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {row["Field"]: row for row in cur.fetchall()}


def enum_values_from_type(column_type):
    import re
    return re.findall(r"'((?:[^'\\\\]|\\\\.)*)'", str(column_type or ""))


def ensure_enum_column(cur, table, column, values, default):
    definitions = table_column_defs(cur, table)
    column_def = definitions.get(column)
    if column_def is None:
        enum_sql = ",".join(f"'{value}'" for value in values)
        cur.execute(
            f"ALTER TABLE `{table}` ADD COLUMN `{column}` ENUM({enum_sql}) NOT NULL DEFAULT %s",
            (default,),
        )
        return

    placeholders = ",".join(["%s"] * len(values))
    cur.execute(
        f"UPDATE `{table}` SET `{column}`=%s "
        f"WHERE `{column}` IS NULL OR `{column}` NOT IN ({placeholders})",
        tuple([default, *values]),
    )
    current_values = enum_values_from_type(column_def.get("Type", ""))
    if current_values == list(values) and str(column_def.get("Default")) == str(default):
        return
    enum_sql = ",".join(f"'{value}'" for value in values)
    cur.execute(
        f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` ENUM({enum_sql}) NOT NULL DEFAULT %s",
        (default,),
    )


def ensure_varchar_column(cur, table, column, length, default):
    definitions = table_column_defs(cur, table)
    if definitions.get(column) is not None:
        return
    cur.execute(
        f"ALTER TABLE `{table}` ADD COLUMN `{column}` VARCHAR({int(length)}) NOT NULL DEFAULT %s",
        (default,),
    )


def ensure_yealink_endpoint_schema(cur):
    ensure_varchar_column(cur, PUSH_TABLE, "name", 255, "")
    ensure_enum_column(
        cur,
        PUSH_TABLE,
        "status",
        ("New", "Unchecked", "Offline", "Online"),
        "Unchecked",
    )


def ensure_database_schema():
    schema_path = BASE_DIR.parent / "install.sql"
    if not schema_path.exists():
        log(f"yealink schema file missing: {schema_path}")
        return
    statements = split_sql_statements(schema_path.read_text(encoding="utf-8"))
    if not statements:
        return
    conn = db()
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            ensure_yealink_endpoint_schema(cur)
        conn.commit()
        log(f"yealink database schema checked statements={len(statements)}")
    finally:
        conn.close()


def fetch_endpoints():
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT ipv4, status FROM `{PUSH_TABLE}`")
            return cur.fetchall()
    finally:
        conn.close()


def update_status(ipv4, status):
    conn = db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE `{PUSH_TABLE}` SET status=%s WHERE ipv4=%s",
                (status, ipv4),
            )
        conn.commit()
    finally:
        conn.close()


def get_endpoint_status():
    endpoints = []
    conn = db()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            try:
                cur.execute(
                    f"SELECT ipv4, name, status, username "
                    f"FROM `{PUSH_TABLE}` "
                    "ORDER BY ipv4 ASC"
                )
                for row in cur.fetchall():
                    device_name = str(row.get("name") or "").strip()
                    username = str(row.get("username") or "").strip()
                    if device_name and username and device_name == username:
                        device_name = ""
                    endpoints.append(
                        {
                            "id": f"push-{row.get('ipv4')}",
                            "name": device_name or row.get("ipv4") or "Yealink Push",
                            "address": row.get("ipv4") or "",
                            "model": "Yealink",
                            "status": row.get("status") or "Unknown",
                            "type": "Yealink Push Endpoint",
                            "direction": "Output",
                            "bell_capable": True,
                            "capabilities": ["bells"],
                        }
                    )
            except pymysql.MySQLError as exc:
                log(f"yealink push endpoint status error: {exc}")
    finally:
        conn.close()
    return {
        "module": "yealink",
        "display_name": "Yealink",
        "endpoints": endpoints,
    }


def ping_phone(ip):
    if not ip:
        return "Offline"

    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", "1000", ip]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return "Online" if result.returncode == 0 else "Offline"
    except Exception:
        return "Offline"


def loop():
    while running:
        try:
            endpoints = fetch_endpoints()
            for ip, status in endpoints:
                if status == "Unchecked":
                    continue

                result = ping_phone(ip)
                if result != status:
                    update_status(ip, result)
                    log(f"{ip} -> {result}")
        except Exception as e:
            log(f"yealink error: {e}")

        time.sleep(INTERVAL)


def shutdown():
    global running
    running = False


def api_endpoint(command_string):
    message_send.handle_api(command_string)


def handle_dispatch(action, stream_id, msg_id, targets, metadata=None):
    if action == "prepare_livepage":
        page_handler.handle_dispatch(action, stream_id, msg_id, targets, metadata)
        return
    message_send.handle_dispatch(action, stream_id, msg_id, targets)


def receive_audio(chunk, stream_id):
    message_send.receive_audio(chunk, stream_id)


def end_stream(stream_id):
    message_send.end_stream(stream_id)
