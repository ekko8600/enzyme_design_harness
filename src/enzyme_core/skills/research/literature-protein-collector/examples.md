# 示例

## 示例 1：某 GH11 木聚糖酶的热稳定性突变体调研

**用户输入**：

- 目标蛋白名称：GH11 Xylanase XynA
- 目标序列：`MKTAYIAKQRQISFVKSHFSRQ...`（330 aa）
- 任务：找出文献中报道过的、能提升该酶热稳定性或催化活性的突变位点，整理成表格并给出理论解析。

**步骤 1 检索**：

```bash
python3 scripts/literature_collector.py search \
  --query "GH11 xylanase thermostability mutant directed evolution" \
  --sources europepmc,openalex,pubmed,semanticscholar,crossref,arxiv \
  --max-results 25 \
  --output output/candidates.json
```

得到 20+ 篇候选，智能体读标题摘要后筛出 6 篇高度相关的（排除了 2 篇纯结构解析无突变体的论文、
1 篇综述、若干不同家族/物种不相关的木聚糖酶论文），另存为 `output/selected.json`。

**步骤 3 抓取全文**：

```bash
python3 scripts/literature_collector.py fetch-fulltext \
  --selected output/selected.json --output output/raw_data.json
```

`fulltext_summary` 显示 `{"text": 3, "pdf": 1, "abstract_only": 2}`：3 篇通过 Europe PMC 拿到
纯文本全文，1 篇通过期刊 OA 直链拿到 PDF，2 篇是付费墙文献只能用摘要分析。

**步骤 4 智能体深读**（节选 `analysis.json` 中一篇的结构，完整格式见 SKILL.md）：

```json
{
  "citation": "Wang et al. 2021, Appl Microbiol Biotechnol",
  "doi": "10.1007/s00253-021-XXXXX",
  "candidate_id": "c3",
  "fulltext_status": "text",
  "analysis_basis": "full_text",
  "accessible_content_summary": "已分析完整正文、方法、图表和补充序列。",
  "content_access_note": "无已知缺失章节。",
  "findings": [
    {
      "category": "stability",
      "claim": "双突变 T30A/N40D 提高了该测定条件下的热稳定性。",
      "evidence_type": "direct_observation",
      "source_locator": "Table 2 and Results, thermostability subsection",
      "confidence": "high",
      "limitations": "单点 T30A 的独立贡献未报告。"
    }
  ],
  "paper_sequence": "MKTAYIAKQR...(该文献 Figure 1 中给出的成熟蛋白序列，256 aa，比目标序列短因为不含信号肽)",
  "sequence_source_note": "Figure 1，成熟蛋白编号（不含 24 aa 信号肽）",
  "mutants": [
    {
      "raw_description": "T30A/N40D (双突变 Mut-TS2)",
      "mutation_position": 30,
      "wild_type_residue": "T",
      "mutant_residue": "A",
      "property_tested": "热稳定性 (T50, 60min 残余活性)",
      "quantitative_result": "T50 从 52.3C 提升到 61.8C (+9.5C)；60C 处理 60min 后残余活性从 12% 提升到 78%",
      "qualitative_result": "显著提升热稳定性，最适 pH 与最适温度无明显变化",
      "assay_method": "T50 测定；DSC 验证 Tm 变化趋势一致",
      "source_locator": "Table 2, Mut-TS2 row",
      "evidence_type": "direct_observation",
      "confidence": "high",
      "limitations": "双突变背景，无法拆分 T30A 的单点贡献。",
      "notes": "与 N40D 联合突变，单点 T30A 的贡献未单独报道"
    }
  ]
}
```

注意：这篇文献用的是**成熟蛋白编号**（不含信号肽），与用户给的目标序列（可能含信号肽）编号体系不
同——这正是为什么必须记录 `paper_sequence` 并交给脚本做序列比对换算，而不能直接把 `30` 当成目标
序列里的第 30 位。

**步骤 5 生成产出**：

```bash
python3 scripts/literature_collector.py from-analysis \
  --analysis output/analysis.json \
  --output-csv output/mutants.csv \
  --output-report output/report.md \
  --search-summary output/raw_data.json
```

`mutants.csv` 中这一行会显示：`mutation_position_in_paper_seq=30`，
`mapped_position_in_target_seq=54`（脚本比对发现目标序列比这篇文献的成熟蛋白序列多出 24 个信号肽
残基，自动把 30 换算成 54），`verification_status=PASSED`（确认 `paper_sequence` 第 30 位确实是
`T`）。

`report.md` 的"突变位点频次分布"一节会显示位点 54 附近如果被多篇文献反复研究，会被列为热点区域，
供后续突变设计优先参考或规避重复劳动。

## 示例 2：仅摘要可用时仍分析并并入结构化报告

若某篇高引用文献是付费墙内容，`fetch-fulltext` 返回 `fulltext_status: "abstract_only"`，智能体仍要
分析标题、摘要和元数据，并在 `analysis.json` 中保留证据边界：

