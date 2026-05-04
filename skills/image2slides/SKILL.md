---
name: image2slides
description: "Create an editable PowerPoint deck from required slide intent, provided knowledge materials, GPT-image-2 full-slide images, matching text-free backgrounds, pixel-based layout analysis, and rendered-slide QA. Invoke for /image2slides or when the user asks to turn images or knowledge into slides with imagegen and PPTX output."
---

# Image2Slides

Use this workflow for `/image2slides`. The output is not a flat image-only deck: final text must be editable PowerPoint text layered above non-selectable background images.

## Required Inputs

Before running generation, confirm these fields are present. They are not optional:

- slide base style and color tone
- slide aspect ratio
- slide page count
- slide purpose: `speech` or `showcase`
- presentation scene: academic, enterprise, classroom, life, or another explicit scene
- knowledge base: user-provided text/image/material paths or pasted materials

If any required field is missing, ask only for the missing fields and stop before creating project artifacts.

## Core Contract

1. Create a project directory and write the required intent to disk with:
   ```bash
   python skills/image2slides/scripts/image2slides.py init --project <deck-dir> --spec <spec.json>
   ```
2. Maintain the wiki structure created by `init`. Put factual source material under `wiki/grep/` and generated/story material under `wiki/generate/`.
3. Create `wiki/02_content_boundary.md` before writing prompts. Every slide needs a boundary:
   - `grep_required`: facts, claims, dates, names, quotes, current data, citations, or anything that must come from source/web/search.
   - `generation_allowed`: visual metaphors, transitions, examples, phrasing, non-factual teaching scaffolds, and abstract imagery.
4. Use GPT-image-2 through the system imagegen CLI. This is an explicit model/API path, so it may use the imagegen skill's fallback CLI:
   - default model: `gpt-image-2`
   - completed slides: `generate-batch`
   - text-free backgrounds: `edit` from each completed slide, removing only text while preserving every non-text graphic element
5. Save full slide images in `completed/` and matching text-free pages in `background/`.
6. Run analysis:
   ```bash
   python skills/image2slides/scripts/image2slides.py analyze --project <deck-dir>
   ```
   This compares `completed/` and `background/`, treats their pixel difference as the text mask, identifies dominant background color, surrounding pixel variation, text regions, and blank fill regions.
7. Build the PPTX:
   ```bash
   python skills/image2slides/scripts/image2slides.py build-pptx --project <deck-dir>
   ```
   Each background image is written as the slide background layer, then editable text boxes are placed above it.
8. Verify:
   ```bash
   python skills/image2slides/scripts/image2slides.py qa --project <deck-dir>
   ```
   This renders the PPTX when local tools are available, compares each rendered page with `completed/`, and writes pixel/patch similarity reports.

## Prompt Rules

For completed slides, include exact slide text and the visual direction. Chinese or dense factual text is allowed only because this output is a reference composition, not the final editable source.

For background slides, use the completed slide as the edit input and require:

```text
Remove all readable text, letters, numbers, labels, captions, and watermarks only.
Preserve every non-text visual element exactly: layout, illustrations, charts without labels, icons, shapes, lighting, texture, color, perspective, and spacing.
The output must be a text-free background for editable PowerPoint overlays.
The only difference from the input should be the absence of text.
```

Do not create a background from a new generation prompt if a completed image exists; use edit so the image geometry stays aligned.

## Imagegen CLI

Default path:

```bash
export IMAGE_GEN="${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/scripts/image_gen.py"
```

Create queues:

```bash
python skills/image2slides/scripts/image2slides.py queue --project <deck-dir>
```

Dry-run API payloads:

```bash
python skills/image2slides/scripts/image2slides.py imagegen --project <deck-dir> --phase completed --dry-run
python skills/image2slides/scripts/image2slides.py imagegen --project <deck-dir> --phase background --dry-run
```

Execute only when the user has supplied/approved API credentials:

```bash
python skills/image2slides/scripts/image2slides.py imagegen --project <deck-dir> --phase completed --execute
python skills/image2slides/scripts/image2slides.py imagegen --project <deck-dir> --phase background --execute
```

## Keynote Lessons Carried Forward

The `szhct-youqin-keynote` project showed the right deck boundary:

- keep important Chinese/factual text editable in PowerPoint
- use imagegen for visual direction, backgrounds, components, and disposable composition sketches
- keep source policy strict: no confirmed fact enters the final deck without a source registry entry or user confirmation
- use native PowerPoint text overlays and run render/QC before completion

Image2Slides intentionally differs by allowing full-slide completed references, then converting those references into editable PPTX via matched text-free backgrounds and pixel-diff text placement.

## Completion Checklist

- `project.json` exists and contains all required input fields
- `wiki/02_content_boundary.md` separates grep-required and generation-allowed content
- `completed/slide_XX_completed.png` exists for every slide
- `background/slide_XX_background.png` exists for every slide
- `analysis/manifest.json` contains dominant colors, text regions, and blank regions
- `pptx/image2slides.pptx` exists
- `reports/qa_similarity.json` and `reports/qa_report.md` exist
- report any slides below the configured similarity threshold
