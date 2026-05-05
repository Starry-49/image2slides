<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides 图标" width="128" height="128">
</p>

# Image2Slides

**语言:** [English](./README.md) | 中文 | [日本語](./README.ja.md)

Image2Slides 是一个 Codex plugin 和 CLI workflow，用于把 GPT-image slide 视觉结果转成可编辑 PowerPoint。它默认用 Codex native `image_gen` 生成 GPT-image-2 视觉底版，保持 source-locked 数据图表不被改写，再把文字作为可编辑 PPT 文本浮在匹配 background 之上，最后用渲染结果和 completed 参考图做像素/patch 级验收。

Plugin 入口是 `/image2slides`，核心说明在 [skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md)，确定性脚本在 [skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py)。

## 只需复制这段 Prompt

你只需要把下面这段复制给本地 Codex 或 coding agent。让 agent 负责装依赖、搭 workflow、导入 Codex App plugin。

```text
请从 https://github.com/Starry-49/image2slides 在本地安装 Image2Slides，并让它在 Codex App 中作为 /image2slides plugin 可用。

请端到端完成：
1. 把仓库 clone 或更新到本地 workspace。
2. 先阅读 README.md、.codex-plugin/plugin.json、skills/image2slides/SKILL.md、package.json、pyproject.toml 和 tests，再做任何改动。
3. 按 pyproject.toml 把 helper workflow 作为 editable Python project 安装，确保 numpy、Pillow 等必需依赖在 doctor/tests 前可用。
4. 从仓库根目录导入或刷新 Codex App plugin，manifest 使用 .codex-plugin/plugin.json。不要把 Codex 指到 skills/ 子目录。
5. 确认 Image2Slides plugin 已经被索引，并且 /image2slides 可用。
6. 运行 plugin doctor 和最小测试。
7. 创建一个小的本地 deck workspace，并确认 workflow 目录可以初始化：wiki、prompts、completed、background、analysis、pptx、reports。
8. 确认 GPT-image-2 默认走 Codex native image_gen，不要向我索要 OPENAI_API_KEY，除非我明确要求 SDK/API fallback。
9. 汇报最终 plugin 路径、依赖/运行时选择、验证证据，以及我是否还需要手动刷新 Codex App。

所有生成的 deck 和私人知识库资料都留在本地。不要发布示例产物，除非我明确要求。
```

## 用户必填输入

`/image2slides` 在生成前必须拿到以下字段：

- slides 底版风格和色调
- slides 画面比例
- slides 页面数量
- slides 用途：演讲或展示
- slides 呈现场景：学术、企业、课堂、生活，或其他明确场景
- slides 知识库：用户提供的文本、图片、引用资料或资料路径

可以从 [examples/spec.example.json](./examples/spec.example.json) 开始。

## 从 Workflow 到 Results

1. 创建项目 wiki 和目录结构：

   ```bash
   image2slides init --project decks/my-deck --spec examples/spec.example.json
   ```

   会落盘 `project.json`、`wiki/00_project_brief.md`、`wiki/01_wiki_map.md`、`wiki/02_content_boundary.md`、`wiki/03_source_registry.yml` 和 `wiki/04_slide_plan.json`。

2. 填写内容边界：

   - `wiki/grep/` 放必须来自资料、引用、web/search 的事实内容。
   - `wiki/generate/` 放可生成的叙事、比喻、教学例子和表达草稿。
   - 更新 `wiki/02_content_boundary.md`，让每一页都明确 grep_required 和 generation_allowed。

3. 生成 prompt 队列：

   ```bash
   image2slides queue --project decks/my-deck
   ```

   输出：
   - `prompts/completed_prompts.jsonl`
   - `prompts/background_edit_prompts.jsonl`

   `queue` 会先归一化 source-layer panel，让每个 panel bbox 按 trim 后 source 图比例和设定的内缩边距生成。也可以显式检查或应用：

   ```bash
   image2slides normalize-source-panels --project decks/my-deck --check --strict
   image2slides normalize-source-panels --project decks/my-deck
   ```

