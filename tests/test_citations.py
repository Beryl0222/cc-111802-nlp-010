import json
import tempfile
import unittest
from pathlib import Path

from nlp_classroom_summary_citations.repository import RecordRepository
from nlp_classroom_summary_citations.workflow import TextWorkflow


OBSERVED = "2026-11-18T09:00:00Z"


def record(record_id, text, revision=1, source="synthetic"):
    return {
        "record_id": record_id,
        "source": source,
        "revision": revision,
        "observed_at": OBSERVED,
        "text": text,
    }


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workflow = TextWorkflow(
            RecordRepository(Path(self.directory.name) / "records.json")
        )

    def test_evidence_is_anchored_to_offsets_and_quote(self):
        self.workflow.ingest([record("a", "前言。课堂资料需要保留原文依据。结语。")])
        claim = self.workflow.add_claim(
            "摘要需要保留依据。",
            [{"record_id": "a", "quote": "课堂资料需要保留原文依据。"}],
        )
        evidence = claim["evidence"][0]
        self.assertEqual(claim["status"], "verified")
        self.assertEqual(evidence["start"], 3)
        self.assertEqual(evidence["end"], 16)
        self.assertEqual(evidence["revision"], 1)
        self.assertEqual(evidence["quote"], "课堂资料需要保留原文依据。")
        self.assertEqual(len(evidence["quote_hash"]), 64)
        self.assertEqual(evidence["cited_excerpt"], "前言。课堂资料需要保留原文依据。结语。")

    def test_unknown_or_ambiguous_quote_is_rejected(self):
        self.workflow.ingest([record("a", "重复片段。重复片段。")])
        with self.assertRaises(ValueError):
            self.workflow.add_claim("x", [{"record_id": "a", "quote": "不存在的片段"}])
        with self.assertRaises(ValueError):
            self.workflow.add_claim("x", [{"record_id": "a", "quote": "重复片段。"}])
        claim = self.workflow.add_claim(
            "x",
            [{"record_id": "a", "quote": "重复片段。", "occurrence": 2}],
        )
        self.assertEqual(claim["evidence"][0]["start"], 5)

    def test_revision_that_moves_quote_keeps_support(self):
        self.workflow.ingest([record("a", "甲结论需要乙依据。其他内容。")])
        claim = self.workflow.add_claim(
            "甲结论成立。", [{"record_id": "a", "quote": "甲结论需要乙依据。"}]
        )
        self.workflow.ingest(
            [record("a", "新增前言。甲结论需要乙依据。其他内容。", revision=2)]
        )
        view = self.workflow.show_claim(claim["claim_id"])
        evidence = view["evidence"][0]
        self.assertEqual(view["status"], "verified")
        self.assertEqual(evidence["match"], "relocated")
        self.assertEqual(evidence["current_start"], 5)
        self.assertTrue(evidence["revision_changed"])
        self.assertEqual(evidence["cited_revision_available"], True)
        self.assertEqual(evidence["cited_excerpt"], "甲结论需要乙依据。其他内容。")

    def test_revision_that_duplicates_quote_becomes_unresolved(self):
        self.workflow.ingest([record("a", "甲结论需要乙依据。")])
        claim = self.workflow.add_claim(
            "甲结论成立。", [{"record_id": "a", "quote": "甲结论需要乙依据。"}]
        )
        self.workflow.ingest(
            [record("a", "又见：甲结论需要乙依据。其他内容。甲结论需要乙依据。", revision=2)]
        )
        view = self.workflow.show_claim(claim["claim_id"])
        self.assertEqual(view["status"], "needs_review")
        self.assertEqual(view["evidence"][0]["reason"], "ambiguous_quote")

    def test_revision_that_removes_quote_needs_review(self):
        self.workflow.ingest([record("a", "甲结论需要乙依据。")])
        claim = self.workflow.add_claim(
            "甲结论成立。", [{"record_id": "a", "quote": "甲结论需要乙依据。"}]
        )
        self.workflow.ingest([record("a", "完全不同的内容。", revision=2)])
        view = self.workflow.show_claim(claim["claim_id"])
        self.assertEqual(view["status"], "needs_review")
        self.assertEqual(view["basis"], "all_evidence_invalid")
        self.assertEqual(view["evidence"][0]["reason"], "quote_missing")
        self.assertIsNone(view["evidence"][0]["current_excerpt"])

    def test_one_retracted_source_does_not_erase_other_evidence(self):
        self.workflow.ingest(
            [record("a", "甲结论来自课堂。"), record("b", "甲结论也见于作业。")]
        )
        claim = self.workflow.add_claim(
            "甲结论成立。",
            [
                {"record_id": "a", "quote": "甲结论来自课堂。"},
                {"record_id": "b", "quote": "甲结论也见于作业。"},
            ],
        )
        self.workflow.retract_record("b")
        view = self.workflow.show_claim(claim["claim_id"])
        self.assertEqual(view["status"], "partially_supported")
        self.assertEqual(view["valid_evidence_count"], 1)
        reasons = {item["record_id"]: item["reason"] for item in view["evidence"]}
        self.assertEqual(reasons, {"a": "exact_match", "b": "source_missing"})

    def test_all_retracted_sources_produce_needs_review_and_event(self):
        self.workflow.ingest([record("a", "唯一依据。")])
        claim = self.workflow.add_claim(
            "结论。", [{"record_id": "a", "quote": "唯一依据。"}]
        )
        self.workflow.retract_record("a")
        view = self.workflow.show_claim(claim["claim_id"])
        self.assertEqual(view["status"], "needs_review")
        transitions = [row for row in view["history"] if row["type"] == "status_changed"]
        self.assertEqual(
            [(row["from_status"], row["to_status"]) for row in transitions],
            [("verified", "needs_review")],
        )

    def test_teacher_can_keep_or_remove_evidence_when_editing(self):
        self.workflow.ingest(
            [record("a", "依据甲。"), record("b", "依据乙。"), record("c", "依据丙。")]
        )
        claim = self.workflow.add_claim(
            "原陈述。",
            [
                {"record_id": "a", "quote": "依据甲。"},
                {"record_id": "b", "quote": "依据乙。"},
            ],
        )
        first, second = claim["evidence"][0]["evidence_id"], claim["evidence"][1]["evidence_id"]
        edited = self.workflow.update_claim(
            claim["claim_id"],
            text="改写后的陈述。",
            keep_evidence_ids=[second],
            add_citations=[{"record_id": "c", "quote": "依据丙。"}],
        )
        self.assertEqual(edited["text"], "改写后的陈述。")
        self.assertEqual(
            {item["record_id"] for item in edited["evidence"]}, {"b", "c"}
        )
        event = [
            row
            for row in self.workflow.show_claim(claim["claim_id"])["history"]
            if row["type"] == "claim_edited"
        ][-1]
        self.assertEqual(event["changes"]["removed_evidence_ids"], [first])

        removed = self.workflow.remove_evidence(claim["claim_id"], second)
        self.assertEqual({item["record_id"] for item in removed["evidence"]}, {"c"})

        empty = self.workflow.update_claim(claim["claim_id"], keep_evidence_ids=[])
        self.assertEqual(empty["status"], "needs_review")
        self.assertEqual(empty["basis"], "no_evidence")

    def test_rejected_citation_does_not_partially_write_claim(self):
        self.workflow.ingest([record("a", "有效依据。")])
        with self.assertRaises(ValueError):
            self.workflow.add_claim(
                "结论。",
                [
                    {"record_id": "a", "quote": "有效依据。"},
                    {"record_id": "missing", "quote": "不存在。"},
                ],
            )
        self.assertEqual(self.workflow.list_claims(), [])

    def test_immutable_revision_rejects_conflicting_reupload(self):
        self.workflow.ingest([record("a", "初稿。")])
        claim = self.workflow.add_claim(
            "结论。", [{"record_id": "a", "quote": "初稿。"}]
        )
        with self.assertRaises(ValueError):
            self.workflow.ingest([record("a", "不同内容。")])
        self.assertEqual(
            self.workflow.show_claim(claim["claim_id"])["status"], "verified"
        )

    def test_retracted_source_can_be_republished_at_latest_revision(self):
        self.workflow.ingest([record("a", "初稿依据。")])
        self.workflow.ingest([record("a", "新稿依据。", revision=2)])
        claim = self.workflow.add_claim(
            "结论。", [{"record_id": "a", "quote": "新稿依据。"}]
        )
        self.workflow.retract_record("a")
        self.assertEqual(
            self.workflow.show_claim(claim["claim_id"])["status"], "needs_review"
        )
        # A stale revision must not overwrite the newer stored material.
        self.workflow.ingest([record("a", "初稿依据。")])
        self.assertEqual(self.workflow.list_records(), [])
        self.workflow.ingest([record("a", "新稿依据。", revision=2)])
        view = self.workflow.show_claim(claim["claim_id"])
        self.assertEqual(view["status"], "verified")
        self.assertEqual(view["evidence"][0]["revision"], 2)
    def test_tampered_anchor_is_reported_not_silently_trusted(self):
        path = Path(self.directory.name) / "records.json"
        self.workflow.ingest([record("a", "依据原文。")])
        claim = self.workflow.add_claim(
            "结论。", [{"record_id": "a", "quote": "依据原文。"}]
        )
        state = json.loads(path.read_text(encoding="utf-8"))
        state["claims"][0]["evidence"][0]["quote"] = "伪造引文。"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        view = self.workflow.show_claim(claim["claim_id"])
        self.assertEqual(view["status"], "needs_review")
        self.assertEqual(view["evidence"][0]["reason"], "anchor_integrity_error")

    def test_claim_without_evidence_needs_review(self):
        claim = self.workflow.add_claim("尚无依据的结论。")
        self.assertEqual(claim["status"], "needs_review")
        self.assertEqual(claim["basis"], "no_evidence")
        self.assertEqual(claim["status_label"], "待复核")
        self.assertEqual(claim["evidence"], [])

    def test_legacy_repository_without_history_is_readable(self):
        path = Path(self.directory.name) / "records.json"
        path.write_text(
            '{"records": [{"record_id": "a", "source": "s", "revision": 1, '
            '"observed_at": "2026-11-18T09:00:00Z", "text": "旧文本。"}]}',
            encoding="utf-8",
        )
        workflow = TextWorkflow(RecordRepository(path))
        self.assertEqual(workflow.list_records()[0]["text"], "旧文本。")
        claim = workflow.add_claim("结论。", [{"record_id": "a", "quote": "旧文本。"}])
        self.assertEqual(claim["status"], "verified")


if __name__ == "__main__":
    unittest.main()
