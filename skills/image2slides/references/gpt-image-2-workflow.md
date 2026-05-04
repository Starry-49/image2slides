# GPT-image-2 Workflow

Image2Slides uses the system `imagegen` skill's CLI fallback because the workflow explicitly requires GPT-image-2 model/API behavior.

## Completed Slides

Use `generate-batch` with one JSONL job per slide. Each job should include:

- `prompt`: full slide visual composition including exact visible text
- `out`: `slide_XX_completed.png`
- `size`: derived from the deck aspect ratio, e.g. `2048x1152` for 16:9
- `quality`: `high` for final slide references
- `model`: `gpt-image-2`

## Background Slides

Use `edit`, not a fresh `generate`, for each completed slide. The completed slide is the only image input. The prompt must remove only text and preserve layout, graphics, color, and geometry.

GPT-image-2 does not support transparent output. This workflow does not need transparency because backgrounds are opaque full-slide pages.

## Alignment Principle

The completed image and background image must differ only in text pixels. If non-text graphics shift, reject the background and rerun the edit with a stricter preservation prompt.
