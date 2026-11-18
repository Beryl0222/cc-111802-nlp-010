import uuid
from datetime import datetime, timezone

from .citations import build_evidence
from .claims import claim_view, synchronize
from .records import TextRecord
from .text import annotate, normalize_text


class TextWorkflow:
    def __init__(self, repository):
        self.repository = repository

    def _now(self):
        return datetime.now(timezone.utc).isoformat()

    def _active(self, state):
        return {record["record_id"]: record for record in state["records"]}

    def _find_revision(self, state, record_id, revision):
        for row in state["revisions"]:
            if row.get("record_id") == record_id and row.get("revision") == revision:
                return row
        return None

    def _latest_revision(self, state, record_id):
        revisions = [
            row["revision"]
            for row in state["revisions"]
            if row.get("record_id") == record_id
        ]
        return max(revisions) if revisions else None

    def ingest(self, values, at=None):
        if not isinstance(values, list):
            raise TypeError("导入内容必须是记录列表")
        incoming = [TextRecord.from_dict(value).to_dict() for value in values]
        state = self.repository.read_state()
        current = self._active(state)
        at = at or self._now()
        stored = 0

        for record in incoming:
            normalized = normalize_text(record["text"])
            record["text"] = normalized
            record["analysis"] = annotate(record)
            previous = current.get(record["record_id"])
            latest = self._latest_revision(state, record["record_id"])

            existing = self._find_revision(
                state, record["record_id"], record["revision"]
            )
            if existing is not None:
                if self._revision_content(existing) != self._revision_content(record):
                    raise ValueError(
                        f"记录 {record['record_id']} 的修订 {record['revision']} "
                        "已存在且内容不同，修订不可改写"
                    )
                if previous is None:
                    # A previously retracted source is republished. Only its
                    # latest known revision may become active again, otherwise
                    # a stale copy would overwrite newer material.
                    if record["revision"] != latest:
                        continue
                    current[record["record_id"]] = existing
                    stored += 1
                    state["events"].append(
                        {
                            "event_id": uuid.uuid4().hex,
                            "at": at,
                            "type": "revision_restored",
                            "record_id": record["record_id"],
                            "revision": record["revision"],
                        }
                    )
                continue

            if latest is not None and record["revision"] <= latest:
                # Revisions are immutable; a lower or equal number that was
                # never stored is stale and must not replace newer material.
                continue

            state["revisions"].append(record)
            current[record["record_id"]] = record
            stored += 1
            state["events"].append(
                {
                    "event_id": uuid.uuid4().hex,
                    "at": at,
                    "type": "revision_ingested",
                    "record_id": record["record_id"],
                    "revision": record["revision"],
                    "previous_revision": previous["revision"] if previous else latest,
                }
            )

        state["records"] = sorted(current.values(), key=lambda row: row["record_id"])
        synchronize(state, at)
        self.repository.write_state(state)
        return {"received": len(incoming), "stored": stored}

    @staticmethod
    def _revision_content(record):
        return (record.get("source"), record.get("text"), record.get("context"))

    def revisions(self, record_id=None):
        state = self.repository.read_state()
        rows = state["revisions"]
        if record_id:
            rows = [row for row in rows if row["record_id"] == record_id]
            if not rows:
                raise ValueError(f"找不到来源记录 {record_id}")
        return sorted(rows, key=lambda row: (row["record_id"], row["revision"]))

    def list_records(self, source=None):
        records = self.repository.read()
        if source:
            records = [record for record in records if record["source"] == source]
        return records

    def _claim(self, state, claim_id):
        for claim in state["claims"]:
            if claim.get("claim_id") == claim_id:
                return claim
        raise ValueError(f"找不到摘要陈述 {claim_id}")

    def _build_evidence(self, state, citation, at):
        if not isinstance(citation, dict):
            raise TypeError("依据必须是包含 record_id 和 quote 的对象")
        record_id = str(citation.get("record_id", "")).strip()
        record = self._active(state).get(record_id)
        if record is None:
            raise ValueError(f"来源记录 {record_id} 不存在或已撤回，无法建立依据")
        return build_evidence(
            record,
            citation.get("quote", ""),
            occurrence=citation.get("occurrence"),
            at=at,
        )

    def add_claim(self, text, citations=(), at=None):
        text = str(text).strip()
        if not text:
            raise ValueError("摘要陈述不能为空")
        at = at or self._now()
        state = self.repository.read_state()
        evidence = [self._build_evidence(state, citation, at) for citation in citations]
        claim = {
            "claim_id": uuid.uuid4().hex,
            "text": text,
            "created_at": at,
            "updated_at": at,
            "evidence": evidence,
            "status": None,
            "basis": None,
        }
        state["claims"].append(claim)
        synchronize(state, at)
        state["events"].append(
            {
                "event_id": uuid.uuid4().hex,
                "at": at,
                "type": "claim_created",
                "claim_id": claim["claim_id"],
                "status": claim["status"],
            }
        )
        self.repository.write_state(state)
        return claim_view(state, claim)

    def update_claim(
        self, claim_id, text=None, keep_evidence_ids=None, add_citations=(), at=None
    ):
        """Rewrite a claim while explicitly keeping or removing its evidence."""
        if text is None and keep_evidence_ids is None and not add_citations:
            raise ValueError("没有需要修改的内容")
        at = at or self._now()
        state = self.repository.read_state()
        claim = self._claim(state, claim_id)
        changes = {}

        if text is not None:
            text = str(text).strip()
            if not text:
                raise ValueError("摘要陈述不能为空")
            if text != claim["text"]:
                claim["text"] = text
                changes["text"] = True

        if keep_evidence_ids is not None:
            keep = [str(value) for value in keep_evidence_ids]
            existing_ids = [item["evidence_id"] for item in claim["evidence"]]
            unknown = [value for value in keep if value not in existing_ids]
            if unknown:
                raise ValueError(f"未知依据 {unknown[0]}")
            kept = [item for item in claim["evidence"] if item["evidence_id"] in keep]
            removed = [
                item["evidence_id"]
                for item in claim["evidence"]
                if item["evidence_id"] not in keep
            ]
            claim["evidence"] = kept
            changes["removed_evidence_ids"] = removed

        if add_citations:
            added = [
                self._build_evidence(state, citation, at)
                for citation in add_citations
            ]
            claim["evidence"].extend(added)
            changes["added_evidence_ids"] = [item["evidence_id"] for item in added]

        claim["updated_at"] = at
        if changes:
            state["events"].append(
                {
                    "event_id": uuid.uuid4().hex,
                    "at": at,
                    "type": "claim_edited",
                    "claim_id": claim["claim_id"],
                    "changes": changes,
                }
            )
        synchronize(state, at)
        self.repository.write_state(state)
        return claim_view(state, claim)

    def remove_evidence(self, claim_id, evidence_id, at=None):
        state = self.repository.read_state()
        claim = self._claim(state, claim_id)
        if not any(item["evidence_id"] == evidence_id for item in claim["evidence"]):
            raise ValueError(f"未知依据 {evidence_id}")
        keep = [
            item["evidence_id"]
            for item in claim["evidence"]
            if item["evidence_id"] != evidence_id
        ]
        return self.update_claim(claim_id, keep_evidence_ids=keep, at=at)

    def list_claims(self):
        state = self.repository.read_state()
        return [claim_view(state, claim) for claim in state["claims"]]

    def show_claim(self, claim_id):
        state = self.repository.read_state()
        claim = self._claim(state, claim_id)
        return claim_view(state, claim, include_events=True)

    def retract_record(self, record_id, at=None):
        """Withdraw a source while retaining its revision history for audits."""
        at = at or self._now()
        state = self.repository.read_state()
        remaining = [
            record for record in state["records"] if record["record_id"] != record_id
        ]
        if len(remaining) == len(state["records"]):
            raise ValueError(f"找不到来源记录 {record_id}")
        state["records"] = remaining
        state["events"].append(
            {
                "event_id": uuid.uuid4().hex,
                "at": at,
                "type": "record_retracted",
                "record_id": record_id,
            }
        )
        synchronize(state, at)
        self.repository.write_state(state)
        return {"record_id": record_id, "status": "retracted"}
