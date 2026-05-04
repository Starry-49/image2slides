<p align="center">
  <img src="./assets/icon.svg" alt="Image2Slides icon" width="128" height="128">
</p>

# Image2Slides

**Languages:** English | [中文](./README.zh-CN.md) | [日本語](./README.ja.md)

Image2Slides is a Codex plugin and CLI workflow for turning GPT-image slide references into editable PowerPoint decks. It uses full-slide generated images as visual targets, derives matching text-free backgrounds, places editable PowerPoint text above those backgrounds, and verifies the final deck against the reference images.

The plugin entrypoint is `/image2slides`, implemented by [skills/image2slides/SKILL.md](./skills/image2slides/SKILL.md). The deterministic helper CLI is [skills/image2slides/scripts/image2slides.py](./skills/image2slides/scripts/image2slides.py).

## Install

This repository is installable through npm directly from GitHub:

```bash
npm install -g git+https://github.com/Starry-49/image2slides.git
image2slides doctor
```

For local development:

```bash
git clone https://github.com/Starry-49/image2slides.git
cd image2slides
PYTHONPATH=skills/image2slides/scripts python3 tests/test_image2slides.py
python3 skills/image2slides/scripts/image2slides.py doctor
```

Real GPT-image-2 execution requires the system Image Gen skill CLI, the OpenAI SDK in the active Python environment, and `OPENAI_API_KEY`. Dry-runs, project initialization, image analysis, PPTX assembly, and local QA do not require an API key.

## Required User Inputs

`/image2slides` requires all of these fields before generation starts:

- slide base style and color tone
- slide aspect ratio
- slide page count
- slide purpose: speech or showcase
- presentation scene: academic, enterprise, classroom, life, or another explicit scene
- knowledge base: user-provided text, images, references, or material paths

Use [examples/spec.example.json](./examples/spec.example.json) as the starting shape.

## Workflow To Results

1. Create the project wiki and output structure:

   ```bash
   image2slides init --project decks/my-deck --spec examples/spec.example.json
   ```

   This writes `project.json`, `wiki/00_project_brief.md`, `wiki/01_wiki_map.md`, `wiki/02_content_boundary.md`, `wiki/03_source_registry.yml`, and `wiki/04_slide_plan.json`.

2. Fill the knowledge boundary:

   - Put sourced facts, citations, extracted text, and web/search findings in `wiki/grep/`.
   - Put generated narrative, metaphors, teaching examples, and draft phrasing in `wiki/generate/`.
   - Update `wiki/02_content_boundary.md` so every slide says what must be grep/search grounded and what can be generated.

3. Write image prompts:

   ```bash
   image2slides queue --project decks/my-deck
   ```

   Results:
   - `prompts/completed_prompts.jsonl`
   - `prompts/background_edit_prompts.jsonl`

4. Generate completed slide references with GPT-image-2:

   ```bash
   image2slides imagegen --project decks/my-deck --phase completed --dry-run
   image2slides imagegen --project decks/my-deck --phase completed --execute
   ```

   Results land in `completed/slide_XX_completed.png`.

5. Generate text-free backgrounds by editing each completed slide:

   ```bash
   image2slides imagegen --project decks/my-deck --phase background --dry-run
   image2slides imagegen --project decks/my-deck --phase background --execute
   ```

   Results land in `background/slide_XX_background.png`. The background prompt requires the only difference from `completed/` to be removed text; layout, graphics, color, and geometry must remain aligned.

6. Analyze text and blank regions:

   ```bash
   image2slides analyze --project decks/my-deck
   ```

   Results:
   - `analysis/slide_XX.json`
   - `analysis/manifest.json`

   The analyzer compares `completed/` and `background/`, treats their pixel difference as the text mask, identifies dominant background colors, estimates text regions, and finds low-variation blank regions for editable text filling.

7. Build the editable PowerPoint deck:

   ```bash
   image2slides build-pptx --project decks/my-deck
   ```

   Result:
   - `pptx/image2slides.pptx`

   Every slide uses the matching background image as the non-editable visual base. Text from `wiki/04_slide_plan.json` is added as editable PowerPoint text boxes above the background.

8. Render and verify:

   ```bash
   image2slides qa --project decks/my-deck
   ```

   Results:
   - `reports/rendered/`
   - `reports/qa_similarity.json`
   - `reports/qa_report.md`

   QA renders the PPTX locally when LibreOffice and `pdftoppm` are available, then compares rendered slides with `completed/` using pixel and patch similarity.

## Output Directory Map

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

## Design Boundary

Image2Slides intentionally does not make the final deck image-only. Full-slide images are visual references and QA targets. The final result keeps important text editable in PowerPoint, using text-free image backgrounds as stable base layers.
