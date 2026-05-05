# GPT-image-2 Workflow

Image2Slides uses Codex native `image_gen` by default for GPT-image-2. The API CLI path is only an explicit fallback.

## Completed Slides

Use `generate-batch` with one JSONL job per slide. Each job should include:

- `prompt`: full slide visual composition including exact visible text
- `out`: `slide_XX_completed.png`
- `size`: derived from the deck aspect ratio, e.g. `2048x1152` for 16:9
- `quality`: `high` for final slide references
- `model`: `gpt-image-2`

For source-locked figures, the prompt must reserve blank panels whose inner proportions match the source image aspect ratio. The figure itself is pasted later from `wiki/sources/`; GPT-image-2 should design the surrounding composition and panel shell, not redraw or mutate data/results. The planned `bbox` is only a search hint for the later detector. The actual panel must be found from the source-free GPT-image background; if the generated panel ratio or location differs, strict QA must flag it instead of silently pasting into the hint box.

During `queue`, every planned source panel is also written into `wiki/04_slide_plan.json` as a `layout_boundaries` entry with `kind: non_editable_image_panel`. That boundary is part of the generation contract: editable PowerPoint text must not be planned inside it. Labels that already live inside a chart, diagram, schematic, or icon are asset-internal text and should stay embedded in the image asset unless the user explicitly asks to extract them.

## Background Slides

Use `edit`, not a fresh `generate`, for each completed slide. The completed slide is the only image input. The prompt must remove only text and preserve layout, graphics, color, and geometry.

GPT-image-2 does not support transparent output. This workflow does not need transparency because backgrounds are opaque full-slide pages.

## Alignment Principle

The completed image and background image must differ only in text pixels. If non-text graphics shift, reject the background and rerun the edit with a stricter preservation prompt.

When `tmp/native_imagegen/slide_XX_base.png` is present, `compose-source-locked` detects the actual generated panel edges before placing source figures. It must not clear or repaint the declared hint region, because a wrong hint can become a false panel if it is erased. QA then checks the detected panel, the source-aspect panel, the inset fit box, the final paste box, the rendered source crop, and local editable-text alignment against the completed reference.

Full-slide pixel similarity is only a smoke test. It can miss bad text placement when large blank regions dominate the page. Use `audit-boundaries` or `qa` output to inspect main-color blank zones, forbidden source/illustration zones, candidate text-fill rectangles, and overlay PNGs. Codex native LLM review should judge the overlays: accept when editable text lives in blank fill zones and source panels remain visual-only regions; regenerate or adjust when text overlaps source panels, panel borders, or nearby illustrations.