```json
{
  "citation": "Lee et al. 2019, Biochemistry (仅摘要，未获取全文)",
  "doi": "10.1021/acs.biochem.XXXXX",
  "fulltext_status": "abstract_only",
  "analysis_basis": "abstract_only",
  "accessible_content_summary": "已分析标题、摘要、关键词和书目信息；正文、图表和补充材料不可访问。",
  "content_access_note": "非 OA；无法核对完整突变列表、实验条件和动力学数值。",
  "paper_sequence": "",
  "sequence_source_note": "未获取全文，无法确认具体序列",
  "findings": [
    {
      "category": "mechanism",
      "claim": "摘要称 E175Q 导致活性完全丧失，并据此支持 E175 为催化亲核残基。",
      "evidence_type": "author_interpretation",
      "source_locator": "Abstract",
      "confidence": "medium",
      "limitations": "仅有摘要；无法核对测定条件、原始数值、对照和完整作者论证。"
    }
  ],
  "mutants": [
    {
      "raw_description": "E175Q",
      "mutation_position": 175,
      "wild_type_residue": "E",
      "mutant_residue": "Q",
      "property_tested": "catalytic activity",
      "quantitative_result": "",
      "qualitative_result": "摘要称活性完全丧失",
      "assay_method": "",
      "source_locator": "Abstract",
      "evidence_type": "author_interpretation",
      "confidence": "medium",
      "limitations": "缺少文献序列、实验条件与定量结果；位点无法进行序列自校验。",
      "notes": "仅摘要证据"
    }
  ]
}
```

该结论会进入最终报告的逐篇 `findings` 表；E175Q 也会以缺字段但带证据边界的形式进入 `mutants.csv`。
绝不编造摘要里没有的具体数值、实验条件、序列或其他突变列表。

## 示例 3：文献较多时的并行深读与合并

假设预筛选后选中了 22 篇文献，不适合在一个回合里逐篇串行深读。改用拆分方式：

```bash
# 22 个子任务并行深读，各自写一个片段文件
# （子任务的产出示例，比如 c5 对应的片段）
cat output/analysis_fragments/c5.json
```
```json
{
  "citation": "Kim et al. 2021, Biotechnol Biofuels",
  "doi": "10.1186/s13068-021-XXXXX",
  "candidate_id": "c5",
  "fulltext_status": "text",
  "analysis_basis": "partial_html",
  "accessible_content_summary": "已读取 HTML 摘要、Results 部分和表格文本；Methods 与补充材料不可访问。",
  "content_access_note": "部分 HTML，实验细节不完整。",
  "findings": [
    {
      "category": "activity",
      "claim": "N42D 在 pH 9 条件下表现出更高活性。",
      "evidence_type": "direct_observation",
      "source_locator": "HTML Results and Table 1",
      "confidence": "low",
      "limitations": "表格中 N42D/N42Q 两行的数值归属存在歧义。"
    }
  ],
  "paper_sequence": "MKTAYIAKQR...",
  "sequence_source_note": "Figure 2 of the paper",
  "mutants": [
    {
      "raw_description": "N42D",
      "mutation_position": 42,
      "wild_type_residue": "N",
      "mutant_residue": "D",
      "property_tested": "activity at pH 9",
      "quantitative_result": "[uncertain] 表格中该行与N42D/N42Q两个突变体的数值排布存在歧义，此处取更靠近标签的一行",
      "qualitative_result": "碱性条件下活性提升",
      "assay_method": "activity assay at pH 9.0, 50C",
      "source_locator": "HTML Table 1",
      "evidence_type": "direct_observation",
      "confidence": "low",
      "limitations": "表格行归属存在歧义；Methods 不可访问。"
    }
  ]
}
```

跑完全部子任务后先做续跑检查，确认没有遗漏（这一步还没有 `synthesis.json` 也没关系，先合并
突变体数据，缺理论解析的部分之后补跑一次即可）：

```bash
python3 scripts/literature_collector.py merge-analysis \
  --fragments-dir output/analysis_fragments \
  --selected output/selected.json \
  --output output/analysis.json
```

若终端打印类似 `[Warning] 3/22 selected candidate(s) have no fragment yet ... ['c9', 'c14', 'c20']`，
说明这三篇的子任务还没跑完/失败了，针对性补跑这三个即可，不用重跑全部 22 个。确认 22 篇都有片段
后，用一个独立回合通读全部片段写出 `output/synthesis.json`（理论解析+设计启示），再重新合并一次：

```bash
python3 scripts/literature_collector.py merge-analysis \
  --fragments-dir output/analysis_fragments \
  --synthesis output/synthesis.json \
  --output output/analysis.json
```

上面 c5 片段里 `quantitative_result` 打了 `[uncertain]` 标记，合并后 `mutants.csv` 对应行的
`has_uncertain_data` 会是 `Yes`，`report.md` 里这一行最左列会显示 ⚠️，提醒使用者这条数据的表格
归属存在歧义、值得回去核对原文截图，而不是被当成和其他行同等确信的数据直接采信。
