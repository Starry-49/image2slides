<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides icon" width="128" height="128">
</p>

# Image2Slides

**语言:** [English](./README.md) | 中文 | [日本語](./README.ja.md)

Image2Slides 是一个 Codex plugin，用于把 GPT-image slide 构图转换成可编辑 PowerPoint。核心不变量很简单：GPT-image-2 生成视觉目标和匹配的无文字底版；最终 PowerPoint 保留可编辑文字。

Plugin 入口：`/image2slides`
Workflow 契约：[skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md)
Helper CLI：[skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py)

## 复制这段 Prompt

把下面这段交给本地 Codex 或 coding agent。让 agent 负责安装依赖、导入 plugin、接好条件式 hook。

```text
请从 https://github.com/Starry-49/image2slides 在本地安装 Image2Slides，并让它在 Codex App 中作为 /image2slides plugin 可用。

请端到端完成：
1. 把仓库 clone 或更新到本地 workspace。
2. 先阅读 README.md、.codex-plugin/plugin.json、skills/image2slides/SKILL.md、package.json、pyproject.toml 和 tests，再做任何改动。
3. 按 pyproject.toml 把 helper workflow 作为 editable Python project 安装，确保 numpy 和 Pillow 在 doctor/tests 前可用。
4. 从仓库根目录导入或刷新 Codex App plugin，manifest 使用 .codex-plugin/plugin.json。不要把 Codex 指到 skills/ 子目录。
5. 把 hooks/image2slides-native-hook.mjs 作为条件式 Codex hook 注册到 UserPromptSubmit、Bash PreToolUse 和 Stop。它必须对无关任务保持静默，只在 /image2slides prompt 或 image2slides CLI 命令后启用。
6. 确认 /image2slides 已被索引，运行 doctor/tests，并创建一个很小的 deck workspace，确认 wiki、prompts、completed、background、analysis、pptx、reports 能初始化。
7. 确认 GPT-image-2 默认走 Codex native image_gen，并且 completed/background 注册只能使用从 $CODEX_HOME/generated_images/.../ig_*.png 复制来的 native receipt manifest。不要向我索要 OPENAI_API_KEY，除非我明确要求 SDK/API fallback。
8. 汇报 plugin 路径、hook 路径、runtime 选择、验证证据，以及我是否还需要手动刷新 Codex App。

所有生成的 deck 和私人知识库资料都留在本地。不要发布示例产物，除非我明确要求。
```

## 核心规则

- Codex Desktop 场景要先给用户可见的 workflow guide，而不是静默执行。用户需要理解工作流、input、output 时运行 `image2slides guide`。
- 必填输入：风格/色调、画面比例、页数、用途、场景、用户知识库材料。
- 必填输入是控制元数据，不得出现在可见 slide 文本里。
- 如果缺任一必填输入，agent 必须先只追问缺失字段，再创建文件。`image2slides intake` 会打印这份清单。
- 事实、引用、当前数据、source-locked 结果放入 `wiki/grep/`；生成叙事和视觉启发放入 `wiki/generate/`。
- `completed/` 必须是 GPT-image-2 生成的含文字完整页面参考图。禁止用 PPTX/PDF render、截图、本地模板或确定性绘图填充。
- `background/` 必须是对应 completed 的 GPT-image-2 edit，只移除文字并保持几何一致。
- native 注册必须提供 receipt manifest，证明每个注册 PNG 都来自 Codex native `image_gen` 输出目录 `$CODEX_HOME/generated_images/.../ig_*.png`。
- 最终 PPTX 的文字必须是可编辑 PowerPoint 文本，浮在匹配 background 上方。

## 条件式 Hook

[hooks/image2slides-native-hook.mjs](./hooks/image2slides-native-hook.mjs) 是 workflow guard，不是全局 PPT 规则。

- `UserPromptSubmit` 只在 `/image2slides` 或明确 Image2Slides 请求时注入两批 GPT-image-2 契约。
- Bash `PreToolUse` 检查 `image2slides ...` CLI 命令，并在 Image2Slides 项目激活后阻断 PPTX/python-pptx 绕行。
- `Stop` 会在 active project 仍缺 native completed/background provenance 时阻断过早结束。
- native `register-completed` 和 `register-background` 没有 `--native-manifest` 会被阻断。
- `compose-source-locked`、`analyze`、`build-pptx`、`qa`、`audit-layout`、`audit-boundaries` 会在缺 completed/background provenance 时被阻断。
- background 生成和注册必须先有 completed provenance。

## Workflow

1. 初始化 deck project。

   ```bash
   image2slides guide
   image2slides intake
   image2slides init --project decks/my-deck --spec examples/spec.example.json
   ```

2. 填写 wiki 边界。

   更新 `wiki/02_content_boundary.md`、`wiki/03_source_registry.yml` 和 `wiki/04_slide_plan.json`。

3. 创建 prompts，并规范 source panel。

   ```bash
   image2slides queue --project decks/my-deck
   image2slides normalize-source-panels --project decks/my-deck --check --strict
   ```

   `queue` 也会写出 `reports/native_imagegen_run.md` 和 native receipt manifest templates。

4. 生成两批 GPT-image-2 图片。

   用 Codex native `image_gen` 生成 completed，再把 completed edit 成无文字 background。

   ```bash
   image2slides register-completed --project decks/my-deck --native-manifest decks/my-deck/reports/native_imagegen_completed_manifest.json
   image2slides register-background --project decks/my-deck --native-manifest decks/my-deck/reports/native_imagegen_background_manifest.json
   ```

5. Patch source-locked figures，并构建 PPTX。

   ```bash
   image2slides compose-source-locked --project decks/my-deck
   image2slides analyze --project decks/my-deck
   image2slides build-pptx --project decks/my-deck
   ```

6. 验收，然后做简短人工细节检查。

   ```bash
   image2slides qa --project decks/my-deck --strict
   ```

   QA 会在本地工具可用时重新渲染 PPTX，与 `completed/` 比对，审查 source-panel layout、background 唯一性，并写出 boundary overlays。最后人工只看细节：panel 留白、换行、字号、溢出、页面一致性，以及 source data/results 是否未被改写。

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

## Example

仓库内包含 [howitworks/](./howitworks/) 作为完整 workflow 的最小心智模型。

![Howitworks 人工审查 PDF 预览](./howitworks/image2slides_run/pptx/image2slides_preview.png)

主要可编辑结果：[howitworks/image2slides_run/pptx/image2slides.pptx](./howitworks/image2slides_run/pptx/image2slides.pptx)
人工审查视觉快照：[howitworks/image2slides_run/pptx/image2slides.pdf](./howitworks/image2slides_run/pptx/image2slides.pdf)

轻量 plugin bundle 不包含这组较大的示例 artifacts。需要检查或复跑示例时请 clone GitHub repo。
