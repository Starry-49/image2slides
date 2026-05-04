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
4. Use Codex native `image_gen` as the default GPT-image-2 path. This path does not require a user-supplied API key. The helper CLI writes prompts and file structure; the Codex agent executes native `image_gen`, then copies generated assets from `$CODEX_HOME/generated_images/...` into the deck project.
   - default model path: native GPT-image-2 through `image_gen`
   - generated bases: text-free visual background/composition images under `tmp/native_imagegen/`
   - source-locked completion: `compose-source-locked --base-dir tmp/native_imagegen` places exact source figures into `background/` and exact editable text into `completed/`
   - optional API fallback only when explicitly requested: `image2slides imagegen --phase ... --execute`
5. Save full slide images in `completed/` and matching text-free pages in `background/`.
   When a user marks data/results as non-mutating, keep those charts or figures source-locked. They may be placed as exact `source_layers` from the wiki instead of being redrawn as generated facts; the GPT-image-2 pass still owns the surrounding slide composition and matched text-free background.
   When native imagegen already created visual panels, set `draw_frame: false` on matching `source_layers` or rely on the `--base-dir` default so the compositor does not add duplicate rounded rectangles.
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
   `build-pptx` runs `lint-visible` first and fails if control-plane metadata appears in visible slide text.
8. Verify:
   ```bash
   python skills/image2slides/scripts/image2slides.py qa --project <deck-dir> --strict
   ```
   This renders the PPTX when local tools are available, compares each rendered page with `completed/`, and writes pixel/patch similarity reports.
   QA also writes `reports/source_layer_audit.md`. This fixed review item checks that source figures stay inside declared `panel_bbox`, do not overlap editable text, and do not add duplicate rounded-rectangle frames over native imagegen panels.

## Prompt Rules

The required input fields are control-plane metadata. They shape the deck, but they must not appear as visible slide copy. Do not render phrases such as the chosen tone, aspect ratio, page count, purpose, scene label, knowledge-base path, plugin name, workflow name, validation label, or file name unless the same phrase is also part of the user-approved presentation content.

For completed slides, include exact slide text and the visual direction. Chinese or dense factual text is allowed only because this output is a reference composition, not the final editable source.

If the user says source data/results must not be changed, do not ask GPT-image-2 to invent or redraw those values. Put the original charts or figures into `source_layers`, keep visible text in `text_items`, and preserve the source registry entry that explains which layers are grep-required.

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
native Codex image_gen tool
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

The API CLI fallback is optional and only for explicit API/SDK runs:

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
- `reports/source_layer_audit.json` exists with zero issues
- report any slides below the configured similarity threshold
