# core/signals.py
import threading

# Session ID -> threading.Event
_stop_flags = {}

def set_stop_flag(session_id: str):
    if session_id not in _stop_flags:
        _stop_flags[session_id] = threading.Event()
    _stop_flags[session_id].set()

def clear_stop_flag(session_id: str):
    if session_id in _stop_flags:
        _stop_flags[session_id].clear()
    else:
        _stop_flags[session_id] = threading.Event()

def is_stopped(session_id: str) -> bool:
    if session_id in _stop_flags:
        return _stop_flags[session_id].is_set()
    return False

def evict_stop_flag(session_id: str):
    _stop_flags.pop(session_id, None)
