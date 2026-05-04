<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides 图标" width="128" height="128">
</p>

# Image2Slides

**语言:** [English](./README.md) | 中文 | [日本語](./README.ja.md)

Image2Slides 是一个 Codex plugin 和 CLI workflow，用于把 GPT-image slide 视觉结果转成可编辑 PowerPoint。它默认用 Codex native `image_gen` 生成 GPT-image-2 视觉底版，保持 source-locked 数据图表不被改写，再把文字作为可编辑 PPT 文本浮在匹配 background 之上，最后用渲染结果和 completed 参考图做像素/patch 级验收。

Plugin 入口是 `/image2slides`，核心说明在 [skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md)，确定性脚本在 [skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py)。

## 安装

可以通过 npm 直接从 GitHub 安装：

```bash
npm install -g git+https://github.com/Starry-49/image2slides.git
image2slides doctor
```

本地开发：

```bash
git clone https://github.com/Starry-49/image2slides.git
cd image2slides
PYTHONPATH=skills/image2slides/scripts python3 tests/test_image2slides.py
python3 skills/image2slides/scripts/image2slides.py doctor
```

默认 GPT-image-2 调用走 Codex native `image_gen`，不需要 `OPENAI_API_KEY`。OpenAI SDK/API-key CLI 只是显式需要 API/SDK 执行时的 fallback。

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

4. 用 Codex native `image_gen` 生成 GPT-image-2 视觉底版。

   默认 plugin workflow 使用 Codex native image generation，不需要 `OPENAI_API_KEY`。把每张无文字视觉底版复制到：

   - `tmp/native_imagegen/slide_XX_base.png`

   对于不能改写的数据/结果，把原始图表放在 `wiki/sources/`，再组合到底版上：

   ```bash
   image2slides compose-source-locked --project decks/my-deck --base-dir tmp/native_imagegen
   image2slides audit-layout --project decks/my-deck --strict
   ```

   输出到 `completed/slide_XX_completed.png` 和 `background/slide_XX_background.png`。
   layout audit 会固定检查 source 图是否在声明的 panel 内、是否遮挡可编辑文字、是否在 native imagegen panel 上重复添加圆角框。
   source fitting 会先裁掉 blank 像素，再把声明的 panel 按 `fit_margin_px` 内缩，在四条平行边约束下最大化缩放并最小化剩余 slack，最后让图片中心和内缩 panel 中心对齐。

5. 可选 API CLI fallback：

   ```bash
   image2slides imagegen --project decks/my-deck --phase completed --dry-run
   image2slides imagegen --project decks/my-deck --phase completed --execute
   ```

   也可以基于 completed 图编辑生成无文字 background：

   ```bash
   image2slides imagegen --project decks/my-deck --phase background --dry-run
   image2slides imagegen --project decks/my-deck --phase background --execute
   ```

   输出到 `background/slide_XX_background.png`。background prompt 会要求只移除文字，保留布局、图形、颜色和几何位置。

6. 分析文字区域和 blank 区域：

   ```bash
   image2slides analyze --project decks/my-deck
   ```

   输出：
   - `analysis/slide_XX.json`
   - `analysis/manifest.json`

   分析器对比 `completed/` 和 `background/`，把像素差视为文字 mask，识别主背景色、文字区域和低变化 blank 区域。

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

   QA 会在本地有 LibreOffice 和 `pdftoppm` 时渲染 PPTX，再与 `completed/` 做像素和 patch 相似度比较，并再次执行固定的 source-layer layout audit。

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
