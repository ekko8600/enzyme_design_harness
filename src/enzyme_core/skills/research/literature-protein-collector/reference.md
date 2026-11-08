
# 参考文档：数据源、参数与错误处理

## 1. 支持的文献来源 (`--sources`)

| 来源 key            | 覆盖范围                                                                     | 是否需要 API Key                                                            | 备注                                                                                                                                                                                                                                                      |
| ------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `europepmc`       | PubMed + PMC 全文 + 预印本（bioRxiv/medRxiv/ChemRxiv 等，`SRC:PPR`）+ 专利 | 否                                                                          | 首选来源：唯一能直接给出去标签纯文本全文的来源；bioRxiv/medRxiv 的关键词检索**不要**直接调用 bioRxiv 官方 API（其 API 不支持服务端关键词检索，只能按日期范围下载后本地过滤，范围一大就会超时/触发限流），而是通过 Europe PMC 的预印本索引间接覆盖。 |
| `openalex`        | 几乎全学科学术产出，含引用数、OA 状态                                        | 否（有 Key 限速更宽松）                                                     | `abstract_inverted_index` 需要重建成连续文本，脚本已处理。                                                                                                                                                                                              |
| `pubmed`          | 生物医学文献（NCBI E-utilities）                                             | 否（有 Key 限速从 3/s 提到 10/s）                                           | 走 esearch → efetch(XML) 两步，脚本已内置。                                                                                                                                                                                                              |
| `semanticscholar` | 跨学科，含`openAccessPdf` 直链                                             | 否（有 Key 更稳）                                                           | 公共接口限速较严（约 1 req/s 无 Key），脚本已做限速与 429 重试；**Key 被拒绝（401/403）时会自动降级为匿名访问重试一次**，而不是让整个来源失败。                                                                                                     |
| `crossref`        | DOI 元数据、出版信息、引用数                                                 | 否                                                                          | 摘要经常缺失或含 JATS 标签，脚本会自动去标签；主要用于补全 DOI 与引用计数。                                                                                                                                                                               |
| `arxiv`           | 物理/CS/部分定量生物学预印本                                                 | 否                                                                          | 遵守 arXiv ToS：同 host 至少间隔 3 秒一次请求，脚本已内置节流；额外提取`categories`（如 `q-bio.BM`），帮助识别学科不符的假阳性命中。                                                                                                                  |
| `core`            | 大型开放获取全文聚合器                                                       | **是**（`CORE_API_KEY`），免费注册：https://core.ac.uk/services/api | 未设置 Key 时脚本会打印提示并自动跳过，不影响其他来源。                                                                                                                                                                                                   |
| `doaj`            | 完全开放获取期刊索引（Directory of Open Access Journals）                    | 否                                                                          | 收录的期刊本身就是开放获取政策，返回结果`is_oa` 恒为真；`oa_pdf_url` 常常是出版商落地页而非直接 PDF，下载会被 `_download_pdf` 的 `%PDF` 校验挡住而不是误存一份 HTML。                                                                             |

`--sources` 默认值：`europepmc,openalex,pubmed,semanticscholar,crossref,arxiv`（不含 `core`/
`doaj`，按需显式加入）。

### 为什么没有 Google Scholar / SSRN / Sci-Hub

