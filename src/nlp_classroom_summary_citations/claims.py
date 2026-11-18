"""Summary claims, their evidence links and review statuses."""

import uuid

from .citations import excerpt, evaluate_evidence

VERIFIED = "verified"
PARTIALLY_SUPPORTED = "partially_supported"
NEEDS_REVIEW = "needs_review"

ALL_EVIDENCE_VALID = "all_evidence_valid"
SOME_EVIDENCE_INVALID = "some_evidence_invalid"
ALL_EVIDENCE_INVALID = "all_evidence_invalid"
NO_EVIDENCE = "no_evidence"


STATUS_LABELS = {
    VERIFIED: "已核实",
    PARTIALLY_SUPPORTED: "部分依据有效",
    NEEDS_REVIEW: "待复核",
}

BASIS_LABELS = {
    ALL_EVIDENCE_VALID: "全部依据均能在当前材料中定位",
    SOME_EVIDENCE_INVALID: "部分依据已失效，仍保留有效依据",
    ALL_EVIDENCE_INVALID: "全部依据均已失效",
    NO_EVIDENCE: "尚无原文依据",
}

SOURCE_EVENT_TYPES = ("revision_ingested", "revision_restored", "record_retracted")

REASON_LABELS = {
    "exact_match": "引文仍在原位置",
    "relocated_unique_match": "引文已移动到新位置，且在新修订中唯一",
    "quote_missing": "当前修订中已找不到引文",
    "ambiguous_quote": "当前修订中引文出现多处，无法确定原段落",
    "source_missing": "来源记录已撤回或不存在",
    "anchor_integrity_error": "依据锚点数据损坏",
}


def _revision(state, record_id, revision):
    for row in state["revisions"]:
        if row.get("record_id") == record_id and row.get("revision") == revision:
            return row
    return None


def evidence_view(state, evidence, active_records):
    record = active_records.get(evidence.get("record_id"))
    evaluation = evaluate_evidence(evidence, record)
    cited = _revision(state, evidence.get("record_id"), evidence.get("revision"))
    current_text = record.get("text") if record else None

    view = {
        "evidence_id": evidence.get("evidence_id"),
        "record_id": evidence.get("record_id"),
        "source": evidence.get("source"),
        "revision": evidence.get("revision"),
        "quote": evidence.get("quote"),
        "quote_hash": evidence.get("quote_hash"),
        "start": evidence.get("start"),
        "end": evidence.get("end"),
        "status": "valid" if evaluation.valid else "invalid",
        "match": evaluation.match,
        "reason": evaluation.reason,
        "reason_label": REASON_LABELS.get(evaluation.reason, evaluation.reason),
        "current_revision": record.get("revision") if record else None,
        "revision_changed": bool(record and record.get("revision") != evidence.get("revision")),
        "current_excerpt": None,
        "cited_revision_available": cited is not None,
        "cited_excerpt": None,
        "cited_text": cited.get("text") if cited else None,
        "cited_observed_at": cited.get("observed_at") if cited else None,
    }
    if evaluation.current_start is not None:
        view["current_start"] = evaluation.current_start
        view["current_end"] = evaluation.current_end
        view["current_excerpt"] = excerpt(
            current_text, evaluation.current_start, evaluation.current_end
        )
    if cited is not None:
        view["cited_excerpt"] = excerpt(
            cited.get("text", ""), evidence.get("start", 0), evidence.get("end", 0)
        )
    return view, evaluation


def claim_evidence_views(state, claim, active_records):
    views = []
    evaluations = []
    for evidence in claim.get("evidence", []):
        view, evaluation = evidence_view(state, evidence, active_records)
        views.append(view)
        evaluations.append(evaluation)
    return views, evaluations


def derive_status(evaluations):
    if not evaluations:
        return NEEDS_REVIEW, NO_EVIDENCE
    valid_count = sum(1 for evaluation in evaluations if evaluation.valid)
    if valid_count == len(evaluations):
        return VERIFIED, ALL_EVIDENCE_VALID
    if valid_count:
        return PARTIALLY_SUPPORTED, SOME_EVIDENCE_INVALID
    return NEEDS_REVIEW, ALL_EVIDENCE_INVALID


def claim_view(state, claim, include_events=False):
    active_records = {record["record_id"]: record for record in state["records"]}
    views, evaluations = claim_evidence_views(state, claim, active_records)
    status, basis = derive_status(evaluations)
    result = {
        "claim_id": claim.get("claim_id"),
        "text": claim.get("text"),
        "status": status,
        "status_label": STATUS_LABELS[status],
        "basis": basis,
        "basis_label": BASIS_LABELS[basis],
        "evidence": views,
        "valid_evidence_count": sum(1 for view in views if view["status"] == "valid"),
        "evidence_count": len(views),
        "created_at": claim.get("created_at"),
        "updated_at": claim.get("updated_at"),
        "stored_status": claim.get("status"),
    }
    if include_events:
        record_ids = {item.get("record_id") for item in claim.get("evidence", [])}
        created_at = claim.get("created_at") or ""
        result["history"] = [
            event
            for event in state.get("events", [])
            if event.get("claim_id") == claim.get("claim_id")
            or (
                event.get("record_id") in record_ids
                and event.get("type") in SOURCE_EVENT_TYPES
                and str(event.get("at", "")) >= created_at
            )
        ]
    return result


def synchronize(state, at):
    """Re-evaluate every claim and log any status transition.

    Nothing is changed silently: every transition, whether towards
    ``needs_review`` or back to ``verified`` after a material update, is
    recorded together with the evidence that caused it, so a teacher can
    explain why the summary's status changed.
    """
    active_records = {record["record_id"]: record for record in state["records"]}
    events = state["events"]
    for claim in state["claims"]:
        views, evaluations = claim_evidence_views(state, claim, active_records)
        status, basis = derive_status(evaluations)
        previous = claim.get("status")
        if previous != status:
            if previous is not None:
                events.append(
                    {
                        "event_id": uuid.uuid4().hex,
                        "at": at,
                        "type": "status_changed",
                        "claim_id": claim.get("claim_id"),
                        "from_status": previous,
                        "to_status": status,
                        "basis": basis,
                        "evidence": [
                            {
                                "evidence_id": view["evidence_id"],
                                "record_id": view["record_id"],
                                "revision": view["revision"],
                                "current_revision": view["current_revision"],
                                "valid": view["status"] == "valid",
                                "reason": view["reason"],
                            }
                            for view in views
                        ],
                    }
                )
            claim["status"] = status
        claim["basis"] = basis
        claim["valid_evidence_count"] = sum(
            1 for view in views if view["status"] == "valid"
        )
