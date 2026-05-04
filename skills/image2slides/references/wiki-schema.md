# Wiki Schema

`image2slides.py init` creates this layout inside each deck project:

- `project.json`: canonical required input spec plus normalized slide dimensions
- `wiki/00_project_brief.md`: user intent and required fields
- `wiki/01_wiki_map.md`: project information architecture
- `wiki/02_content_boundary.md`: grep/generate boundary table
- `wiki/03_source_registry.yml`: source records for grep-required claims
- `wiki/04_slide_plan.json`: slide titles, text items, notes, and optional layout hints
- `wiki/grep/`: sourced material and web/search findings
- `wiki/generate/`: generated narrative, examples, metaphors, and prompt drafts
- `prompts/completed_prompts.jsonl`: GPT-image-2 generation queue
- `prompts/background_edit_prompts.jsonl`: one edit prompt per completed slide
- `completed/`: full reference slide images
- `background/`: text-free background-only images
- `analysis/`: region detection outputs
- `pptx/`: generated PowerPoint files
- `reports/`: render and similarity QA

`wiki/04_slide_plan.json` uses:

```json
{
  "slides": [
    {
      "slide": 1,
      "title": "Slide title",
      "layout": "cover",
      "visual_intent": "What the image composition should convey",
      "source_boundary": "grep_required | generation_allowed | mixed",
      "text_items": [
        {
          "role": "title",
          "text": "Editable title text",
          "font_size": 32,
          "bold": true,
          "color": "#ffffff",
          "bbox": [0.08, 0.16, 0.45, 0.18]
        }
      ],
      "speaker_notes": ""
    }
  ]
}
```

`bbox` is optional and normalized as `[x, y, w, h]` from 0 to 1. If absent, `analyze` results are used.
