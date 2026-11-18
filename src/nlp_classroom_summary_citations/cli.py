import argparse
import json
from pathlib import Path

from .repository import RecordRepository
from .workflow import TextWorkflow


def _citations(values):
    citations = []
    for value in values or []:
        record_id, _, quote = value.partition("=")
        if not record_id or not quote:
            raise SystemExit("--cite 需要 RECORD_ID=引文 格式")
        citations.append({"record_id": record_id, "quote": quote})
    return citations


def _evidence_ids(values):
    if values is None:
        return None
    return [value for value in values.split(",") if value]


def _add_state(parser):
    # Accepted both before and after the subcommand; the root default applies
    # when the option is omitted entirely.
    parser.add_argument("--state", default=argparse.SUPPRESS)


def build_parser():
    parser = argparse.ArgumentParser(description="本地教研文本处理工具")
    parser.add_argument("--state", default="var/records.json")
    commands = parser.add_subparsers(dest="action", required=True)

    ingest = commands.add_parser("ingest", help="导入或更新材料修订")
    ingest.add_argument("path", nargs="?", default="fixtures/records.json")
    _add_state(ingest)

    listing = commands.add_parser("list", help="列出当前材料")
    listing.add_argument("--source")
    _add_state(listing)

    claims = commands.add_parser("claims", help="列出摘要陈述及依据状态")
    _add_state(claims)

    history = commands.add_parser("revisions", help="查看来源材料的全部修订")
    history.add_argument("record_id", nargs="?")
    _add_state(history)

    show = commands.add_parser("show", help="查看单条摘要陈述、依据和版本历史")
    show.add_argument("claim_id")
    _add_state(show)

    add = commands.add_parser("add-claim", help="新增摘要陈述")
    add.add_argument("text")
    add.add_argument("--cite", action="append", metavar="RECORD_ID=引文")
    _add_state(add)

    edit = commands.add_parser("edit-claim", help="改写摘要并保留或移除依据")
    edit.add_argument("claim_id")
    edit.add_argument("--text")
    edit.add_argument(
        "--keep",
        metavar="EVIDENCE_ID,...",
        help="只保留这些依据（逗号分隔，留空表示移除全部依据）",
    )
    edit.add_argument("--cite", action="append", metavar="RECORD_ID=引文")
    _add_state(edit)

    drop = commands.add_parser("remove-evidence", help="从摘要陈述移除一条依据")
    drop.add_argument("claim_id")
    drop.add_argument("evidence_id")
    _add_state(drop)

    retract = commands.add_parser("retract", help="撤回一个来源（保留修订历史）")
    retract.add_argument("record_id")
    _add_state(retract)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    workflow = TextWorkflow(RecordRepository(args.state))
    if args.action == "ingest":
        result = workflow.ingest(
            json.loads(Path(args.path).read_text(encoding="utf-8"))
        )
    elif args.action == "list":
        result = workflow.list_records(args.source)
    elif args.action == "claims":
        result = workflow.list_claims()
    elif args.action == "revisions":
        result = workflow.revisions(args.record_id)
    elif args.action == "show":
        result = workflow.show_claim(args.claim_id)
    elif args.action == "add-claim":
        result = workflow.add_claim(args.text, _citations(args.cite))
    elif args.action == "edit-claim":
        keep = _evidence_ids(args.keep)
        result = workflow.update_claim(
            args.claim_id,
            text=args.text,
            keep_evidence_ids=keep,
            add_citations=_citations(args.cite),
        )
    elif args.action == "remove-evidence":
        result = workflow.remove_evidence(args.claim_id, args.evidence_id)
    else:
        result = workflow.retract_record(args.record_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
