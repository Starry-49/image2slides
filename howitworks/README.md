# Howitworks Minimal Example

This folder is a committed minimal mental model for the Image2Slides workflow. It shows one classroom-report deck built from a user knowledge base, source-locked figures, GPT-image-2 visual bases, editable PowerPoint text, strict QA, and a final human detail review.

## Input

- `spec.json`: required `/image2slides` inputs for a white classroom report deck.
- `howitworks.docx`: original knowledge-base document.
- `extracted_text.md`: extracted factual text used for the grep/source side of the workflow.
- `extracted_media/`: source figures extracted from the knowledge base.

## Output

- `image2slides_run/wiki/`: structured wiki, source boundary, source registry, and slide plan.
- `image2slides_run/tmp/native_imagegen/`: GPT-image-2 text-free visual bases created through Codex native `image_gen`.
- `image2slides_run/completed/`: full reference slide images after source-locked composition.
- `image2slides_run/background/`: text-free slide backgrounds.
- `image2slides_run/pptx/image2slides.pptx`: final editable PowerPoint deck.
- `image2slides_run/pptx/image2slides.pdf`: final human-reviewed PDF export from PowerPoint. Prefer this file when inspecting the accepted visual result because it reflects the reviewed deck directly and avoids a later LibreOffice/render conversion drift.
- `image2slides_run/pptx/image2slides_preview.png`: README preview contact sheet rendered from the reviewed PDF.
- `image2slides_run/reports/`: rendered slides, similarity QA, source-layer audit, and data-lock report.

![Reviewed PDF preview](./image2slides_run/pptx/image2slides_preview.png)

## Rebuild Shape

Run from the repository root when refreshing the committed example output:

```bash
image2slides queue --project howitworks/image2slides_run
image2slides compose-source-locked --project howitworks/image2slides_run --base-dir tmp/native_imagegen
image2slides audit-layout --project howitworks/image2slides_run --strict
image2slides analyze --project howitworks/image2slides_run
image2slides build-pptx --project howitworks/image2slides_run
image2slides render --project howitworks/image2slides_run
image2slides qa --project howitworks/image2slides_run --strict
```

The committed run keeps the curated wiki and native GPT-image-2 bases so the important mental model is inspectable without regenerating images.

## Manual Detail Review

After strict QA passes, open `image2slides_run/pptx/image2slides.pptx` and perform a brief human pass:

- check that source figures sit visually inside their generated panels;
- check title/body line breaks, font scale, and obvious text overflow;
- check page-to-page visual consistency;
- check that source data/results were not altered;
- make only small presentation-polish edits, then keep the final deck in `pptx/`;
- export the reviewed deck to `image2slides_run/pptx/image2slides.pdf` as the canonical visual snapshot.
