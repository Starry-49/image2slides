# Image2Slides

Image2Slides is a Codex plugin with a `/image2slides` workflow. It creates a structured slide project, separates sourced facts from generated narrative, calls the system `imagegen` GPT-image-2 CLI for visual slide images, derives text-free backgrounds, builds an editable PPTX, and verifies rendered slides against completed reference images.

The main entrypoint is the skill at `skills/image2slides/SKILL.md`; deterministic helpers live in `skills/image2slides/scripts/image2slides.py`.

Quick local check:

```bash
PYTHONPATH=skills/image2slides/scripts python3 tests/test_image2slides.py
python3 skills/image2slides/scripts/image2slides.py doctor
```

The analysis and QA helpers require Pillow and numpy. Real GPT-image-2 execution also requires the OpenAI SDK plus `OPENAI_API_KEY`; dry-runs do not.
