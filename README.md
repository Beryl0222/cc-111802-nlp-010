# 课堂资料摘要器

课堂资料摘要器用于处理本地合成教研文本，保存来源版本、文本片段和处理记录，供校内复核使用。

## 环境

需要 Python 3.11 或更新版本。应用运行不依赖第三方包；运行测试和构建前，安装固定版本的开发工具：`python -m pip install -r requirements-dev.txt`。

## 运行

执行 `PYTHONPATH=src python -m nlp_classroom_summary_citations.cli ingest fixtures/records.json` 导入演练记录，再执行 `PYTHONPATH=src python -m nlp_classroom_summary_citations.cli list` 查看当前文本处理结果。运行数据保存在 `var/records.json`。

## 摘要陈述与原文依据

每条摘要陈述（claim）保存自己的文字，并保存若干条**依据**（evidence）。依据不改存“第几段”这类易失效的编号，而是记录：来源 `record_id`、引用时的 `revision`、引文原文 `quote`、引文哈希、以及在该修订中的字符区间 `start`/`end`。材料修订全部保留在 `revisions` 中，因此任何时候都能回看引用时的原文。

新增陈述并附上原文：

```bash
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli add-claim \
  "摘要需要保留原文依据。" \
  --cite "classroom-summary-citations-lesson-a=课堂资料摘要器需要保留摘要陈述和原文依据。"
```

引文在来源中出现多次时，需要在该次引用里显式给出 `occurrence`（从 1 开始）；系统不会替老师猜测是哪一处。

改写陈述时可以显式保留或移除依据（`--keep` 传依据 ID，逗号分隔；留空表示移除全部），也可以追加 `--cite`：

```bash
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli edit-claim CLAIM_ID \
  --text "改写后的陈述。" --keep EVIDENCE_ID_1,EVIDENCE_ID_2
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli remove-evidence CLAIM_ID EVIDENCE_ID
```

## 依据核对与状态

材料每次导入都视为不可改写的新修订（同一 `revision` 内容不同会被拒绝）。查看陈述时，系统按当前生效修订逐条核对依据：

- 引文仍在原区间：`exact_match`，依据有效。
- 引文被移动、但在新修订中只出现一次：`relocated_unique_match`，依据有效，并给出新的区间。
- 新修订中找不到引文：`quote_missing`，依据失效。
- 引文在新修订中出现多处：`ambiguous_quote`，依据失效（不会随便落到某一段）。
- 来源被撤回或不存在：`source_missing`，依据失效。

陈述状态由依据共同决定：

| 状态 | 含义 |
| --- | --- |
| `verified`（已核实） | 至少一条依据，且全部依据有效 |
| `partially_supported`（部分依据有效） | 部分依据失效，但仍至少有一条有效依据 |
| `needs_review`（待复核） | 没有任何有效依据：全部失效，或尚未附依据 |

因此单个来源撤回只会让相应依据失效，不会抹掉其他依据；只有全部依据都失效时，陈述才进入待复核。状态变化（包括材料恢复后回到已核实）会写入事件流，`show` 会列出该陈述的完整历史。

```bash
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli claims          # 概览
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli show CLAIM_ID   # 逐条依据、引用版本、当前版本、状态历史
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli revisions RECORD_ID  # 来源的全部修订
PYTHONPATH=src python -m nlp_classroom_summary_citations.cli retract RECORD_ID    # 撤回来源，保留修订历史
```

`show` 对每条依据同时给出 `cited_text`（引用时所在修订的原文）、`cited_excerpt`（引用区间上下文）与 `current_excerpt`（当前修订中的实际位置），并注明 `revision_changed`、`reason`，使材料更新造成的状态变化可以解释。

## 测试

执行 `python -m pytest -q` 检查文本规范化、记录处理、依据锚定、修订撤改后的状态变化、状态读取及本地持久化。
