# How2Reinforce Workflow Example

This example rebuilds `raw.pdf` into a 15-slide undergraduate thesis defense deck using the Image2Slides workflow.

## Required Input

- Style: white academic defense style with restrained blue/green biomedical accents
- Aspect ratio: 16:9
- Page count: 15
- Purpose: speech
- Scene: academic undergraduate thesis defense
- Knowledge base: `raw.pdf`

## Workflow Evidence

1. The required intent was written to `image2slides_run/project.json`.
2. The wiki boundary was written under `image2slides_run/wiki/`, with factual content and source figures treated as grep-required material.
3. `completed/` contains the approved native GPT-image-2 full-slide reference batch, then exact source figures were source-locked into the approved imagegen layouts.
4. `background/` contains native GPT-image-2 text-free edits from the matching completed slides, then the same exact source figures were patched into the matched positions.
5. Source figures were extracted from `raw.pdf`, trimmed, fitted by source aspect ratio, centered in the detected/native panel inset, and checked with the source-layer audit.
6. `analysis/` was generated from pixel differences between `completed/` and `background/`.
7. `pptx/image2slides.pptx` was built with each background as the slide base and editable text floating above it.
8. Strict QA rendered the PPTX, compared rendered slides to `completed/`, and checked source-layer and background uniqueness audits.
9. A final visual snapshot is available at `pptx/image2slides.pdf`.

## Outputs

- Editable PPTX: `image2slides_run/pptx/image2slides.pptx`
- Visual PDF snapshot: `image2slides_run/pptx/image2slides.pdf`
- Rendered contact sheet: `image2slides_run/reports/rendered_contact_sheet.png`
- QA report: `image2slides_run/reports/qa_report.md`
- Source-layer audit: `image2slides_run/reports/source_layer_audit.md`
- Background audit: `image2slides_run/reports/background_audit.md`
- Figure registry: `image2slides_run/wiki/sources/figure_registry.json`

## Verification

- Strict QA: passed
- Slides checked: 15
- Failing or missing slides: 0
- Source-layer layout issues: 0
- Background issues: 0
- Minimum pixel similarity: 0.90178
- Maximum bad-pixel ratio over 32: 0.24461
- Maximum patch p90 MAE: 55.946
- Maximum bad detail patch ratio: 0.35417

## Provenance

- Completed provenance: `registered_native_image_gen_with_source_locked_patch`
- Background provenance: `registered_native_image_gen_edit_with_source_locked_patch`
- Source figures: exact crops from `raw.pdf`

`completed/` is not a PPTX/PDF/render screenshot batch. Rendered slides only live under `reports/` as QA evidence, and the accepted PDF snapshot lives under `pptx/`.