4. 用 Codex native `image_gen` 生成 GPT-image-2 completed 全页参考图。

   默认 plugin workflow 使用 Codex native image generation，不需要 `OPENAI_API_KEY`。每张 `completed/slide_XX_completed.png` 必须是 GPT-image-2 生成的、包含目标可见文字的全页参考图。禁止用 PPTX/PDF 渲染、页面截图、本地绘图或确定性 compositor 输出填充 `completed/`。把 native image_gen 输出复制到 `completed/` 后先注册：

   ```bash
   image2slides register-completed --project decks/my-deck
   ```

5. 基于 completed 图用 GPT-image-2 edit 生成无文字 background。

   background pass 只移除文字，保留和 `completed/` 对齐的布局、图形、颜色和几何位置。把 native image_gen edit 输出复制到 `background/` 后必须注册，后续步骤才允许继续：

   ```bash
   image2slides imagegen --project decks/my-deck --phase background --dry-run
   image2slides imagegen --project decks/my-deck --phase background --execute
   image2slides register-background --project decks/my-deck
   ```

   对于不能改写的数据/结果，把原始图表放在 `wiki/sources/`，再把 trim 后的 source 图精确 patch 到已有的 image_gen completed/background 成对图片中：

   ```bash
   image2slides compose-source-locked --project decks/my-deck
   image2slides audit-layout --project decks/my-deck --strict
   ```

   输出到 `background/slide_XX_background.png` 和 `background/.image2slides_background_provenance.json`。background prompt 会要求只移除文字，保留布局、图形、颜色和几何位置。`analyze`、`build-pptx`、`compose-source-locked` 和 `qa` 会拒绝未注册的本地模板、截图、确定性绘图、过期 background，或没有绑定当前 completed provenance 的 background 批次。
   layout audit 会固定检查 source 图是否在检测到的 native panel 内、panel 内缩比例是否匹配 trim 后 source 图比例、source panel/image 之间是否互相重叠、是否遮挡可编辑文字、figure-first 页面中图是否压过文字成为视觉主角，以及是否在 native imagegen panel 上重复添加圆角框。
   source fitting 会先裁掉 blank 像素，并在 GPT-image-2 prompt 中要求 panel 比例反向匹配 source 图片；compose 时会识别生成底版或 background 中的真实 panel 边界，再按 `fit_margin_px` 内缩，在四条平行边约束下最大化缩放并最小化剩余 slack，最后让图片中心和内缩 panel 中心对齐。生成式/native panel 的内缩边距最小为 32 px，即便某个 layer 设置得更小也不能突破；source 图边缘连通的近白 blank 默认透明贴入，避免图表自带白底矩形压住生成 panel。计划里的 `bbox` 只允许作为 panel detector 的搜索提示；如果它指向错误插图位置，Image2Slides 也必须使用真实生成 panel，或让 strict QA 失败。如果真实生成 panel 比例与 trim 后 source 比例不一致，必须重生 GPT-image-2 panel，不能本地拉伸、裁切或假装通过。
   `queue` 还会把每个 source panel 写入 `wiki/04_slide_plan.json` 的 `non_editable_image_panel` layout boundary。可编辑 PowerPoint 文字不能进入这些区域。source 图、图表、示意图、icon 内部原本就存在的文字属于图片资产内容，默认保留，不纳入可编辑文字排版诊断，除非用户明确要求抽取。

   Figure-first 标准：
   - 只有数值、坐标轴、统计关系必须保持不变的 result/data figure 才作为精确 source panel 保留
   - 装饰性示意图、icon、内部文字可抽取的 text-heavy screenshot 不预留大图位
   - 文本要少，只负责帮助读图，不和图抢主视觉
   - 多图信息太挤时拆页，不要把每张图都缩到看不清

   可选 API/SDK completed 生成 fallback 只在明确需要时使用：

   ```bash
   image2slides imagegen --project decks/my-deck --phase completed --dry-run
   image2slides imagegen --project decks/my-deck --phase completed --execute
   ```

6. 分析文字区域和 blank 区域：

   ```bash
   image2slides analyze --project decks/my-deck
   ```

   输出：
   - `analysis/slide_XX.json`
   - `analysis/manifest.json`

   分析器对比 `completed/` 和 `background/`，把像素差视为文字 mask，识别主背景色、文字区域和低变化 blank 区域。
   在把这个 mask 当作文字校正目标之前，需要先确认 completed/background 之间的差异主要是文字。如果 panel、figure、illustration 或其他非文字几何也发生漂移，应重新生成 GPT-image-2 background edit，而不是围绕污染后的 mask 调 PPT 文字。

