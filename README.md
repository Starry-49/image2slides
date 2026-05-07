<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides icon" width="128" height="128">
</p>

# Image2Slides

**Languages:** English | [中文](./README.zh-CN.md) | [日本語](./README.ja.md)

Image2Slides is a Codex plugin for turning GPT-image slide compositions into editable PowerPoint decks. The invariant is simple: GPT-image-2 creates the visual target and matching text-free backgrounds; PowerPoint keeps the final text editable.

Plugin entrypoint: `/image2slides`
Workflow contract: [skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md)
Helper CLI: [skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py)

## Copy This Prompt

Give this prompt to a local Codex or coding agent. The agent should install dependencies, import the plugin, and wire the conditional hook.

```text
Install Image2Slides locally from https://github.com/Starry-49/image2slides and make it available in Codex App as the /image2slides plugin.

Do this end to end:
1. Clone or update the repository into a local workspace.
2. Inspect README.md, .codex-plugin/plugin.json, skills/image2slides/SKILL.md, package.json, pyproject.toml, and tests before changing anything.
3. Install the helper workflow as an editable Python project from pyproject.toml so numpy and Pillow are available before doctor/tests.
4. Import or refresh the Codex App plugin from the repository root, using .codex-plugin/plugin.json as the manifest. Do not point Codex at the skills/ subdirectory.
5. Register hooks/image2slides-native-hook.mjs as a conditional Codex hook for UserPromptSubmit, Bash PreToolUse, and Stop. It must stay silent for unrelated work and only activate after /image2slides prompts or image2slides CLI commands.
6. Verify /image2slides is indexed, run doctor/tests, and create a tiny deck workspace with wiki, prompts, completed, background, analysis, pptx, and reports.
7. Confirm GPT-image-2 uses Codex native image_gen by default, and register completed/background images only with native receipt manifests copied from $CODEX_HOME/generated_images/.../ig_*.png. Do not ask me for OPENAI_API_KEY unless I explicitly request SDK/API fallback.
8. Report the plugin path, hook path, runtime choices, verification evidence, and any Codex App refresh step I still need to perform.

Keep generated decks and private knowledge-base material local. Do not publish example artifacts unless I explicitly ask.
```

## Core Rules

- Codex Desktop usage should start with a visible guide, not silent execution. Run `image2slides guide` when a user needs to understand the workflow, inputs, and outputs.
- Required inputs: style/tone, aspect ratio, page count, purpose, scene, and user-provided knowledge materials.
- The required input fields are control metadata; they must not appear as visible slide text.
- If any required input is missing, the agent must ask only for the missing fields before creating files. `image2slides intake` prints the checklist.
- Facts, citations, current data, and source-locked results go under `wiki/grep/`; generated narrative and visual ideas go under `wiki/generate/`.
- `completed/` must contain GPT-image-2 full-slide references with visible text. Never fill it from PPTX/PDF renders, screenshots, local templates, or deterministic drawing.
- `background/` must contain GPT-image-2 edits of the matching completed slides, with text removed and geometry preserved.
- Native registration must include a receipt manifest proving each registered PNG was copied from Codex native `image_gen` output under `$CODEX_HOME/generated_images/.../ig_*.png`.
- Final PPTX text is editable PowerPoint text layered above the matching background image.

## Conditional Hook

[hooks/image2slides-native-hook.mjs](./hooks/image2slides-native-hook.mjs) is a workflow guard, not a global PPT rule.

- `UserPromptSubmit` adds the two-pass GPT-image-2 contract only for `/image2slides` or explicit Image2Slides requests.
- Bash `PreToolUse` inspects `image2slides ...` CLI commands and blocks PPTX/python-pptx bypasses while an Image2Slides project is active.
- `Stop` blocks premature completion when the active project still lacks native completed/background provenance.
- Native `register-completed` and `register-background` commands are blocked unless they include `--native-manifest`.
- `compose-source-locked`, `analyze`, `build-pptx`, `qa`, `audit-layout`, and `audit-boundaries` are blocked until completed/background provenance manifests exist.
- Background generation and registration require completed provenance first.

## Workflow

1. Initialize the deck project.

   ```bash
   image2slides guide
   image2slides intake
   image2slides init --project decks/my-deck --spec examples/spec.example.json
   ```

2. Fill the wiki boundary.

   Update `wiki/02_content_boundary.md`, `wiki/03_source_registry.yml`, and `wiki/04_slide_plan.json`.

3. Queue prompts and normalize source panels.

   ```bash
   image2slides queue --project decks/my-deck
   image2slides normalize-source-panels --project decks/my-deck --check --strict
   ```

   `queue` also writes `reports/native_imagegen_run.md` plus native receipt manifest templates.

4. Generate the two GPT-image-2 image batches.

   Use Codex native `image_gen` for completed slides, then edit those completed slides into text-free backgrounds.

   ```bash
   image2slides register-completed --project decks/my-deck --native-manifest decks/my-deck/reports/native_imagegen_completed_manifest.json
   image2slides register-background --project decks/my-deck --native-manifest decks/my-deck/reports/native_imagegen_background_manifest.json
   ```

5. Patch source-locked figures and build the PPTX.

   ```bash
   image2slides compose-source-locked --project decks/my-deck
   image2slides analyze --project decks/my-deck
   image2slides build-pptx --project decks/my-deck
   ```

6. Verify, then do a short human detail check.

   ```bash
   image2slides qa --project decks/my-deck --strict
   ```

   QA renders the PPTX when local tools are available, compares it with `completed/`, audits source-panel layout, checks background uniqueness, and writes boundary overlays. The final human pass only checks polish: panel padding, line breaks, text scale, overflow, consistency, and unchanged source data/results.

## Output Map

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

The repository includes [howitworks/](./howitworks/) as a minimal mental model of the full workflow.

![Howitworks reviewed PDF preview](./howitworks/image2slides_run/pptx/image2slides_preview.png)

Primary editable output: [howitworks/image2slides_run/pptx/image2slides.pptx](./howitworks/image2slides_run/pptx/image2slides.pptx)
Reviewed visual snapshot: [howitworks/image2slides_run/pptx/image2slides.pdf](./howitworks/image2slides_run/pptx/image2slides.pdf)

The lightweight plugin bundle does not include the large example artifact set. Clone the GitHub repo when you want to inspect or rerun the example.