调研同类项目 [paper-search-mcp](https://github.com/openags/paper-search-mcp)（覆盖 20+ 来源，
含 MCP + Claude Code Skill 两种形态）后，特意对照了它的取舍并复用了几处经验（DOI 兜底提取、
Semantic Scholar Key 被拒后自动降级匿名重试、来源自检工具的思路），但没有照搬它的全部来源列表：

- **Google Scholar / SSRN**：没有官方公开 API，只能爬网页，两者都有明显的反爬/Bot 检测
  （CAPTCHA、Cloudflare 403）——这一点在 paper-search-mcp 自己的兼容性矩阵里也如实标注为
  "⚠️ 不稳定"。爬取这类页面还涉及是否违反其服务条款的问题，与本 Skill"只用合法公开 API"的原则
  冲突，因此不纳入。
- **Sci-Hub**：paper-search-mcp 把它列为可选、默认关闭的兜底全文来源，并在文档里明确提示法律
  风险因司法管辖区而异、由用户自行承担。本 Skill 的核心原则是"只下载合法开放获取全文"（见
  SKILL.md 架构说明），Sci-Hub 绕过出版商付费墙的性质与这一原则直接冲突，**不会**加入，即使作为
  可选/默认关闭的开关也不会。遇到付费墙论文时，本 Skill 的正确行为是诚实标记 `abstract_only`，
  继续分析合法可访问的标题、摘要和元数据并写入 `findings[]`，而不是想办法绕过限制或丢弃该文献。

## 2. `search` 子命令

```bash
python3 scripts/literature_collector.py search \
  --query "<检索词>" [--query "<另一种措辞>" ...] \
  [--sources europepmc,openalex,pubmed,semanticscholar,crossref,arxiv,core,doaj] \
  [--max-results 20] \
  --output <path.json> \
  [--cache-dir cache] [--no-cache] [--config <path>]
```

- `--query` 直接透传给各来源的关键词检索接口，不同来源的语法解析方式不完全一致（如 Europe PMC 支持
  `TITLE:`/`AUTH:`/布尔逻辑，OpenAlex/CrossRef/Semantic Scholar 是全文检索），构造一条较自然、包含
  核心关键词的查询串通常在所有来源上都能取得合理效果；如需要来源专属高级语法，可以针对单个来源单独
  再跑一次 `--sources <单个来源>`。
- **`--query` 可以重复传多次**（`action="append"`），一次调用内跑多种关键词措辞。所有"关键词 ×
  来源"组合会提交到同一个 `ThreadPoolExecutor`（`max_workers=min(16, len(tasks))`），并共享同一套
  进程内的每主机限速状态（`_last_call`/`_rate_lock`）。这是刻意的设计：如果改成让调用方分别开多个
  独立进程并行跑单一 `--query`，每个进程会各自独立计时限速，几个进程一起打同一个来源实际请求频率
  会超过该来源发布的限制，更容易触发 429/503，反而更慢——所以多关键词场景应该用重复 `--query`，
  不要用多进程并行。
- 每个来源的失败都不会终止整体运行，只会在 `source_errors` 里记录并跳过；但如果**全部**"关键词×
  来源"组合都失败，脚本会直接报错退出（而不是安静地写出一份空 `candidates.json`）——这种情况通常是
  网络/凭证问题，不应该被解读成"这个蛋白没有相关文献"。
- 去重前会先做 **DOI 兜底提取**：某些来源（如 DOAJ、CrossRef）的记录有时缺少独立的 DOI 字段，但
  `url`/`oa_pdf_url` 里其实嵌了 `10.xxxx/yyyy` 形式的 DOI，脚本用正则把它提取出来回填，避免同一篇
  文献因为"一个来源填了 DOI、另一个来源没填"而被误判成两条不同记录。
- 去重优先按 DOI（大小写不敏感），其次按标准化标题（去标点、转小写）；同一篇文献被多个来源**或多个
  关键词**命中时都会合并 `sources` 字段并保留信息更全的字段值（如引用数取最大值、`is_oa` 任一来源
  为真即为真）——用多种措辞扩大召回率不会在输出里产生重复行。

**输出结构** (`candidates.json`)：

```json
{
  "queries": ["...", "..."],
  "sources_requested": ["europepmc", "..."],
  "source_errors": [{"source": "openalex", "query": "...", "error": "..."}],
  "total_raw_results": 42,
  "total_unique_candidates": 30,
  "candidates": [
    {
      "candidate_id": "c1",
      "title": "...", "abstract": "...", "authors": ["..."],
      "year": 2020, "journal": "...",
      "doi": "10.xxxx/yyyy", "pmid": "...", "pmcid": "PMC...", "arxiv_id": null,
      "citation_count": 55, "is_oa": true, "oa_pdf_url": "https://...",
      "url": "https://...", "sources": ["europepmc", "pubmed"],
      "categories": ["q-bio.BM"]
    }
  ]
}
```

`queries` 字段永远是数组（哪怕只传了一个 `--query`），下游的 `fetch-fulltext`/`from-analysis
--search-summary` 都读的是这个复数字段名。

`categories` 目前只有 `arxiv` 来源会填充（其余来源保持空数组）；对生物医学类蛋白检索，一篇明明
命中了关键词、但 `categories` 是 `cs.LG`/`math.OC` 之类的记录，通常就是跑题的假阳性，可以在预筛选
时优先排除，不需要读摘要。

## 2b. `merge-candidates` 子命令

```bash
python3 scripts/literature_collector.py merge-candidates \
  --inputs output/candidates_round1.json output/candidates_round2.json [...] \
  --output output/candidates.json
```

和 `search` 内置的多 `--query` 支持是互补关系：多 `--query` 处理"同一次调用内跑多种措辞"，
`merge-candidates` 处理"检索本来就是分开几次做的"（不同时间、不同 `--sources`、甚至不同人跑的
`search`），把多份 `candidates.json` 合并成一份，复用与 `search` 完全相同的 DOI 兜底提取 + 去重
逻辑，`queries`/`sources_requested` 取并集，`source_errors` 取并集，`total_raw_results` 累加。
`--inputs` 至少传一个文件路径，可以传任意多个（`nargs="+"`）。

## 3. `fetch-fulltext` 子命令

```bash
python3 scripts/literature_collector.py fetch-fulltext \
  --selected output/selected.json \
  --output output/raw_data.json \
  [--fulltext-dir output/fulltext] [--concurrency 10] \
  [--cache-dir cache] [--no-cache] [--config <path>]
```

`--concurrency`（默认 10）是同时处理的候选文献数，可以放心调更高（比如候选来自几十个不同期刊时调
到 20-30）——实际请求频率由每个域名各自的 `RATE_LIMITS` 节流阀控制，不会因为整体线程数变多就对
某一个网站发得更猛，调高主要是减少"等别的候选抓完才轮到我"的空闲等待。

全文解析优先级（第一个成功即停止）：

1. **Europe PMC 全文 XML**（若候选自带 `pmcid`，或能用 `doi` 反查到 `pmcid`）→ 去标签后存为
   `<candidate_id>.txt`，这是质量最高、噪声最少的来源。
2. **候选自带的 `oa_pdf_url`**（来自 OpenAlex/Semantic Scholar/CrossRef/arXiv/DOAJ）。
3. **Unpaywall**（需要 `doi` + 真实邮箱环境变量 `UNPAYWALL_EMAIL`）→ 取 `best_oa_location` 的链接。
   **用占位/虚构邮箱会被 Unpaywall 拒绝（HTTP 422）**，务必设置真实邮箱。
4. **CORE**（需要 `doi` + `CORE_API_KEY`）→ 用 `downloadUrl`。
5. **DOAJ（按 DOI 反查）** → 取 `fulltext` 类型的链接；这条链接经常是出版商落地页而不是直接 PDF，
   所以放在最后一档，命中率不高，但不花额外的 API Key 成本，顺手试一下无妨。

第 2–5 条都是"一个 URL，可能是 PDF 也可能是网页"，统一交给 `_fetch_fulltext_from_url()` 处理，
**不会因为响应不是 `%PDF` 就直接放弃**：

1. 先尝试直接当 PDF 下载（校验以 `%PDF` 开头且体积 > 2KB）。
2. 不是 PDF 则把响应当 HTML 解析，找 `<meta name="citation_pdf_url">`——这是学术出版商
   （PMC、PLOS、Frontiers、MDPI、BMC、Wiley、Springer 等主流平台的落地页都有）专门为文献
   管理软件/索引工具埋的标准标签，指向真正的 PDF 直链；找到就跳转过去重复第 1 步下载。
   实测 PLOS ONE 的落地页正是如此——`oa_pdf_url` 指向文章落地页而非 PDF，通过这个标签成功换算
   到了真正的 8 页 PDF 直链。
3. 找不到 `citation_pdf_url` 但有 `<meta name="citation_fulltext_html_url">`（部分出版商只提供
   HTML 全文视图，没有独立 PDF），则改为抓取该 HTML 全文页并提取正文。
4. 以上都没有，就对当前这个落地页本身做兜底的正文提取：优先取语义化的 `<article>` 标签内容
   （多数现代学术站点模板都有），否则退化为拼接所有 `<p>` 标签文本（避免把导航栏/按钮文字也
   当成正文）。

  **长度不是判断"这是真全文还是只是摘要"的可靠信号**——实测发现一个付费墙的 Springer 落地页，
  正文提取后残留了"Cite this article / Save article / Aims and scope"这类期刊导航样板文字外加
  摘要，长度也有 3300 多字符，如果只靠"够长就接受"的阈值会被误判成全文。因此提取结果 **必须同时
  满足**：长度 ≥ 1500 字符，且（长度 ≥ 6000 字符，**或者** 文本里出现 Methods/Results/Discussion/
  References/Acknowledgments 等真实论文正文才会有的章节关键词）——真实全文几乎总能满足其中一个
  条件，而纯摘要页几乎不可能同时越过长度门槛又意外出现这些关键词。不满足则视为提取失败，falls
  through 到下一档来源或最终标记 `abstract_only`，而不是把一份"摘要+网站样板文字"当成全文存下来。

命中第 2–4 步时会在 `fulltext_status: "text"` 的基础上额外记录 `extraction_method` 字段
（`citation_pdf_url` / `citation_fulltext_html_url` / `landing_page_readable_text`），方便追溯
这份全文到底是原生 PDF、出版商 HTML 全文页、还是兜底正文抓取——后者质量不如前两者可靠，深读时可以
适当留意。这一层设计参考了 [`@extractus/article-extractor`](https://github.com/extractus/article-extractor)
（一个通用网页正文提取库）的思路，但没有照搬它面向任意网页的 Readability 算法，而是针对学术文献
场景做了更可靠的简化：优先信任出版商自己声明的 `citation_*` 元数据标签，只有在标签缺失时才退化到
通用的"取 `<article>`/`<p>` 标签正文"启发式。

五条路径全部失败（含 HTML 兜底提取都拿不到 ≥500 字符正文）则标记
`fulltext_status: "abstract_only"`，`local_path` 为 `null`。

**输出结构** (`raw_data.json`)：候选字段全部保留，并新增：

```json
{
  "fulltext_status": "text | pdf | abstract_only",
  "fulltext_source": "europepmc | direct_oa_link | unpaywall | core | doaj | cached_local_file | null",
  "extraction_method": "direct_pdf | citation_pdf_url | citation_fulltext_html_url | landing_page_readable_text (仅 fulltext_source 非 europepmc/cached_local_file 时存在)",
  "fulltext_pmcid": "PMC... (仅 europepmc 来源时存在)",
  "local_path": "output/fulltext/c1.txt 或 .pdf 或 null"
}
```

`fulltext_source: "cached_local_file"` 表示这次运行直接复用了上一次已经下载好的本地文件，没有
重新发起任何网络请求——`fetch-fulltext` 是幂等的：重跑同一个命令前会先检查
`<fulltext-dir>/<candidate_id>.txt`/`.pdf` 是否已存在且非空，存在就直接复用（不会重新查
Europe PMC/Unpaywall/CORE/DOAJ），这样中断后重跑、或后续给 `selected.json` 追加新论文后再跑一次，
都不会对已经成功抓到的文献重复计费/重复限速等待。想强制重新抓某一篇，删掉对应本地文件即可。

顶层还会带上 `fulltext_summary`（各状态计数）以及从 `selected.json` 继承来的
`sources_requested`/`total_unique_candidates` 等字段，供 `from-analysis --search-summary` 直接
复用生成报告开头的检索概述。

## 4. `from-analysis` 子命令

详见 SKILL.md 中的"分析 JSON 格式"一节。校验规则（校验失败会汇总列出全部问题并以非零状态退出，不
会生成残缺产物）：

- `target_sequence` 允许为空（此时不做位置换算，`mapped_position_in_target_seq` 全部为 `N/A`），
  但若非空则必须是合法的氨基酸序列（允许标准 20 种残基 + `BZXJUO` 扩展/模糊码）。
- `papers` 必须是列表；每篇至少要有 `citation` 或 `doi` 之一用于标识来源。
- 每篇必须声明 `analysis_basis`（`full_text`/`partial_html`/`abstract_only`/`metadata_only`）、
  `accessible_content_summary` 和 `findings`。`abstract_only` 获取状态不得伪装成全文分析依据。
- `findings` 必须为列表；每条必须包含 `claim`、`source_locator`、`limitations`，并使用受控的
  `evidence_type`（`direct_observation`/`author_interpretation`/`agent_inference`）和
  `confidence`（`high`/`medium`/`low`）。这使非 OA、摘要和部分 HTML 的结论同样进入最终报告且保留边界。
- `paper_sequence` 若提供，同样会做合法性校验（自动去空白/数字，非法字符报错）。
- `mutants[].mutation_position` 若提供必须能转成整数；`wild_type_residue`/`mutant_residue` 若提
  供必须是单个合法氨基酸字母。

**自我校验状态含义**（`verification_status` 字段）：

| 状态                 | 含义                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------- |
| `PASSED`           | 提取出的野生型残基与`paper_sequence` 对应位置一致，可信度高                                           |
| `FAILED`           | 不一致——可能是 LLM 提取幻觉、文献本身编号方式不同（如成熟肽 vs 前体蛋白）、OCR/印刷误差，需要人工复核 |
| `AUTO-FILLED`      | 未提供野生型残基，脚本直接从`paper_sequence` 对应位置读取并回填                                       |
| `OUT_OF_RANGE`     | 给出的位置超出了`paper_sequence` 长度范围，需要复核位置或序列本身是否正确                             |
| `INVALID_POSITION` | 位置字段无法解析成整数                                                                                  |
| `N/A`              | 该突变体缺少位置或该文献缺少`paper_sequence`，无法校验                                                |

`mapped_position_in_target_seq` 同理还可能是 `"Gap (Insertion)"`（该位置在目标序列中对应一个
缺口，说明这是文献序列相对目标序列的插入残基）或 `"Not Found"`（理论上不应出现，出现说明比对异常，
需要检查两条序列是否输入有误）。

**`[uncertain]` 标记与 `has_uncertain_data` 列**：`raw_description`/`property_tested`/
`quantitative_result`/`qualitative_result`/`assay_method`/`notes` 六个字段中任意一个包含字面量
`[uncertain]` 子串，`has_uncertain_data` 就会标为 `Yes`（`UNCERTAIN_MUTANT_FIELDS` 常量定义了检测
范围）；标记本身不会被剥离，CSV/报告里仍能看到完整的 `[uncertain] ...` 原文。这个约定思路借鉴自
[Deep-Research-skills](https://github.com/Weizhena/Deep-Research-skills) 的结构化多智能体研究
工作流——不确定的信息不是被静默丢弃或被自信地当成确定结论写出，而是显式标注、依然保留，交给使用
者自行判断是否需要复核。

**`--output-svg`**（可选）：突变热点 SVG 图的输出路径，默认写在 `--output-report` 同目录下的
`mutation_hotspots.svg`。柱状图按 `mapped_position_in_target_seq` 聚合，柱高与不透明度由"研究过
该位点的不同文献数量"决定（同一篇文献报道多个突变体只算一次），标签取该位点第一个突变的
`raw_description`（多于一个会标 `(+N)`）。`target_sequence` 为空、或没有任何突变体成功换算到目标
坐标系时会跳过生成并打印 `[Info]`，不会报错中断整个 `from-analysis`。**注意**：SVG 热点统计对
`[uncertain]` 标记的突变体不做区分，会和确定的记录一起计入频次——查看某个热点位点的具体支撑证据
时仍应回到 `mutants.csv`/报告详情表核实是否有不确定标记。

## 4b. `merge-analysis` 子命令（合并并行深读产出的文献片段）

```bash
python3 scripts/literature_collector.py merge-analysis \
  --fragments-dir output/analysis_fragments \
  [--synthesis output/synthesis.json] \
  [--selected output/selected.json] \
  --output output/analysis.json
```

用于文献数量较多、深读工作被拆成"每篇文献一个子任务"并行执行时，把多个小 JSON 文件合并成
`from-analysis` 需要的单一 `analysis.json`。设计动机与用法见 SKILL.md"可选：大批量文献并行深读"
一节；这里补充脚本行为细节：

- `--fragments-dir`：目录下所有 `*.json` 文件都会被读取，按文件名排序后拼进最终 `papers` 数组；
  文件名本身不重要（脚本读的是文件内容里的 `candidate_id`），但建议用 `<candidate_id>.json` 命名
  方便人工核对。任何一个片段文件解析失败（JSON 语法错误）都会汇总列出全部错误文件并以非零状态
  退出，不会跳过坏文件继续合并出一份不完整的结果。
- `--synthesis`：可选。不传时合并结果里 `target_protein_name`/`target_sequence`/
  `theoretical_analysis_markdown`/`design_implications_summary` 都会是空值，此时打印警告但不会
  报错退出（允许你先合并突变体片段、后补理论解析）。
- `--selected`：可选的续跑检查。传入后会对比 `selected.json` 里的 `candidate_id` 集合与
  `--fragments-dir` 里实际存在的片段，把还缺片段的 `candidate_id` 列表打印出来（`[Warning]`，不
  会中断合并——已经产出的片段照样合并进去，缺的部分等对应子任务补跑完再重新合并一次即可）。
- 合并逻辑本身很简单（顶层字段来自 `--synthesis`，`papers` 数组来自 `--fragments-dir`），后续
  `--from-analysis` 的校验/比对/自校验逻辑完全复用，不需要为"合并出来的" analysis.json 单独写
  一套处理流程。

## 5. `align` 子命令（独立工具）

```bash
python3 scripts/literature_collector.py align \
  --paper-seq "<文献序列>" --target-seq "<目标序列>" --pos "45,23,10"
```

对两条序列只做一次 Needleman-Wunsch 全局比对，一次性换算多个位置，返回 JSON 数组
`[{"position", "mapped_position", "aligned_paper_residue", "aligned_target_residue"}, ...]`。
适合临时核对个别位点，不生成 CSV/报告。

## 5b. `check-sources` 子命令（连通性自检，非检索步骤）

```bash
python3 scripts/literature_collector.py check-sources [--probe-query protein] [--config <path>]
```

对 `SOURCE_FUNCS` 里的每一个来源（含 `core`/`doaj`）发一次只要 1 条结果的最小查询，打印
`OK`/`OK (0 results)`/`FAIL`/`SKIPPED (no API key)`（`core` 在没有 `CORE_API_KEY` 时会直接显示
跳过，而不是伪装成"查询到 0 条结果"）及每次查询耗时，随后列出 `CONFIG_ENV_MAP` 里每个凭证字段
当前是"set"还是"not set"。这是一个**一次性诊断工具**，不是检索流水线的一部分：

- 用途：刚在新环境部署这个 Skill 时快速确认网络/凭证是否都通；或者某次 `search` 返回的候选数量
  明显低于预期时，先排除"某个来源本身就连不通"这个可能性，而不是先怀疑目标蛋白没有文献。
- **不测试全文抓取链路**：Unpaywall/CORE/DOAJ-by-DOI/Europe PMC 全文这几步都需要一个真实的
  DOI/PMCID 才能验证，`check-sources` 用的是空泛的探测查询，测不到这几步；怀疑全文抓取有问题时，
  直接对一条真实候选跑 `fetch-fulltext` 排查。
- `FAIL` 大概率是外部服务临时不稳定或 Key 失效（工具输出的错误信息里通常有 HTTP 状态码），不代表
  这个 Skill 本身坏了；`OpenAlex`/`Semantic Scholar` 匿名访问在网络繁忙时段出现 503/429 是常见的
  正常现象，见下方"常见问题排查"。

## 6. 配置一览（`config.json` + 环境变量）

`load_config()`（`literature_collector.py` 与 `ocr_pdf.py` 共用）按以下优先级解析每个字段：
**对应环境变量 > `config.json` 里的同名字段 > 空**。`config.json` 的查找顺序是：`--config` 显式
指定的路径 → 当前工作目录下的 `./config.json` → Skill 根目录（`scripts/` 的上级目录）下的
`config.json`。仓库只提供字段全空的 `config.example.json`；需要文件配置时复制为已被 `.gitignore`
忽略的 `config.json`。优先使用环境变量或仓库外的 `--config` 路径，绝不能提交填有密钥的配置文件。

| `config.json` 字段         | 环境变量                                 | 用途                                                | 是否必需                            |
| ---------------------------- | ---------------------------------------- | --------------------------------------------------- | ----------------------------------- |
| `contact_email`            | `LIT_COLLECTOR_EMAIL` / `USER_EMAIL` | User-Agent 联系方式                                 | 建议设置                            |
| `unpaywall_email`          | `UNPAYWALL_EMAIL`                      | Unpaywall API 要求的真实邮箱                        | 想用 Unpaywall 这一路全文来源则必需 |
| `ncbi_api_key`             | `NCBI_API_KEY`                         | PubMed E-utilities 提速（3→10 req/s）              | 可选                                |
| `openalex_api_key`         | `OPENALEX_API_KEY`                     | OpenAlex 提速                                       | 可选                                |
| `semantic_scholar_api_key` | `SEMANTIC_SCHOLAR_API_KEY`             | Semantic Scholar 提速/更稳定                        | 可选                                |
| `core_api_key`             | `CORE_API_KEY`                         | 启用 CORE 来源                                      | 想用 CORE 则必需                    |
| `paddleocr_vl_token`       | `PADDLEOCR_VL_TOKEN`                   | `scripts/ocr_pdf.py` 的 PaddleOCR-VL Bearer Token | 想用 OCR 全文解析则必需             |

除 `contact_email`/`unpaywall_email` 外，其余字段若只在 `config.json` 里配置（没有设置对应环境
变量），`load_config()` 会打印警告提醒不要把填好密钥的 `config.json` 提交到版本库；所有密钥的申请
都应遵循 `credentials` skill 的安全凭证协议，不要猜测或编造。`doaj` 来源不需要任何凭证，表里没有
它的条目。

## 7. `scripts/ocr_pdf.py`（可选的 PDF OCR / 文档结构解析）

本 Skill 里唯一依赖第三方包（`requests`）和外部付费/限额服务（PaddleOCR-VL）的脚本，用于把下载
到的 PDF 转成保留表格结构的 Markdown，供智能体更可靠地读取密集数据表格。不使用这个脚本完全不影响
其余流程（`search`/`fetch-fulltext`/`from-analysis`/`align` 仍然零依赖）。

```bash
# 单篇
python3 scripts/ocr_pdf.py --file output/fulltext/c3.pdf --output output/fulltext_ocr/c3.ocr.md

# 批量（目录下所有 *.pdf；已存在同名 .ocr.md 的会跳过，可安全重跑/续跑）
python3 scripts/ocr_pdf.py --dir output/fulltext --output-dir output/fulltext_ocr
```

*可选参数*：`--token`/`--config`（Token 解析顺序为 `--token` > `PADDLEOCR_VL_TOKEN` 环境变量 >
`config.json` 里的 `paddleocr_vl_token`，与本 Skill 其余凭证一致，见第 6 节）、`--model`（默认
`PaddleOCR-VL-1.6`）、`--poll-interval`（轮询间隔秒数，默认 5）、`--timeout`（任务超时秒数，默认
1800）、`--use-doc-orientation-classify`/`--use-doc-unwarping`/`--use-chart-recognition`（对应
PaddleOCR-VL 的可选预处理开关，默认全部关闭，与官方示例一致）。

*工作原理*：提交异步任务（本地文件走 multipart 上传，`http(s)://` 开头的路径走 `fileUrl` JSON
提交）→ 轮询 `pending`/`running`/`done`/`failed` 状态直至完成或超时 → 下载结果 JSONL，把每一页的
`markdown.text` 按页拼接成一份 Markdown（页与页之间用 `---` 分隔，并保留 `<!-- page N -->` 注释
方便定位），页内引用的图片另存到 `<输出文件去掉.md后缀>_images/` 目录。

*失败处理*：任务提交失败、状态查询失败、任务本身 `state == "failed"` 都会抛异常并打印错误信息；
批量模式下单个文件 OCR 失败只会跳过该文件并继续处理其余文件，不会中断整个批次。

*什么时候值得跑*：并不是所有 PDF 都需要 OCR——纯文字为主、没有复杂表格的论文，直接用 Read 工具读
PDF 通常就够了。**当文献里出现突变体-表型对照表、动力学参数表、多列 SI 表格时，先 OCR 再读能明显
降低把表格数字读串行/读到错误突变体上的风险**，值得多花几分钟等异步任务完成。

## 8. 常见问题排查

| 现象                                                                  | 可能原因 / 处理                                                                                                                                                                                                                                                                                        |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `openalex` 返回 503 "Anonymous search is temporarily rate-limited"  | OpenAlex 匿名池临时过载，属服务端限流，脚本已自动退避重试；持续出现建议申请`OPENALEX_API_KEY`。                                                                                                                                                                                                      |
| Unpaywall 返回 422 "Please use your own email"                        | `UNPAYWALL_EMAIL` 未设置或是占位邮箱，改成真实邮箱地址。                                                                                                                                                                                                                                             |
| `semanticscholar` 频繁 429                                          | 公共接口限速很严，申请`SEMANTIC_SCHOLAR_API_KEY` 或减小并发/`--max-results`。                                                                                                                                                                                                                      |
| 某文献`fulltext_status` 一直是 `abstract_only`                    | 说明五条合法 OA 路径都没有找到全文，通常是纯付费墙文献；不要跳过，先以 `analysis_basis: abstract_only` 分析标题/摘要/元数据并写入 `findings[]`。若用户可合法提供机构订阅 PDF，可放入 `output/fulltext/<candidate_id>.pdf` 后再升级分析依据并复核原结果。 |
| `semanticscholar` 打印 "rejected the configured API key" 然后继续跑 | 这不是失败——`SEMANTIC_SCHOLAR_API_KEY` 被拒绝（401/403）时脚本会自动退回匿名访问重试一次，只是匿名访问限速更严；如果紧接着又报 429，说明确实需要申请/更换一个有效的 Key。                                                                                                                          |
| `search` 直接报错退出（而不是打印 0 条结果）                        | 说明这次请求的所有来源全部失败（通常是断网或本机代理/DNS 问题），脚本认为这种情况不该被误读成"没有相关文献"而选择直接报错；先跑`check-sources` 定位是哪一层出的问题。                                                                                                                                |
| CSV 中`verification_status` 大量 `FAILED`                         | 优先检查`paper_sequence` 是否真的取自该文献（而不是误用了目标序列或另一篇文献的序列），以及该文献的残基编号是否包含信号肽/前导肽（常见的"成熟蛋白编号 vs 全长编号"错位）。                                                                                                                           |
| `mutation_hotspots.svg` 没有生成                                    | `target_sequence` 为空，或 `analysis.json` 里所有 `paper_sequence` 都缺失/所有位点都换算失败——检查 `from-analysis` 运行日志里的 `[Info]`/`[Warning]` 输出。                                                                                                                              |
| `ocr_pdf.py` 报 `ModuleNotFoundError: requests`                   | 只有这一个脚本需要第三方包，运行`pip install requests` 即可，不影响其余脚本。                                                                                                                                                                                                                        |
| `ocr_pdf.py` 报 "No PaddleOCR-VL API token provided"                | `--token`/`PADDLEOCR_VL_TOKEN`/`config.json` 里的 `paddleocr_vl_token` 都没有配置；通过 `credentials` skill 的安全凭证协议向用户申请，不要硬编码 Token。                                                                                                                                     |
| `ocr_pdf.py` 长时间停在 `running`                                 | 大页数 PDF 或服务端排队都可能耗时较久，脚本会持续轮询直到`--timeout`（默认 1800 秒）；确有必要可以调大 `--timeout` 或改用 `--poll-interval` 更小的值更快看到进度。                                                                                                                               |
| `report.md` 里"文献筛选结果汇总"表的跳转链接点了没反应              | `_gfm_anchor()` 只是近似复刻主流 Markdown 渲染器的自动锚点算法，两篇文献的 `citation` 文本完全相同时，渲染器会给第二个标题自动加 `-1` 后缀消除冲突，而生成链接时预测不到这个后缀；给每篇文献用不会重复的 `citation`（比如带上年份/期刊）可以避免这个问题，链接失效不影响报告其余内容的正确性。 |
| `merge-analysis` 报某个片段文件解析失败                             | 片段文件本身不是合法 JSON（多半是子任务写文件时被截断，或者把非 JSON 说明文字也写进了文件），检查报错信息里列出的具体文件，修正后重新跑`merge-analysis`（其他片段不受影响）。                                                                                                                        |