7. 生成可编辑 PPTX：

   ```bash
   image2slides build-pptx --project decks/my-deck
   ```

   输出：
   - `pptx/image2slides.pptx`

   每页使用对应 background 作为底图，`wiki/04_slide_plan.json` 中的文字会作为可编辑 PowerPoint 文本框浮在上方。

8. 渲染并验收：

   ```bash
   image2slides qa --project decks/my-deck --strict
   ```

   输出：
   - `reports/rendered/`
   - `reports/qa_similarity.json`
   - `reports/qa_report.md`
   - `reports/source_layer_audit.md`
   - `reports/text_alignment_audit.md`
   - `reports/source_render_audit.md`
   - `reports/background_audit.md`
   - `reports/content_boundary_audit.md`
   - `reports/content_boundary_overlays/`

   QA 会在本地有 LibreOffice 和 `pdftoppm` 时重新渲染 PPTX，再与 `completed/` 做像素和 patch 相似度比较，并再次执行固定的 source-layer layout audit；同时逐个检查 rendered source crop 是否匹配最终 source-locked background crop，确认 source 透明 blank 区域仍与 source-free native panel 一致，并检查可编辑文字是否在局部上对齐 `completed` 参考图；strict 模式也会拒绝 byte-identical 或视觉近重复的 background 页面。默认 strict 相似度预设要求整体 pixel similarity >= 0.90，同时允许 GPT-image-2 参考文字与可编辑 PowerPoint 文字之间正常的字体栅格化差异。每次 render 前会清理旧截图，避免 stale render 造成假阳性。
   content-boundary audit 是强化布局置信度的诊断项：它识别 main blank color，定义 `blank_zone`、`forbidden_zone`、`text_fill_zone`，把 source panel 当作不可写文字区域，排除图片内部自带标签，并写出 overlay 给 Codex/LLM review。需要单独运行时用 `image2slides audit-boundaries --project decks/my-deck --rendered-dir reports/rendered`。只有当希望这些 warning 直接让 QA 失败时，才给 `qa` 加 `--boundary-strict`。

如果任何阶段脱离这个顺序，必须从第一个失效阶段开始清掉所有下游产物并回到最后可信 checkpoint。例如 `completed/` 失效就重做 completed、background、analysis、PPTX 和 QA；`background/` 失效就重做 background、analysis、PPTX 和 QA。

9. 做一次简短人工细节核对。

   打开 `pptx/image2slides.pptx`，只检查自动 QA 不应该过度拟合的小细节：图片和 panel 的视觉留白、换行、字号、明显文字溢出、页面之间的一致性，以及 source data/results 是否仍未被改写。这个步骤是 strict QA 之后的最终轻量审查，不替代 QA。

## Howitworks 示例

仓库内包含 [howitworks/](./howitworks/) 作为完整 workflow 的最小心智模型。它包含知识库文档、抽取文本和图片、结构化 wiki、GPT-image-2 native 底版、source-locked completed/background 图片、最终 PPTX、渲染结果、QA 报告，以及示例说明。

![Howitworks 人工审查 PDF 预览](./howitworks/image2slides_run/pptx/image2slides_preview.png)

主要可编辑结果是 [howitworks/image2slides_run/pptx/image2slides.pptx](./howitworks/image2slides_run/pptx/image2slides.pptx)。人工审查后的视觉导出是 [howitworks/image2slides_run/pptx/image2slides.pdf](./howitworks/image2slides_run/pptx/image2slides.pdf)；最终看图时优先看这个 PDF，因为它直接来自审查后的 PowerPoint，避免后续转换带来的偏差。轻量 plugin bundle 不包含这组较大的示例 artifacts；需要检查或复跑示例时请 clone GitHub repo。

## 输出目录

```text
decks/my-deck/
├── project.json
├── wiki/
│   ├── grep/
│   ├── generate/
│   ├── 02_content_boundary.md
│   └── 04_slide_plan.json
├── prompts/
├── completed/
├── background/
├── analysis/
├── pptx/
└── reports/
```

## 设计边界

Image2Slides 不把最终 deck 做成纯图片。完整 slide 图片是视觉参考和 QA 目标；最终交付保留可编辑 PowerPoint 文字，并把无文字 background 作为稳定底版。
