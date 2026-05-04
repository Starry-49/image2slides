# Data Lock Report

The user required data results to remain unchanged. This run uses source-locked figure layers for source result images and editable text overlays for the narrative text.

- Source figures are copied from the knowledge base extraction into `wiki/sources/media/`.
- Numeric claims in `text_items` are copied from the extracted knowledge text and not regenerated.
- Control-plane phrases are excluded from visible slide text by `lint-visible`.
