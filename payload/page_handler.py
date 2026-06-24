import importlib
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEBUG = os.getenv("DEBUG", "").strip().lower() == "true"


def load_message_send():
    return importlib.import_module("yealink_message_send_runtime")


message_send = load_message_send()


def page_debug(message):
    if DEBUG:
        message_send.debug_log(f"page_handler {message}")


def handle_dispatch(action, stream_id, group_id, targets, metadata=None):
    page_debug(f"handle_dispatch_start action={action} stream={stream_id} group={group_id} targets={targets} metadata={metadata}")
    if action != "prepare_livepage":
        return
    page_debug(f"handle_dispatch_ready stream={stream_id}")
    message_send.send_ready_signal("yealink", stream_id)


def receive_audio(chunk, stream_id):
    message_send.receive_audio(chunk, stream_id)


def end_stream(stream_id):
    message_send.end_stream(stream_id)
