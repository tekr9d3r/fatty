import json
import os
import tempfile
from typing import Optional

DATA_FILE = "user_data.json"

# In-memory pending confirmations — keyed by user_id (int), cleared on restart
pending: dict[int, dict] = {}


def load_store() -> dict:
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_store(data: dict) -> None:
    dir_name = os.path.dirname(os.path.abspath(DATA_FILE))
    with tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, suffix=".tmp") as f:
        json.dump(data, f, indent=2)
        tmp_path = f.name
    os.replace(tmp_path, DATA_FILE)


def _get_user(user_id: int) -> dict:
    return load_store().get(str(user_id), {})


def _set_user(user_id: int, updates: dict) -> None:
    data = load_store()
    key = str(user_id)
    if key not in data:
        data[key] = {}
    data[key].update(updates)
    save_store(data)


def get_goal(user_id: int) -> Optional[int]:
    return _get_user(user_id).get("goal")


def set_goal(user_id: int, goal: int) -> None:
    _set_user(user_id, {"goal": goal})


def get_last_row(user_id: int) -> Optional[int]:
    return _get_user(user_id).get("last_row")


def set_last_row(user_id: int, row_index: Optional[int]) -> None:
    _set_user(user_id, {"last_row": row_index})
