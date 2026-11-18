import json
from pathlib import Path


STATE_KEYS = ("records", "revisions", "claims", "events")


class RecordRepository:
    """Atomic JSON state storage.

    The file keeps the existing ``records`` list (the active revision of each
    material) and adds revision history, summary claims and an audit event
    stream. Older files containing only ``records`` are still readable.
    """

    def __init__(self, path):
        self.path = Path(path)

    def read_state(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {key: [] for key in STATE_KEYS}
        if not isinstance(value, dict) or not isinstance(value.get("records"), list):
            raise ValueError("记录库格式无效")
        state = {}
        for key in STATE_KEYS:
            rows = value.get(key, [])
            if not isinstance(rows, list):
                raise ValueError("记录库格式无效")
            state[key] = rows

        # Older repositories did not retain revisions. Treat the active
        # record as its own revision so citations can still be validated.
        known = {(row.get("record_id"), row.get("revision")) for row in state["revisions"]}
        for record in state["records"]:
            key = (record.get("record_id"), record.get("revision"))
            if key not in known:
                state["revisions"].append(dict(record))
                known.add(key)
        return state

    def write_state(self, state):
        payload = {key: state.get(key, []) for key in STATE_KEYS}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def read(self):
        return self.read_state()["records"]

    def replace(self, records):
        state = self.read_state()
        state["records"] = list(records)
        known = {(row.get("record_id"), row.get("revision")) for row in state["revisions"]}
        for record in records:
            key = (record.get("record_id"), record.get("revision"))
            if key not in known:
                state["revisions"].append(dict(record))
                known.add(key)
        self.write_state(state)
