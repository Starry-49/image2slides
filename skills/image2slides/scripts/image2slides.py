#!/usr/bin/env python3
"""Deterministic helpers for the Image2Slides Codex plugin.

The agent owns the creative and factual judgment. This CLI owns repeatable file
layout, GPT-image-2 queue construction, image pair analysis, PPTX assembly, and
similarity reports.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import zipfile
from datetime import datetime, timezone
from html import escape as xml_escape
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - exercised by users without Pillow.
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore

try:
    import numpy as np
except Exception:  # pragma: no cover - exercised by users without numpy.
    np = None  # type: ignore


REQUIRED_FIELDS = (
    "style_tone",
    "aspect_ratio",
    "slide_count",
    "purpose",
    "scene",
    "knowledge_base",
)

PROJECT_DIRS = (
    "wiki",
    "wiki/grep",
    "wiki/generate",
    "wiki/knowledge",
    "wiki/sources",
    "prompts",
    "completed",
    "background",
    "analysis",
    "pptx",
    "reports",
    "tmp/imagegen",
)

EMU_PER_INCH = 914400
DEFAULT_PANEL_MARGIN_PX = 32
DEFAULT_GENERATED_PANEL_MIN_MARGIN_PX = 32
DEFAULT_PANEL_ASPECT_TOLERANCE = 0.04
DEFAULT_FIGURE_FIRST_MIN_SOURCE_AREA = 0.26
DEFAULT_FIGURE_FIRST_MIN_SOURCE_TEXT_RATIO = 1.15
DEFAULT_FIGURE_FIRST_MAX_TEXT_CHARS = 140
DEFAULT_TEXT_MIN_PIXEL_SIMILARITY = 0.78
DEFAULT_TEXT_MAX_BAD_PIXEL_RATIO_32 = 0.30
DEFAULT_TEXT_MIN_INK_IOU = 0.45
DEFAULT_TEXT_MAX_CENTER_DELTA = 0.16
DEFAULT_SOURCE_MIN_RENDERED_SIMILARITY = 0.94
DEFAULT_SOURCE_MAX_RENDERED_BAD_PIXEL_RATIO_32 = 0.08
DEFAULT_SOURCE_BLANK_MIN_RENDERED_SIMILARITY = 0.97
DEFAULT_SOURCE_BLANK_MAX_RENDERED_BAD_PIXEL_RATIO_32 = 0.04
DEFAULT_BOUNDARY_BLANK_DISTANCE = 36.0
DEFAULT_BOUNDARY_TEXT_DIFF_THRESHOLD = 30.0
DEFAULT_BOUNDARY_FORBIDDEN_DILATE_PX = 22
DEFAULT_BOUNDARY_SAFE_MARGIN_RATIO = 0.035
DEFAULT_BOUNDARY_MAX_TEXT_OUTSIDE_FILL = 0.35
DEFAULT_BOUNDARY_MAX_TEXT_FORBIDDEN_OVERLAP = 0.12
DEFAULT_BOUNDARY_MIN_CLEARANCE_P10_PX = 12
BOUNDARY_GRID_COLS = 32
BOUNDARY_GRID_ROWS = 18
COMPLETED_PROVENANCE = ".image2slides_completed_provenance.json"
BACKGROUND_PROVENANCE = ".image2slides_background_provenance.json"
COMPLETED_ALLOWED_METHODS = {
    "native_image_gen",
    "api_imagegen_cli",
    "registered_native_image_gen",
    "native_image_gen_with_source_locked_patch",
    "api_imagegen_cli_with_source_locked_patch",
    "registered_native_image_gen_with_source_locked_patch",
    "test_image_gen_fixture",
    "test_image_gen_fixture_with_source_locked_patch",
}
BACKGROUND_ALLOWED_METHODS = {
    "native_image_gen_edit",
    "api_imagegen_cli_edit",
    "registered_native_image_gen_edit",
    "native_image_gen_edit_with_source_locked_patch",
    "api_imagegen_cli_edit_with_source_locked_patch",
    "registered_native_image_gen_edit_with_source_locked_patch",
    "test_image_gen_edit_fixture",
    "test_image_gen_edit_fixture_with_source_locked_patch",
}
COMPLETED_FORBIDDEN_SOURCE_MARKERS = (
    "pptx",
    "powerpoint export",
    "render",
    "rendered",
    "screenshot",
    "reports/rendered",
    "libreoffice",
    "soffice",
)

XMLNS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def require_image_libs() -> None:
    if Image is None or ImageDraw is None or ImageFont is None:
        die("Pillow is required for image analysis. Install pillow or use the Codex bundled Python.")
    if np is None:
        die("numpy is required for image analysis. Install numpy or use the Codex bundled Python.")


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        die(f"JSON file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"Invalid JSON in {path}: {exc}")
    return {}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_relative(project: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve()))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_purpose(value: Any) -> str:
    raw = str(value).strip().lower()
    mapping = {
        "speech": "speech",
        "talk": "speech",
        "presentation": "speech",
        "showcase": "showcase",
        "display": "showcase",
        "demo": "showcase",
        "演讲": "speech",
        "展示": "showcase",
    }
    return mapping.get(raw, raw)


def parse_aspect_ratio(raw: Any) -> Tuple[int, int]:
    value = str(raw).strip().lower().replace(" ", "")
    aliases = {
        "wide": "16:9",
        "widescreen": "16:9",
        "landscape": "16:9",
        "standard": "4:3",
        "portrait": "9:16",
        "square": "1:1",
    }
    value = aliases.get(value, value)
    if ":" in value:
        left, right = value.split(":", 1)
    elif "x" in value:
        left, right = value.split("x", 1)
    else:
        die(f"Unsupported aspect ratio: {raw}")
    try:
        w = int(left)
        h = int(right)
    except ValueError:
        die(f"Unsupported aspect ratio: {raw}")
    if w <= 0 or h <= 0:
        die(f"Unsupported aspect ratio: {raw}")
    g = math.gcd(w, h)
    return w // g, h // g


def slide_inches_for_ratio(ratio: Tuple[int, int]) -> Tuple[float, float]:
    w, h = ratio
    if w >= h:
        return round(7.5 * w / h, 3), 7.5
    return 7.5, round(7.5 * h / w, 3)


def image_size_for_ratio(ratio: Tuple[int, int]) -> str:
    w, h = ratio
    if (w, h) == (16, 9):
        return "2048x1152"
    if (w, h) == (9, 16):
        return "1152x2048"
    if (w, h) == (4, 3):
        return "2048x1536"
    if (w, h) == (3, 4):
        return "1536x2048"
    if w == h:
        return "1536x1536"

    long_edge = 2048
    if w >= h:
        width = long_edge
        height = int(round(long_edge * h / w / 16) * 16)
    else:
        height = long_edge
        width = int(round(long_edge * w / h / 16) * 16)
    width = max(816, min(3840, width))
    height = max(816, min(3840, height))
    if width * height < 655360:
        scale = math.sqrt(655360 / (width * height))
        width = int(math.ceil(width * scale / 16) * 16)
        height = int(math.ceil(height * scale / 16) * 16)
    if max(width, height) / min(width, height) > 3:
        die(f"Aspect ratio {w}:{h} exceeds GPT-image-2 3:1 ratio limit")
    return f"{width}x{height}"


def normalize_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    aliases = {
        "style": "style_tone",
        "styleTone": "style_tone",
        "base_style": "style_tone",
        "ratio": "aspect_ratio",
        "pages": "slide_count",
        "page_count": "slide_count",
        "slides": "slide_count",
        "use": "purpose",
        "presentation_scene": "scene",
        "materials": "knowledge_base",
        "knowledge": "knowledge_base",
    }
    spec = dict(raw)
    for old, new in aliases.items():
        if old in spec and new not in spec:
            spec[new] = spec[old]

    missing = [field for field in REQUIRED_FIELDS if field not in spec or spec[field] in ("", None, [])]
    if missing:
        die("Missing required field(s): " + ", ".join(missing))

    try:
        slide_count = int(spec["slide_count"])
    except Exception:
        die("slide_count must be an integer")
    if slide_count < 1:
        die("slide_count must be >= 1")

    ratio = parse_aspect_ratio(spec["aspect_ratio"])
    slide_w, slide_h = slide_inches_for_ratio(ratio)
    spec["slide_count"] = slide_count
    spec["purpose"] = normalize_purpose(spec["purpose"])
    spec["aspect_ratio_normalized"] = f"{ratio[0]}:{ratio[1]}"
    spec["slide_size_inches"] = {"width": slide_w, "height": slide_h}
    spec["image_size"] = image_size_for_ratio(ratio)
    spec["created_at"] = spec.get("created_at") or now_iso()
    return spec


def mkdirs(project: Path) -> None:
    for rel in PROJECT_DIRS:
        (project / rel).mkdir(parents=True, exist_ok=True)


def project_file(project: Path) -> Path:
    return project / "project.json"


def load_project(project: Path) -> Dict[str, Any]:
    return read_json(project_file(project))


def completed_provenance_path(project: Path) -> Path:
    return project / "completed" / COMPLETED_PROVENANCE


def background_provenance_path(project: Path) -> Path:
    return project / "background" / BACKGROUND_PROVENANCE


def completed_image_path(project: Path, slide_no: int) -> Path:
    return project / "completed" / f"slide_{slide_no:02d}_completed.png"


def background_image_path(project: Path, slide_no: int) -> Path:
    return project / "background" / f"slide_{slide_no:02d}_background.png"


def expected_slide_numbers(spec: Dict[str, Any]) -> List[int]:
    return list(range(1, int(spec["slide_count"]) + 1))


def image_record(project: Path, path: Path, slide_no: int) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "slide": slide_no,
        "path": project_relative(project, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if Image is not None:
        try:
            with Image.open(path) as image:
                record["width"] = image.width
                record["height"] = image.height
        except Exception:
            pass
    return record


def provenance_source_is_forbidden(value: Any) -> bool:
    raw = str(value or "").replace("\\", "/").lower()
    return any(marker in raw for marker in COMPLETED_FORBIDDEN_SOURCE_MARKERS)


def write_completed_provenance(
    project: Path,
    spec: Dict[str, Any],
    *,
    method: str,
    source: str,
    note: str = "",
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if method not in COMPLETED_ALLOWED_METHODS:
        die(f"Unsupported completed provenance method: {method}")
    if provenance_source_is_forbidden(source) or provenance_source_is_forbidden(method):
        die("completed provenance cannot point to PPTX/rendered/exported screenshot sources")

    entries = []
    for slide_no in expected_slide_numbers(spec):
        path = completed_image_path(project, slide_no)
        if not path.exists():
            die(f"Cannot register completed provenance; missing image_gen completed file: {path}")
        entries.append(image_record(project, path, slide_no))

    manifest: Dict[str, Any] = {
        "version": 1,
        "created_at": now_iso(),
        "required_origin": "GPT-image-2 completed reference image, not PPTX/PDF/render screenshot",
        "generator": "gpt-image-2",
        "method": method,
        "source": source,
        "note": note,
        "slides": entries,
    }
    if previous:
        manifest["previous"] = {
            "created_at": previous.get("created_at"),
            "method": previous.get("method"),
            "source": previous.get("source"),
            "slides": previous.get("slides", []),
        }
    write_json(completed_provenance_path(project), manifest)
    return manifest


def write_background_provenance(
    project: Path,
    spec: Dict[str, Any],
    *,
    method: str,
    source: str,
    note: str = "",
    previous: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if method not in BACKGROUND_ALLOWED_METHODS:
        die(f"Unsupported background provenance method: {method}")
    if provenance_source_is_forbidden(source) or provenance_source_is_forbidden(method):
        die("background provenance cannot point to PPTX/rendered/exported screenshot sources")
    completed_manifest = validate_completed_provenance(project, spec)
    completed_manifest_hash = sha256_file(completed_provenance_path(project))

    entries = []
    for slide_no in expected_slide_numbers(spec):
        path = background_image_path(project, slide_no)
        if not path.exists():
            die(f"Cannot register background provenance; missing GPT-image-2 background edit file: {path}")
        entries.append(image_record(project, path, slide_no))

    manifest: Dict[str, Any] = {
        "version": 1,
        "created_at": now_iso(),
        "required_origin": "GPT-image-2 text-free edit from the matching completed reference, not a local template or screenshot",
        "generator": "gpt-image-2",
        "method": method,
        "source": source,
        "note": note,
        "completed_provenance_sha256": completed_manifest_hash,
        "completed_method": completed_manifest.get("method"),
        "slides": entries,
    }
    if previous:
        manifest["previous"] = {
            "created_at": previous.get("created_at"),
            "method": previous.get("method"),
            "source": previous.get("source"),
            "slides": previous.get("slides", []),
        }
    write_json(background_provenance_path(project), manifest)
    return manifest


def load_completed_provenance(project: Path) -> Dict[str, Any]:
    path = completed_provenance_path(project)
    if not path.exists():
        die(
            "Missing completed image provenance. `completed/` must come from GPT-image-2 image_gen, "
            "not PPTX/PDF/render screenshots. Run `image2slides imagegen --phase completed --execute` "
            "or register native image_gen outputs with `image2slides register-completed`."
        )
    return read_json(path)


def load_background_provenance(project: Path) -> Dict[str, Any]:
    path = background_provenance_path(project)
    if not path.exists():
        die(
            "Missing background image provenance. `background/` must come from GPT-image-2 text-free edits "
            "of `completed/`, not local templates, screenshots, or deterministic drawing. Run native image_gen "
            "background edits and register them with `image2slides register-background`."
        )
    return read_json(path)


def validate_completed_provenance(project: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    manifest = load_completed_provenance(project)
    if manifest.get("generator") != "gpt-image-2":
        die("completed provenance must declare generator `gpt-image-2`")
    method = str(manifest.get("method", ""))
    source = str(manifest.get("source", ""))
    if method not in COMPLETED_ALLOWED_METHODS:
        die(f"completed provenance method is not an accepted image_gen method: {method}")
    if provenance_source_is_forbidden(source) or provenance_source_is_forbidden(method):
        die("completed provenance points to a PPTX/render/PDF screenshot path, which is forbidden")

    entries = {int(item.get("slide")): item for item in manifest.get("slides", []) if item.get("slide") is not None}
    for slide_no in expected_slide_numbers(spec):
        path = completed_image_path(project, slide_no)
        if not path.exists():
            die(f"Missing image_gen completed reference: {path}")
        entry = entries.get(slide_no)
        if not entry:
            die(f"completed provenance is missing slide {slide_no:02d}")
        if project_relative(project, path) != str(entry.get("path")):
            die(f"completed provenance path mismatch for slide {slide_no:02d}")
        current_hash = sha256_file(path)
        if current_hash != entry.get("sha256"):
            die(
                f"completed image changed after provenance registration for slide {slide_no:02d}; "
                "rerun image_gen or re-register only genuine image_gen output"
            )
    return manifest


def validate_background_provenance(project: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    validate_completed_provenance(project, spec)
    manifest = load_background_provenance(project)
    if manifest.get("generator") != "gpt-image-2":
        die("background provenance must declare generator `gpt-image-2`")
    method = str(manifest.get("method", ""))
    source = str(manifest.get("source", ""))
    if method not in BACKGROUND_ALLOWED_METHODS:
        die(f"background provenance method is not an accepted image_gen edit method: {method}")
    if provenance_source_is_forbidden(source) or provenance_source_is_forbidden(method):
        die("background provenance points to a PPTX/render/PDF screenshot path, which is forbidden")
    completed_hash = sha256_file(completed_provenance_path(project))
    if manifest.get("completed_provenance_sha256") != completed_hash:
        die("background provenance is not tied to the current completed provenance; regenerate background edits")

    entries = {int(item.get("slide")): item for item in manifest.get("slides", []) if item.get("slide") is not None}
    for slide_no in expected_slide_numbers(spec):
        path = background_image_path(project, slide_no)
        if not path.exists():
            die(f"Missing GPT-image-2 background edit: {path}")
        entry = entries.get(slide_no)
        if not entry:
            die(f"background provenance is missing slide {slide_no:02d}")
        if project_relative(project, path) != str(entry.get("path")):
            die(f"background provenance path mismatch for slide {slide_no:02d}")
        if sha256_file(path) != entry.get("sha256"):
            die(
                f"background image changed after provenance registration for slide {slide_no:02d}; "
                "rerun image_gen background edit or re-register only genuine image_gen output"
            )
    return manifest


def write_project_docs(project: Path, spec: Dict[str, Any]) -> None:
    knowledge = spec["knowledge_base"]
    knowledge_text = "\n".join(f"- {item}" for item in knowledge) if isinstance(knowledge, list) else str(knowledge)

    brief = f"""# Project Brief

Created: {spec["created_at"]}

## Required Inputs

- Style / tone: {spec["style_tone"]}
- Aspect ratio: {spec["aspect_ratio_normalized"]}
- Slide count: {spec["slide_count"]}
- Purpose: {spec["purpose"]}
- Scene: {spec["scene"]}

## Knowledge Base

{knowledge_text}
"""
    (project / "wiki/00_project_brief.md").write_text(brief, encoding="utf-8")

    wiki_map = """# Wiki Map

- `00_project_brief.md`: required user intent and normalized deck settings
- `01_wiki_map.md`: this structure map
- `02_content_boundary.md`: grep-required versus generation-allowed boundary
- `03_source_registry.yml`: source records for factual claims
- `04_slide_plan.json`: slide text, layout hints, and visual intent
- `grep/`: sourced facts, search notes, citations, images, and extracted text
- `generate/`: generated story scaffolding, prompt drafts, examples, and metaphors
- `knowledge/`: user-provided materials transformed into structured notes
- `sources/`: copied or referenced source files
"""
    (project / "wiki/01_wiki_map.md").write_text(wiki_map, encoding="utf-8")

    boundary_rows = "\n".join(
        f"| {i} | TBD | TBD | TBD | pending |" for i in range(1, int(spec["slide_count"]) + 1)
    )
    boundary = f"""# Content Boundary

Every slide must define what is grep-required and what may be generated.

| Slide | Topic | grep_required | generation_allowed | Status |
| --- | --- | --- | --- | --- |
{boundary_rows}
"""
    (project / "wiki/02_content_boundary.md").write_text(boundary, encoding="utf-8")

    registry = """# Source registry
# Fill with source records before factual claims enter final slides.
#
# - id: source-id
#   type: user | web | pdf | image | official | filing
#   title: Source title
#   path_or_url: path or URL
#   checked_at: ISO timestamp
#   notes: Claim scope and limitations
sources: []
"""
    (project / "wiki/03_source_registry.yml").write_text(registry, encoding="utf-8")

    slides = []
    for i in range(1, int(spec["slide_count"]) + 1):
        if i == 1:
            layout = "cover"
            title = "Deck title"
            text_items = [
                {"role": "title", "text": "Deck title", "font_size": 34, "bold": True},
                {"role": "subtitle", "text": "Subtitle", "font_size": 16, "bold": False},
            ]
        else:
            layout = "content"
            title = f"Slide {i:02d} title"
            text_items = [
                {"role": "title", "text": title, "font_size": 24, "bold": True},
                {"role": "body", "text": "Key point 1\nKey point 2\nKey point 3", "font_size": 14},
            ]
        slides.append(
            {
                "slide": i,
                "title": title,
                "layout": layout,
                "visual_intent": "TBD from wiki and user materials",
                "source_boundary": "mixed",
                "text_items": text_items,
                "speaker_notes": "",
            }
        )
    write_json(project / "wiki/04_slide_plan.json", {"slides": slides})


def cmd_init(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    if project.exists() and any(project.iterdir()) and not args.force:
        existing = project_file(project)
        if not existing.exists():
            die(f"Project directory is not empty: {project} (use --force to scaffold anyway)")
    project.mkdir(parents=True, exist_ok=True)
    mkdirs(project)
    spec = normalize_spec(read_json(Path(args.spec)))
    write_json(project_file(project), spec)
    write_project_docs(project, spec)
    print(f"Initialized Image2Slides project at {project}")


def slide_plan(project: Path) -> Dict[str, Any]:
    return read_json(project / "wiki/04_slide_plan.json")


def ensure_source_layout_boundaries(project: Path, *, write: bool = True) -> int:
    """Mirror source-layer panels into plan-level non-editable layout boundaries."""
    plan_path = project / "wiki/04_slide_plan.json"
    plan = read_json(plan_path)
    changed = 0
    for slide in plan.get("slides", []):
        boundaries = [
            boundary
            for boundary in slide.get("layout_boundaries", [])
            if boundary.get("source") != "source_layer_panel"
        ]
        for idx, layer in enumerate(slide.get("source_layers", []), start=1):
            bbox = layer.get("panel_bbox") or layer.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            boundaries.append(
                {
                    "id": f"source_panel_{idx}",
                    "kind": "non_editable_image_panel",
                    "source": "source_layer_panel",
                    "bbox": [round(float(value), 5) for value in bbox],
                    "editable_text_allowed": False,
                    "asset_internal_text_policy": "preserve_as_image_content",
                    "review_rule": (
                        "source image panel is a planned visual region; do not place editable text inside it "
                        "or strip text contained inside the image asset"
                    ),
                }
            )
        existing = slide.get("layout_boundaries", [])
        if boundaries != existing:
            slide["layout_boundaries"] = boundaries
            changed += 1
        if slide.get("source_layers") and not slide.get("illustration_text_policy"):
            slide["illustration_text_policy"] = (
                "preserve_internal_text_as_image_content; do not extract, delete, or align "
                "asset-internal labels as editable text unless explicitly requested"
            )
            changed += 1
    if changed and write:
        write_json(plan_path, plan)
    return changed


def base_prompt(spec: Dict[str, Any]) -> str:
    return (
        "Use case: productivity-visual. "
        f"Non-visible setup metadata: asset type is a full-slide {spec['aspect_ratio_normalized']} "
        f"presentation composition; style/tone is {spec['style_tone']}; purpose is {spec['purpose']}; "
        f"scene is {spec['scene']}. These setup metadata values are instructions only and must never be "
        "rendered as readable slide text. Use the supplied wiki as the knowledge boundary. "
        "Respect source boundaries: factual claims must come from grep_required material; generated content "
        "may only provide visual metaphor, pacing, and non-factual scaffolding."
    )


def completed_prompt(project: Path, spec: Dict[str, Any], slide: Dict[str, Any]) -> str:
    texts = []
    for item in slide.get("text_items", []):
        text = str(item.get("text", "")).strip()
        if text:
            texts.append(text)
    text_block = "\n".join(texts)
    source_plan = source_layout_instructions(project, spec, slide)
    return (
        base_prompt(spec)
        + f"\nSlide {slide['slide']:02d}: {slide.get('title', '')}\n"
        + f"Layout: {slide.get('layout', 'content')}\n"
        + f"Visual intent: {slide.get('visual_intent', '')}\n"
        + f"Source boundary: {slide.get('source_boundary', 'mixed')}\n"
        + source_plan
        + "Create a polished complete slide reference image with the following exact visible text.\n"
        + f"Text (verbatim):\n{text_block}\n"
        + "Visible text rule: render only the text listed above. Do not add setup labels, style/tone names, "
        + "aspect ratio, page count, purpose, scene, knowledge-base paths, plugin names, workflow names, "
        + "validation marks, file names, watermarks, or badges.\n"
        + "Constraints: professional slide composition, no logos unless provided in the wiki, no unsupported data, "
        + "no patient identifiers, no watermark."
    )


def background_prompt(slide: Dict[str, Any]) -> str:
    return (
        f"Create a text-free background-only version of slide {slide['slide']:02d}: {slide.get('title', '')}.\n"
        "Remove all readable text, letters, numbers, labels, captions, and watermarks only.\n"
        "Preserve every non-text visual element exactly: layout, illustrations, charts without labels, icons, "
        "shapes, lighting, texture, color, perspective, and spacing.\n"
        "The output must be a text-free background for editable PowerPoint overlays.\n"
        "The only difference from the input should be the absence of text."
    )


def _flatten_values(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: List[str] = []
        for item in value:
            items.extend(_flatten_values(item))
        return items
    if isinstance(value, dict):
        items = []
        for item in value.values():
            items.extend(_flatten_values(item))
        return items
    return [str(value)]


def internal_visible_terms(spec: Dict[str, Any]) -> List[str]:
    """Return control-plane terms that should not appear as slide copy."""
    raw_terms: List[str] = []
    for key in ("style_tone", "aspect_ratio", "aspect_ratio_normalized", "purpose", "scene", "knowledge_base"):
        raw_terms.extend(_flatten_values(spec.get(key)))

    slide_count = spec.get("slide_count")
    if slide_count is not None:
        raw_terms.extend(
            [
                f"{slide_count} pages",
                f"{slide_count} page",
                f"{slide_count} slides",
                f"{slide_count} slide",
                f"{slide_count} 页",
                f"{slide_count}页",
                f"{slide_count}枚",
            ]
        )

    raw_terms.extend(
        [
            "Image2Slides",
            "image2slides",
            "minimal validation",
            "plugin validation",
            "workflow validation",
            "最小验证",
            "最小心智验证",
            "白色系课堂汇报",
            "课堂汇报｜",
            "howitworks.docx",
            "spec.json",
        ]
    )

    terms: List[str] = []
    seen = set()
    for term in raw_terms:
        clean = str(term).strip()
        if len(clean) < 3:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(clean)
        path_name = Path(clean).name
        if path_name != clean and len(path_name) >= 3 and path_name.lower() not in seen:
            seen.add(path_name.lower())
            terms.append(path_name)
    return terms


def visible_text_items(plan: Dict[str, Any]) -> List[Tuple[int, str, str]]:
    items: List[Tuple[int, str, str]] = []
    for slide in plan.get("slides", []):
        slide_no = int(slide.get("slide", len(items) + 1))
        for item in slide.get("text_items", []):
            role = str(item.get("role", "text"))
            text = str(item.get("text", ""))
            if text.strip():
                items.append((slide_no, role, text))
    return items


def lint_visible_text(project: Path) -> Dict[str, Any]:
    spec = load_project(project)
    plan = slide_plan(project)
    terms = internal_visible_terms(spec)
    issues = []
    for slide_no, role, text in visible_text_items(plan):
        lower = text.lower()
        for term in terms:
            if term.lower() in lower:
                issues.append(
                    {
                        "slide": slide_no,
                        "role": role,
                        "term": term,
                        "text": text,
                        "reason": "control-plane input appears in visible slide text",
                    }
                )
    return {"created_at": now_iso(), "issue_count": len(issues), "issues": issues}


def write_lint_report(project: Path, report: Dict[str, Any]) -> None:
    write_json(project / "reports/internal_text_lint.json", report)
    lines = [
        "# Internal Text Lint",
        "",
        f"- Issues: {report['issue_count']}",
        "",
        "| Slide | Role | Term | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for issue in report["issues"]:
        lines.append(
            f"| {issue['slide']} | {issue['role']} | `{issue['term']}` | {issue['reason']} |"
        )
    (project / "reports/internal_text_lint.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_lint_visible(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    report = lint_visible_text(project)
    write_lint_report(project, report)
    print(f"Wrote {project / 'reports/internal_text_lint.json'}")
    print(f"Wrote {project / 'reports/internal_text_lint.md'}")
    if args.strict and report["issue_count"]:
        die(f"visible text contains {report['issue_count']} internal/control-plane term(s)")


def parse_image_size(size: str) -> Tuple[int, int]:
    parts = str(size).lower().replace(" ", "").split("x")
    if len(parts) != 2:
        die(f"Cannot compose local images for non-explicit size: {size}")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        die(f"Cannot compose local images for non-explicit size: {size}")
    if width <= 0 or height <= 0:
        die(f"Cannot compose local images for non-explicit size: {size}")
    return width, height


def rgb(value: str, fallback: str = "#fbfcfe") -> Tuple[int, int, int]:
    raw = str(value or fallback).strip().lstrip("#")
    if len(raw) != 6:
        raw = fallback.lstrip("#")
    try:
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except Exception:
        return tuple(int(fallback.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def resolve_project_path(project: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return project / path


def normalized_to_pixels(bbox: Sequence[float], width: int, height: int) -> Tuple[int, int, int, int]:
    if len(bbox) != 4:
        die(f"bbox must have four values: {bbox}")
    x, y, w, h = [float(v) for v in bbox]
    return (
        int(round(x * width)),
        int(round(y * height)),
        int(round((x + w) * width)),
        int(round((y + h) * height)),
    )


def pixels_to_unit_rect(box: Tuple[int, int, int, int], width: int, height: int) -> Tuple[float, float, float, float]:
    return box[0] / width, box[1] / height, box[2] / width, box[3] / height


def inset_box(box: Tuple[int, int, int, int], margin_px: int) -> Tuple[int, int, int, int]:
    margin = max(0, int(margin_px))
    inner = (box[0] + margin, box[1] + margin, box[2] - margin, box[3] - margin)
    if inner[2] <= inner[0] or inner[3] <= inner[1]:
        return box
    return inner


def bool_option(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def trim_whitespace(image: "Image.Image", *, threshold: int = 248, margin: int = 8) -> "Image.Image":
    arr = np.asarray(image.convert("RGB"))
    mask = np.any(arr < threshold, axis=2)
    if not mask.any():
        return image
    ys, xs = np.where(mask)
    x0 = max(0, int(xs.min()) - margin)
    y0 = max(0, int(ys.min()) - margin)
    x1 = min(image.width, int(xs.max()) + margin + 1)
    y1 = min(image.height, int(ys.max()) + margin + 1)
    if (x1 - x0) < image.width * 0.08 or (y1 - y0) < image.height * 0.08:
        return image
    return image.crop((x0, y0, x1, y1))


def edge_connected_blank_mask(
    image: "Image.Image",
    *,
    threshold: int = 248,
    max_saturation: int = 18,
) -> "np.ndarray":
    """Return edge-connected near-white blank pixels.

    Source figures often carry a large white plotting canvas. Pasting that
    canvas opaquely can visually cover the generated panel even when the
    source bbox is mathematically inside the panel. Only edge-connected blank
    is removed, so enclosed white data cells are preserved.
    """
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    near_blank = (
        (arr[:, :, 0] >= threshold)
        & (arr[:, :, 1] >= threshold)
        & (arr[:, :, 2] >= threshold)
        & ((arr.max(axis=2) - arr.min(axis=2)) <= max_saturation)
    )
    height, width = near_blank.shape
    blank = np.zeros((height, width), dtype=bool)
    stack: List[Tuple[int, int]] = []
    for x in range(width):
        if near_blank[0, x]:
            blank[0, x] = True
            stack.append((0, x))
        if near_blank[height - 1, x] and not blank[height - 1, x]:
            blank[height - 1, x] = True
            stack.append((height - 1, x))
    for y in range(height):
        if near_blank[y, 0] and not blank[y, 0]:
            blank[y, 0] = True
            stack.append((y, 0))
        if near_blank[y, width - 1] and not blank[y, width - 1]:
            blank[y, width - 1] = True
            stack.append((y, width - 1))
    while stack:
        y, x = stack.pop()
        for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= yy < height and 0 <= xx < width and near_blank[yy, xx] and not blank[yy, xx]:
                blank[yy, xx] = True
                stack.append((yy, xx))
    return blank


def source_alpha_mask_for_layer(
    source: "Image.Image",
    layer: Dict[str, Any],
    *,
    draw_frame: bool,
) -> Optional["Image.Image"]:
    if draw_frame or not bool_option(layer.get("mask_edge_blank"), True):
        return None
    threshold = int(layer.get("blank_alpha_threshold", 248))
    max_saturation = int(layer.get("blank_alpha_max_saturation", 18))
    blank = edge_connected_blank_mask(source, threshold=threshold, max_saturation=max_saturation)
    if float(blank.mean()) < 0.01:
        return None
    alpha = np.where(blank, 0, 255).astype("uint8")
    return Image.fromarray(alpha, mode="L")


def clamp_box(box: Tuple[int, int, int, int], width: int, height: int) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    x0 = max(0, min(width - 1, int(x0)))
    y0 = max(0, min(height - 1, int(y0)))
    x1 = max(x0 + 1, min(width, int(x1)))
    y1 = max(y0 + 1, min(height, int(y1)))
    return x0, y0, x1, y1


def expand_box(box: Tuple[int, int, int, int], width: int, height: int, pad_x: int, pad_y: int) -> Tuple[int, int, int, int]:
    return clamp_box((box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y), width, height)


def edge_candidates(profile: "np.ndarray", offset: int) -> List[Tuple[int, float]]:
    if profile.size == 0:
        return []
    high_cutoff = max(
        2.5,
        float(np.percentile(profile, 97.5)),
        float(profile.mean() + profile.std() * 1.75),
    )
    cutoff = max(2.5, min(high_cutoff, float(np.percentile(profile, 95.0)) * 1.5))
    raw = [int(i) for i in np.where(profile >= cutoff)[0]]
    if not raw:
        strongest = int(profile.argmax())
        if float(profile[strongest]) >= 2.5:
            return [(offset + strongest, float(profile[strongest]))]
        return []
    clusters: List[List[int]] = []
    for idx in raw:
        if not clusters or idx > clusters[-1][-1] + 2:
            clusters.append([idx])
        else:
            clusters[-1].append(idx)
    candidates: List[Tuple[int, float]] = []
    for cluster in clusters:
        best = max(cluster, key=lambda i: float(profile[i]))
        candidates.append((offset + best, float(profile[best])))
    return candidates


def nearest_edge(candidates: List[Tuple[int, float]], target: int, window: int) -> Optional[int]:
    scoped = [item for item in candidates if abs(item[0] - target) <= window]
    if not scoped:
        return None
    scoped.sort(key=lambda item: (abs(item[0] - target), -item[1]))
    return scoped[0][0]


def mask_segments(row: "np.ndarray", *, min_width: int) -> List[Tuple[int, int]]:
    segments: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for idx, value in enumerate(row):
        if bool(value) and start is None:
            start = idx
        if start is not None and (not bool(value) or idx == len(row) - 1):
            end = idx if not bool(value) else idx + 1
            if end - start >= min_width:
                segments.append((start, end))
            start = None
    return segments


def panel_line_candidates(
    mask: "np.ndarray",
    search: Tuple[int, int, int, int],
    declared_box: Tuple[int, int, int, int],
    target_y: int,
) -> List[Tuple[float, int, int, int]]:
    sx0, sy0, sx1, sy1 = search
    dx0, _, dx1, _ = declared_box
    declared_w = max(1, dx1 - dx0)
    center_x = (dx0 + dx1) / 2.0
    min_segment_w = max(64, int(declared_w * 0.30))
    candidates: List[Tuple[float, int, int, int]] = []
    for y in range(sy0, sy1):
        row = mask[y, sx0:sx1]
        for start, end in mask_segments(row, min_width=min_segment_w):
            x0 = sx0 + start
            x1 = sx0 + end
            overlap = max(0, min(x1, dx1) - max(x0, dx0))
            if not (x0 - 120 <= center_x <= x1 + 120 or overlap >= min_segment_w * 0.25):
                continue
            score = abs(y - target_y) - (x1 - x0) * 0.002
            candidates.append((score, y, x0, x1))
    candidates.sort(key=lambda item: item[0])
    return candidates[:12]


def panel_vertical_line_candidates(
    mask: "np.ndarray",
    search: Tuple[int, int, int, int],
    declared_box: Tuple[int, int, int, int],
    target_x: int,
) -> List[Tuple[float, int, int, int]]:
    sx0, sy0, sx1, sy1 = search
    _, dy0, _, dy1 = declared_box
    declared_h = max(1, dy1 - dy0)
    center_y = (dy0 + dy1) / 2.0
    min_segment_h = max(48, int(declared_h * 0.28))
    candidates: List[Tuple[float, int, int, int]] = []
    for x in range(sx0, sx1):
        col = mask[sy0:sy1, x]
        for start, end in mask_segments(col, min_width=min_segment_h):
            y0 = sy0 + start
            y1 = sy0 + end
            overlap = max(0, min(y1, dy1) - max(y0, dy0))
            if not (y0 - 120 <= center_y <= y1 + 120 or overlap >= min_segment_h * 0.25):
                continue
            score = abs(x - target_x) - (y1 - y0) * 0.002
            candidates.append((score, x, y0, y1))
    candidates.sort(key=lambda item: item[0])
    return candidates[:12]


def detect_panel_box_from_light_border(
    image: "Image.Image",
    declared_box: Tuple[int, int, int, int],
) -> Optional[Tuple[int, int, int, int]]:
    """Detect light blue rounded-card borders from a source-free background."""
    width, height = image.size
    declared_box = clamp_box(declared_box, width, height)
    panel_w = max(1, declared_box[2] - declared_box[0])
    panel_h = max(1, declared_box[3] - declared_box[1])
    search = expand_box(declared_box, width, height, max(160, int(panel_w * 0.45)), max(160, int(panel_h * 0.45)))
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    red = arr[:, :, 0]
    green = arr[:, :, 1]
    blue = arr[:, :, 2]
    # Imagegen panel borders in this workflow are pale blue strokes. This mask
    # intentionally ignores dark chart strokes and source figure edges.
    mask = (
        (red > 165)
        & (green > 180)
        & (blue > 195)
        & ((blue - red) > 8)
        & ((green - red) > 0)
        & ((blue - green) >= 0)
    )
    top_candidates = panel_line_candidates(mask, search, declared_box, declared_box[1])
    bottom_candidates = panel_line_candidates(mask, search, declared_box, declared_box[3])
    left_candidates = panel_vertical_line_candidates(mask, search, declared_box, declared_box[0])
    right_candidates = panel_vertical_line_candidates(mask, search, declared_box, declared_box[2])
    if not top_candidates or not bottom_candidates:
        return None

    best: Optional[Tuple[float, Tuple[int, int, int, int]]] = None
    min_height = max(48, int(panel_h * 0.25))
    min_width = max(96, int(panel_w * 0.25))
    declared_center_x = (declared_box[0] + declared_box[2]) / 2.0
    declared_center_y = (declared_box[1] + declared_box[3]) / 2.0
    if left_candidates and right_candidates:
        for left in left_candidates:
            for right in right_candidates:
                left_x = left[1]
                right_x = right[1]
                if right_x <= left_x + min_width:
                    continue
                for top in top_candidates:
                    top_y = top[1]
                    if top[2] > left_x + panel_w * 0.35 or top[3] < right_x - panel_w * 0.35:
                        continue
                    for bottom in bottom_candidates:
                        bottom_y = bottom[1]
                        if bottom_y <= top_y + min_height:
                            continue
                        if bottom[2] > left_x + panel_w * 0.35 or bottom[3] < right_x - panel_w * 0.35:
                            continue
                        # A rounded panel's vertical strokes do not span the
                        # rounded corners. They still need to cover most of
                        # the candidate height, otherwise a decorative line can
                        # be mistaken for the actual panel.
                        height_span = bottom_y - top_y
                        left_overlap = max(0, min(left[3], bottom_y) - max(left[2], top_y))
                        right_overlap = max(0, min(right[3], bottom_y) - max(right[2], top_y))
                        if left_overlap < height_span * 0.45 or right_overlap < height_span * 0.45:
                            continue
                        box = clamp_box((left_x, top_y, right_x + 1, bottom_y + 1), width, height)
                        box_center_x = (box[0] + box[2]) / 2.0
                        box_center_y = (box[1] + box[3]) / 2.0
                        center_penalty = abs(box_center_x - declared_center_x) * 0.18 + abs(box_center_y - declared_center_y) * 0.35
                        edge_penalty = abs(top_y - declared_box[1]) + abs(bottom_y - declared_box[3]) * 0.65
                        score = left[0] + right[0] + top[0] + bottom[0] + center_penalty + edge_penalty
                        if best is None or score < best[0]:
                            best = (score, box)
    if best is not None:
        return best[1]

    for top in top_candidates:
        for bottom in bottom_candidates:
            top_y = top[1]
            bottom_y = bottom[1]
            if abs(bottom_y - top_y) < min_height:
                continue
            left = min(top[2], bottom[2])
            right = max(top[3], bottom[3])
            intersect_left = max(top[2], bottom[2])
            intersect_right = min(top[3], bottom[3])
            if intersect_right - intersect_left >= min_width:
                left = intersect_left
                right = intersect_right
            if right - left < min_width:
                continue
            y0 = min(top_y, bottom_y)
            y1 = max(top_y, bottom_y) + 1
            score = abs(top_y - declared_box[1]) + abs(bottom_y - declared_box[3]) - (right - left) * 0.002
            if best is None or score < best[0]:
                best = (score, clamp_box((left, y0, right, y1), width, height))
    return best[1] if best else None


def detect_panel_box_from_image(
    image: "Image.Image",
    declared_box: Tuple[int, int, int, int],
) -> Optional[Tuple[int, int, int, int]]:
    """Detect the visible panel edge nearest to a declared source-layer panel."""
    require_image_libs()
    width, height = image.size
    declared_box = clamp_box(declared_box, width, height)
    light_border_panel = detect_panel_box_from_light_border(image, declared_box)
    if light_border_panel is not None:
        return light_border_panel
    panel_w = declared_box[2] - declared_box[0]
    panel_h = declared_box[3] - declared_box[1]
    pad_x = max(96, int(panel_w * 0.28), int(width * 0.035))
    pad_y = max(96, int(panel_h * 0.75), int(height * 0.04))
    search = expand_box(declared_box, width, height, pad_x, pad_y)
    if search[2] - search[0] < 32 or search[3] - search[1] < 32:
        return None

    arr = np.asarray(image.convert("RGB")).astype(np.float32)
    lum = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114
    crop = lum[search[1] : search[3], search[0] : search[2]]
    if crop.shape[0] < 3 or crop.shape[1] < 3:
        return None

    v_profile = np.abs(np.diff(crop, axis=1)).mean(axis=0)
    h_profile = np.abs(np.diff(crop, axis=0)).mean(axis=1)
    verticals = edge_candidates(v_profile, search[0])
    horizontals = edge_candidates(h_profile, search[1])
    window_x = max(120, int(panel_w * 0.32), int(width * 0.04))
    window_y = max(120, int(panel_h * 0.75), int(height * 0.05))
    left = nearest_edge(verticals, declared_box[0], window_x)
    right = nearest_edge(verticals, declared_box[2], window_x)
    top = nearest_edge(horizontals, declared_box[1], window_y)
    bottom = nearest_edge(horizontals, declared_box[3], window_y)
    if left is None or right is None or top is None or bottom is None:
        return None
    if right <= left + 24 or bottom <= top + 24:
        return None
    center_x = (declared_box[0] + declared_box[2]) / 2.0
    center_y = (declared_box[1] + declared_box[3]) / 2.0
    if not (left - window_x <= center_x <= right + window_x and top - window_y <= center_y <= bottom + window_y):
        return None
    return clamp_box((left, top, right + 1, bottom + 1), width, height)


def load_compose_font(size_px: int) -> Any:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size_px, index=0)  # type: ignore[name-defined]
            except Exception:
                continue
    return ImageFont.load_default()  # type: ignore[name-defined]


def draw_wrapped(draw: Any, text: str, box: Tuple[int, int, int, int], font_obj: Any, fill: Tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    max_width = max(1, x1 - x0)
    line_height = max(14, int(getattr(font_obj, "size", 14) * 1.2))
    lines: List[str] = []
    for raw_line in str(text).splitlines():
        current = ""
        for char in raw_line:
            candidate = current + char
            try:
                width = draw.textlength(candidate, font=font_obj)
            except Exception:
                width = len(candidate) * 8
            if width <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
        lines.append(current)
    y = y0
    for line in lines:
        if y + line_height > y1:
            break
        draw.text((x0, y), line, font=font_obj, fill=fill)
        y += line_height


def source_image_for_layer(path: Path, layer: Dict[str, Any], *, draw_frame: bool) -> "Image.Image":
    source = Image.open(path).convert("RGB")
    if bool_option(layer.get("trim_whitespace"), not draw_frame):
        threshold = int(layer.get("trim_threshold", 248))
        margin = int(layer.get("trim_margin_px", 8))
        source = trim_whitespace(source, threshold=threshold, margin=margin)
    return source


def source_image_and_mask_for_layer(
    path: Path,
    layer: Dict[str, Any],
    *,
    draw_frame: bool,
) -> Tuple["Image.Image", Optional["Image.Image"]]:
    source = source_image_for_layer(path, layer, draw_frame=draw_frame)
    return source, source_alpha_mask_for_layer(source, layer, draw_frame=draw_frame)


def aspect_adjusted_panel_box(
    panel: Tuple[int, int, int, int],
    source_size: Tuple[int, int],
    margin_px: int,
) -> Tuple[int, int, int, int]:
    source_w, source_h = source_size
    if source_w <= 0 or source_h <= 0:
        return panel
    x0, y0, x1, y1 = panel
    panel_w = max(1, x1 - x0)
    panel_h = max(1, y1 - y0)
    inner_w = max(1, panel_w - margin_px * 2)
    inner_h = max(1, panel_h - margin_px * 2)
    source_ratio = source_w / max(1, source_h)
    panel_ratio = inner_w / max(1, inner_h)
    if abs(source_ratio - panel_ratio) < 0.015:
        return panel

    if panel_ratio > source_ratio:
        new_inner_w = max(1, int(round(inner_h * source_ratio)))
        new_panel_w = min(panel_w, new_inner_w + margin_px * 2)
        new_panel_h = panel_h
    else:
        new_inner_h = max(1, int(round(inner_w / source_ratio)))
        new_panel_w = panel_w
        new_panel_h = min(panel_h, new_inner_h + margin_px * 2)

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    nx0 = int(round(cx - new_panel_w / 2.0))
    ny0 = int(round(cy - new_panel_h / 2.0))
    return nx0, ny0, nx0 + int(new_panel_w), ny0 + int(new_panel_h)


def panel_aspect_from_source(layer: Dict[str, Any]) -> bool:
    return bool_option(layer.get("panel_aspect_from_source"), True)


def panel_detection_enabled(layer: Dict[str, Any]) -> bool:
    return bool_option(layer.get("detect_panel"), True)


def fit_source_bounds(source_size: Tuple[int, int], box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Fit source into box by maximizing scale, centering, and minimizing B - A slack."""
    x0, y0, x1, y1 = box
    target_w = max(1, x1 - x0)
    target_h = max(1, y1 - y0)
    source_w, source_h = source_size
    scale = min(target_w / max(1, source_w), target_h / max(1, source_h))
    fitted_w = max(1, int(round(source_w * scale)))
    fitted_h = max(1, int(round(source_h * scale)))
    px = x0 + (target_w - fitted_w) // 2
    py = y0 + (target_h - fitted_h) // 2
    return px, py, px + fitted_w, py + fitted_h


def source_panel_detection_reference(
    slide: Dict[str, Any],
    image: "Image.Image",
    image_size: Tuple[int, int],
) -> "Image.Image":
    """Return the source-free panel reference used for detecting generated panel borders.

    The declared source-layer box is only a search hint. Never clear that hint
    area before detection; if the hint is wrong, clearing it creates a fake
    edge and turns the wrong paste location into a false panel.
    """
    _ = slide, image_size
    return image.copy()


def layer_margin_px(layer: Dict[str, Any], *, has_panel: bool) -> int:
    for key in ("fit_margin_px", "panel_margin_px", "padding_px"):
        if key in layer:
            return max(0, int(layer[key]))
    return DEFAULT_PANEL_MARGIN_PX if has_panel else 20


def layer_panel_box(layer: Dict[str, Any], width: int, height: int) -> Tuple[int, int, int, int]:
    raw = layer.get("panel_bbox") or layer.get("bbox")
    return normalized_to_pixels(raw, width, height)


def source_fit_geometry(
    project: Path,
    layer: Dict[str, Any],
    image_size: Tuple[int, int],
    *,
    draw_frame: bool,
    panel_image: Optional["Image.Image"] = None,
) -> Dict[str, Any]:
    width, height = image_size
    declared_panel = layer_panel_box(layer, width, height)
    margin = layer_margin_px(layer, has_panel=bool(layer.get("panel_bbox")))
    source_path = resolve_project_path(project, str(layer.get("path", "")))
    source_size = (max(1, declared_panel[2] - declared_panel[0]), max(1, declared_panel[3] - declared_panel[1]))
    if source_path.exists():
        source = source_image_for_layer(source_path, layer, draw_frame=draw_frame)
        source_size = source.size

    detected_panel = None
    if panel_image is not None and panel_detection_enabled(layer):
        reference = panel_image.convert("RGB")
        if reference.size != image_size:
            reference = reference.resize(image_size, Image.Resampling.LANCZOS)
        detected_panel = detect_panel_box_from_image(reference, declared_panel)
        if detected_panel is not None and not draw_frame:
            # Native imagegen panels carry borders, shadows, and rounded-card
            # affordances. A too-small layer override can be mathematically
            # inside the panel while visually covering the panel shell.
            margin = max(margin, DEFAULT_GENERATED_PANEL_MIN_MARGIN_PX)

    rejected_detected_panel = None

    actual_panel = detected_panel or declared_panel
    panel = actual_panel
    fit_region = inset_box(panel, margin)
    paste_box = fit_source_bounds(source_size, fit_region)
    fit_w = max(1, fit_region[2] - fit_region[0])
    fit_h = max(1, fit_region[3] - fit_region[1])
    paste_w = max(1, paste_box[2] - paste_box[0])
    paste_h = max(1, paste_box[3] - paste_box[1])
    fit_center = ((fit_region[0] + fit_region[2]) / 2.0, (fit_region[1] + fit_region[3]) / 2.0)
    paste_center = ((paste_box[0] + paste_box[2]) / 2.0, (paste_box[1] + paste_box[3]) / 2.0)
    return {
        "panel_box": panel,
        "actual_panel_box": actual_panel,
        "declared_panel_box": declared_panel,
        "detected_panel_box": detected_panel,
        "rejected_detected_panel_box": rejected_detected_panel,
        "fit_region": fit_region,
        "paste_box": paste_box,
        "source_size": source_size,
        "margin_px": margin,
        "panel_aspect_from_source": panel_aspect_from_source(layer),
        "slack_px": (fit_w - paste_w, fit_h - paste_h),
        "center_delta_px": (paste_center[0] - fit_center[0], paste_center[1] - fit_center[1]),
    }


def paste_source_image(
    target: "Image.Image",
    project: Path,
    path: Path,
    layer: Dict[str, Any],
    *,
    canvas_backed: bool,
    panel_image: Optional["Image.Image"] = None,
    geometry: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, int, int]:
    if geometry is None:
        geometry = source_fit_geometry(project, layer, target.size, draw_frame=canvas_backed, panel_image=panel_image)
    source, source_mask = source_image_and_mask_for_layer(path, layer, draw_frame=canvas_backed)
    paste_box = geometry["paste_box"]
    fitted_w = paste_box[2] - paste_box[0]
    fitted_h = paste_box[3] - paste_box[1]
    source = source.resize((fitted_w, fitted_h), Image.Resampling.LANCZOS)
    if source_mask is not None:
        source_mask = source_mask.resize((fitted_w, fitted_h), Image.Resampling.LANCZOS)
    if not canvas_backed:
        target.paste(source, paste_box[:2], source_mask)
        return paste_box
    x0, y0, x1, y1 = geometry["panel_box"]
    target_w = max(1, x1 - x0)
    target_h = max(1, y1 - y0)
    canvas = Image.new("RGB", (target_w, target_h), (255, 255, 255))
    canvas.paste(source, (paste_box[0] - x0, paste_box[1] - y0), source_mask)
    target.paste(canvas, (x0, y0))
    return paste_box


def source_layout_instructions(project: Path, spec: Dict[str, Any], slide: Dict[str, Any]) -> str:
    layers = slide.get("source_layers", [])
    if not layers:
        return ""
    width, height = parse_image_size(spec["image_size"])
    lines = [
        "Source-locked panel plan for GPT-image-2: reserve blank visual panels for exact source insertion later; "
        "do not redraw, restyle, crop, relabel, or mutate source data/results in the generated image. "
        "Every reserved source panel is a non-editable visual boundary: do not place editable slide text inside it. "
        "Text already printed inside a source chart, diagram, schematic, or icon belongs to the image asset and must be "
        "preserved as image content, not extracted into the PowerPoint text alignment mask unless the user explicitly asks.\n"
    ]
    for idx, layer in enumerate(layers, start=1):
        raw_path = str(layer.get("path", "")).strip()
        if not raw_path:
            continue
        declared = layer_panel_box(layer, width, height)
        margin = layer_margin_px(layer, has_panel=bool(layer.get("panel_bbox")))
        if not bool_option(layer.get("draw_frame"), False) and panel_detection_enabled(layer):
            margin = max(margin, DEFAULT_GENERATED_PANEL_MIN_MARGIN_PX)
        source_size = (max(1, declared[2] - declared[0]), max(1, declared[3] - declared[1]))
        source_path = resolve_project_path(project, raw_path)
        if source_path.exists() and Image is not None:
            try:
                source_size = source_image_for_layer(source_path, layer, draw_frame=False).size
            except Exception:
                source_size = (max(1, declared[2] - declared[0]), max(1, declared[3] - declared[1]))
        planned = aspect_adjusted_panel_box(declared, source_size, margin) if panel_aspect_from_source(layer) else declared
        planned = clamp_box(planned, width, height)
        px0, py0, px1, py1 = pixels_to_unit_rect(planned, width, height)
        ratio = source_size[0] / max(1, source_size[1])
        lines.append(
            "- Layer "
            + str(idx)
            + f": blank panel bbox [{px0:.4f}, {py0:.4f}, {px1 - px0:.4f}, {py1 - py0:.4f}], "
            + f"source aspect {ratio:.3f}:1, inset margin d={margin}px. "
            + "The panel proportion must match the source aspect so later pixel insertion fits without touching borders. "
            + "This panel is a planned non-composable region for editable text. "
            + "If the composition cannot preserve this ratio, regenerate the panel instead of stretching or cropping the source.\n"
        )
    return "".join(lines)


def source_size_for_layer(
    project: Path,
    layer: Dict[str, Any],
    fallback_size: Tuple[int, int],
    *,
    draw_frame: bool = False,
) -> Tuple[int, int]:
    source_path = resolve_project_path(project, str(layer.get("path", "")))
    if source_path.exists() and Image is not None:
        try:
            return source_image_for_layer(source_path, layer, draw_frame=draw_frame).size
        except Exception:
            return fallback_size
    return fallback_size


def normalize_source_panels(project: Path, *, write: bool = True) -> int:
    """Rewrite source-layer panel boxes so the panel itself matches trimmed source aspect."""
    require_image_libs()
    spec = load_project(project)
    width, height = parse_image_size(spec["image_size"])
    plan_path = project / "wiki/04_slide_plan.json"
    plan = read_json(plan_path)
    changed = 0
    for slide in plan.get("slides", []):
        for layer in slide.get("source_layers", []):
            if not panel_aspect_from_source(layer):
                continue
            declared = layer_panel_box(layer, width, height)
            margin = layer_margin_px(layer, has_panel=bool(layer.get("panel_bbox")))
            fallback = (max(1, declared[2] - declared[0]), max(1, declared[3] - declared[1]))
            draw_frame = bool_option(layer.get("draw_frame"), False)
            source_size = source_size_for_layer(project, layer, fallback, draw_frame=draw_frame)
            adjusted = clamp_box(aspect_adjusted_panel_box(declared, source_size, margin), width, height)
            if adjusted == declared:
                continue
            x0, y0, x1, y1 = pixels_to_unit_rect(adjusted, width, height)
            layer["panel_bbox"] = [
                round(x0, 5),
                round(y0, 5),
                round(x1 - x0, 5),
                round(y1 - y0, 5),
            ]
            changed += 1
    if changed and write:
        write_json(plan_path, plan)
    return changed


def load_base_background(project: Path, raw_base_dir: Optional[str], slide_no: int, size: Tuple[int, int], fallback_color: Tuple[int, int, int]) -> "Image.Image":
    if not raw_base_dir:
        return Image.new("RGB", size, fallback_color)
    base_dir = resolve_project_path(project, raw_base_dir)
    candidates = [
        base_dir / f"slide_{slide_no:02d}_base.png",
        base_dir / f"slide_{slide_no:02d}_background.png",
        base_dir / f"slide_{slide_no:02d}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            base = Image.open(candidate).convert("RGB")
            if base.size != size:
                base = base.resize(size, Image.Resampling.LANCZOS)
            return base
    die(f"base background not found for slide {slide_no}: {base_dir}")


def load_native_panel_reference(project: Path, slide_no: int, size: Tuple[int, int]) -> Optional["Image.Image"]:
    base_dir = project / "tmp/native_imagegen"
    candidates = [
        base_dir / f"slide_{slide_no:02d}_base.png",
        base_dir / f"slide_{slide_no:02d}_background.png",
        base_dir / f"slide_{slide_no:02d}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            base = Image.open(candidate).convert("RGB")
            if base.size != size:
                base = base.resize(size, Image.Resampling.LANCZOS)
            return base
    return None


def draw_text_items(image: "Image.Image", slide: Dict[str, Any]) -> None:
    draw = ImageDraw.Draw(image)  # type: ignore[name-defined]
    width, height = image.size
    for item in slide.get("text_items", []):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        bbox = item.get("bbox") or default_bbox(str(item.get("role", "body")), 0)
        box = normalized_to_pixels(bbox, width, height)
        size_px = max(12, int(round(float(item.get("font_size") or 14) * 2.05)))
        font_obj = load_compose_font(size_px)
        draw_wrapped(draw, text, box, font_obj, rgb(str(item.get("color", "#152033")), "#152033"))


def cmd_compose_source_locked(args: argparse.Namespace) -> None:
    require_image_libs()
    project = Path(args.project).resolve()
    spec = load_project(project)
    previous_provenance = validate_completed_provenance(project, spec)
    previous_background_provenance = validate_background_provenance(project, spec)
    previous_method = str(previous_provenance.get("method"))
    previous_background_method = str(previous_background_provenance.get("method"))
    already_source_locked = previous_method.endswith("_with_source_locked_patch") or previous_background_method.endswith(
        "_with_source_locked_patch"
    )
    if already_source_locked and not bool(getattr(args, "force", False)):
        die(
            "This image pair is already source-locked. Refusing to mutate generated completed/background images again; "
            "restore a clean pre-source-lock image_gen checkpoint or rerun with --force only after deliberate review."
        )
    plan = slide_plan(project)
    width, height = parse_image_size(spec["image_size"])

    for slide in plan.get("slides", []):
        slide_no = int(slide["slide"])
        completed_path = completed_image_path(project, slide_no)
        background_path = background_image_path(project, slide_no)
        if not completed_path.exists():
            die(f"Missing GPT-image-2 completed reference: {completed_path}")
        if not background_path.exists():
            die(
                f"Missing GPT-image-2 background edit: {background_path}. "
                "Run `image2slides imagegen --phase background --execute` before source locking."
            )
        completed = Image.open(completed_path).convert("RGB")
        background = Image.open(background_path).convert("RGB")
        if completed.size != (width, height):
            completed = completed.resize((width, height), Image.Resampling.LANCZOS)
        if background.size != (width, height):
            background = background.resize((width, height), Image.Resampling.LANCZOS)
        panel_reference = load_native_panel_reference(project, slide_no, (width, height)) or background
        detection_reference = source_panel_detection_reference(slide, panel_reference, (width, height))

        layer_geometries: List[Tuple[Dict[str, Any], Path, Dict[str, Any], bool]] = []
        for layer in slide.get("source_layers", []):
            raw_path = str(layer.get("path", "")).strip()
            if not raw_path:
                continue
            source_path = resolve_project_path(project, raw_path)
            if not source_path.exists():
                die(f"source layer not found for slide {slide_no}: {source_path}")
            draw_frame = bool_option(layer.get("draw_frame"), False)
            if not draw_frame and not panel_detection_enabled(layer):
                die(
                    f"source layer panel detection is disabled for slide {slide_no}; "
                    "generated panels must be located from their actual edges before source insertion"
                )
            geometry = source_fit_geometry(
                project,
                layer,
                (width, height),
                draw_frame=draw_frame,
                panel_image=detection_reference,
            )
            if not draw_frame and geometry.get("detected_panel_box") is None:
                die(
                    f"source layer panel was not detected for slide {slide_no}; "
                    "the declared source bbox is only a search hint, not the panel. "
                    "Provide a source-free imagegen panel reference under tmp/native_imagegen or regenerate the background."
                )
            layer_geometries.append((layer, source_path, geometry, draw_frame))

        for layer, _source_path, geometry, draw_frame in layer_geometries:
            box = geometry["panel_box"]
            radius = int(layer.get("radius_px", 24))
            if draw_frame:
                for image in (completed, background):
                    draw = ImageDraw.Draw(image)  # type: ignore[name-defined]
                    draw.rounded_rectangle(box, radius=radius, fill=(255, 255, 255), outline=rgb("#d9e3ef"), width=2)

        for layer, source_path, geometry, draw_frame in layer_geometries:
            paste_source_image(
                completed,
                project,
                source_path,
                layer,
                canvas_backed=draw_frame,
                panel_image=panel_reference,
                geometry=geometry,
            )
            paste_source_image(
                background,
                project,
                source_path,
                layer,
                canvas_backed=draw_frame,
                panel_image=panel_reference,
                geometry=geometry,
            )

        completed.save(completed_path)
        background.save(background_path)

    patch_methods = {
        "api_imagegen_cli": "api_imagegen_cli_with_source_locked_patch",
        "registered_native_image_gen": "registered_native_image_gen_with_source_locked_patch",
        "test_image_gen_fixture": "test_image_gen_fixture_with_source_locked_patch",
    }
    patched_method = patch_methods.get(previous_method, "native_image_gen_with_source_locked_patch")
    write_completed_provenance(
        project,
        spec,
        method=patched_method,
        source=str(previous_provenance.get("source", "image_gen completed references")),
        note="Exact source_layers were pasted onto existing image_gen completed/background pairs; no local text composition was used.",
        previous=previous_provenance,
    )
    background_patch_methods = {
        "api_imagegen_cli_edit": "api_imagegen_cli_edit_with_source_locked_patch",
        "registered_native_image_gen_edit": "registered_native_image_gen_edit_with_source_locked_patch",
        "test_image_gen_edit_fixture": "test_image_gen_edit_fixture_with_source_locked_patch",
    }
    patched_background_method = background_patch_methods.get(
        previous_background_method,
        "native_image_gen_edit_with_source_locked_patch",
    )
    write_background_provenance(
        project,
        spec,
        method=patched_background_method,
        source=str(previous_background_provenance.get("source", "image_gen background edits")),
        note="Exact source_layers were pasted onto existing GPT-image-2 background edits; no local template background was substituted.",
        previous=previous_background_provenance,
    )

    print(f"Patched source-locked figures into existing image_gen completed/background pairs")
    print(f"Wrote completed provenance to {completed_provenance_path(project)}")
    print(f"Wrote background provenance to {background_provenance_path(project)}")


def cmd_queue(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    normalized = normalize_source_panels(project)
    if normalized:
        print(f"Normalized {normalized} source-layer panel bbox(es) to trimmed source aspect")
    boundary_changes = ensure_source_layout_boundaries(project)
    if boundary_changes:
        print(f"Wrote {boundary_changes} source panel layout boundary update(s)")
    slides = slide_plan(project)["slides"]
    completed_path = project / "prompts/completed_prompts.jsonl"
    background_path = project / "prompts/background_edit_prompts.jsonl"
    with completed_path.open("w", encoding="utf-8") as handle:
        for slide in slides:
            idx = int(slide["slide"])
            job = {
                "prompt": completed_prompt(project, spec, slide),
                "out": f"slide_{idx:02d}_completed.png",
                "model": "gpt-image-2",
                "size": spec["image_size"],
                "quality": "high",
            }
            handle.write(json.dumps(job, ensure_ascii=False) + "\n")
    with background_path.open("w", encoding="utf-8") as handle:
        for slide in slides:
            idx = int(slide["slide"])
            job = {
                "slide": idx,
                "image": f"completed/slide_{idx:02d}_completed.png",
                "out": f"slide_{idx:02d}_background.png",
                "prompt": background_prompt(slide),
                "model": "gpt-image-2",
                "size": spec["image_size"],
                "quality": "high",
            }
            handle.write(json.dumps(job, ensure_ascii=False) + "\n")
    print(f"Wrote {completed_path}")
    print(f"Wrote {background_path}")


def cmd_normalize_source_panels(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    changed = normalize_source_panels(project, write=not args.check)
    boundary_changes = ensure_source_layout_boundaries(project, write=not args.check)
    if args.check:
        print(f"Source-layer panel bbox(es) needing normalization: {changed}")
        print(f"Source panel layout boundary update(s) needed: {boundary_changes}")
        if args.strict and (changed or boundary_changes):
            die(
                f"{changed} source-layer panel bbox(es) do not match trimmed source aspect; "
                f"{boundary_changes} source panel boundary update(s) are missing"
            )
        return
    print(f"Normalized {changed} source-layer panel bbox(es) to trimmed source aspect")
    if boundary_changes:
        print(f"Wrote {boundary_changes} source panel layout boundary update(s)")


def default_imagegen_cli() -> Path:
    if os.getenv("IMAGE_GEN"):
        return Path(os.environ["IMAGE_GEN"]).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return codex_home / "skills/.system/imagegen/scripts/image_gen.py"


def run_command(command: Sequence[str], *, dry_run: bool) -> None:
    if dry_run:
        print(json.dumps({"command": list(command)}, ensure_ascii=False))
        return
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        die(f"imagegen CLI failed with exit code {exc.returncode}: {' '.join(command)}", exc.returncode)


def cmd_imagegen(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    if args.execute == args.dry_run:
        die("Use exactly one of --dry-run or --execute")
    imagegen = Path(args.imagegen_cli).expanduser() if args.imagegen_cli else default_imagegen_cli()
    if not imagegen.exists():
        die(f"imagegen CLI not found: {imagegen}")
    cmd_queue(argparse.Namespace(project=str(project)))

    if args.phase == "completed":
        command = [
            sys.executable,
            str(imagegen),
            "generate-batch",
            "--input",
            str(project / "prompts/completed_prompts.jsonl"),
            "--out-dir",
            str(project / "completed"),
            "--model",
            "gpt-image-2",
            "--size",
            spec["image_size"],
            "--quality",
            args.quality,
            "--concurrency",
            str(args.concurrency),
            "--force",
        ]
        if args.dry_run:
            command.append("--dry-run")
        run_command(command, dry_run=False)
        if not args.dry_run:
            write_completed_provenance(
                project,
                spec,
                method="api_imagegen_cli",
                source="image2slides imagegen --phase completed --execute",
                note="Generated by GPT-image-2 through the imagegen CLI fallback.",
            )
            print(f"Wrote {completed_provenance_path(project)}")
        return

    jobs = [
        json.loads(line)
        for line in (project / "prompts/background_edit_prompts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    validate_completed_provenance(project, spec)
    for job in jobs:
        prompt_file = project / "tmp/imagegen" / f"slide_{int(job['slide']):02d}_background_prompt.txt"
        prompt_file.write_text(job["prompt"], encoding="utf-8")
        image_path = project / job["image"]
        out_path = project / "background" / job["out"]
        command = [
            sys.executable,
            str(imagegen),
            "edit",
            "--image",
            str(image_path),
            "--prompt-file",
            str(prompt_file),
            "--out",
            str(out_path),
            "--model",
            "gpt-image-2",
            "--size",
            spec["image_size"],
            "--quality",
            args.quality,
            "--force",
        ]
        if args.dry_run:
            command.append("--dry-run")
        if args.dry_run and not image_path.exists():
            print(json.dumps({"command": command, "note": "completed image missing; command preview only"}, ensure_ascii=False))
            continue
        run_command(command, dry_run=False)
    if not args.dry_run:
        write_background_provenance(
            project,
            spec,
            method="api_imagegen_cli_edit",
            source="image2slides imagegen --phase background --execute",
            note="Generated by GPT-image-2 edit from completed references through the imagegen CLI fallback.",
        )
        print(f"Wrote {background_provenance_path(project)}")


def cmd_register_completed(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    write_completed_provenance(
        project,
        spec,
        method=args.method,
        source=args.source,
        note=args.note,
    )
    print(f"Wrote {completed_provenance_path(project)}")


def cmd_register_background(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    write_background_provenance(
        project,
        spec,
        method=args.method,
        source=args.source,
        note=args.note,
    )
    print(f"Wrote {background_provenance_path(project)}")


def color_hex(rgb: Sequence[int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def dominant_color(image: "Image.Image") -> str:
    small = image.convert("RGB").resize((160, max(1, round(160 * image.height / image.width))))
    quantized = small.quantize(colors=12)
    counts = quantized.getcolors(maxcolors=160 * 160)
    if not counts:
        arr = np.asarray(small)
        return color_hex(arr.reshape(-1, 3).mean(axis=0))
    palette = quantized.getpalette()
    count, index = max(counts, key=lambda item: item[0])
    _ = count
    return color_hex(palette[index * 3 : index * 3 + 3])


def resize_for_analysis(image: "Image.Image", max_width: int) -> "Image.Image":
    if image.width <= max_width:
        return image
    height = max(1, round(image.height * max_width / image.width))
    return image.resize((max_width, height), Image.Resampling.BILINEAR)


def connected_components(mask: "np.ndarray", min_area: int) -> List[Dict[str, int]]:
    height, width = mask.shape
    seen = np.zeros(mask.shape, dtype=bool)
    components: List[Dict[str, int]] = []
    neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    ys, xs = np.where(mask & ~seen)
    for start_y, start_x in zip(ys.tolist(), xs.tolist()):
        if seen[start_y, start_x] or not mask[start_y, start_x]:
            continue
        stack = [(start_x, start_y)]
        seen[start_y, start_x] = True
        min_x = max_x = start_x
        min_y = max_y = start_y
        area = 0
        while stack:
            x, y = stack.pop()
            area += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for dx, dy in neighbors:
                nx = x + dx
                ny = y + dy
                if nx < 0 or ny < 0 or nx >= width or ny >= height:
                    continue
                if seen[ny, nx] or not mask[ny, nx]:
                    continue
                seen[ny, nx] = True
                stack.append((nx, ny))
        if area >= min_area:
            components.append({"x": min_x, "y": min_y, "w": max_x - min_x + 1, "h": max_y - min_y + 1, "area": area})
    components.sort(key=lambda c: (c["y"], c["x"]))
    return components


def normalized_bbox(component: Dict[str, int], width: int, height: int) -> List[float]:
    return [
        round(component["x"] / width, 5),
        round(component["y"] / height, 5),
        round(component["w"] / width, 5),
        round(component["h"] / height, 5),
    ]


def text_color_for_region(completed: "np.ndarray", mask: "np.ndarray", component: Dict[str, int]) -> str:
    x, y, w, h = component["x"], component["y"], component["w"], component["h"]
    region = completed[y : y + h, x : x + w, :]
    region_mask = mask[y : y + h, x : x + w]
    if region.size == 0 or not region_mask.any():
        return "#152033"
    pixels = region[region_mask]
    return color_hex(np.median(pixels, axis=0))


def blank_regions(background: "np.ndarray", text_mask: "np.ndarray", cols: int = 16, rows: int = 9) -> List[Dict[str, Any]]:
    height, width = text_mask.shape
    cell_ok = np.zeros((rows, cols), dtype=bool)
    cell_stats: Dict[Tuple[int, int], Tuple[float, float]] = {}
    gray = background.mean(axis=2)
    for row in range(rows):
        for col in range(cols):
            x0 = int(round(col * width / cols))
            x1 = int(round((col + 1) * width / cols))
            y0 = int(round(row * height / rows))
            y1 = int(round((row + 1) * height / rows))
            if x1 <= x0 or y1 <= y0:
                continue
            patch = gray[y0:y1, x0:x1]
            mask_patch = text_mask[y0:y1, x0:x1]
            text_ratio = float(mask_patch.mean()) if mask_patch.size else 1.0
            std = float(patch.std()) if patch.size else 999.0
            mean = float(patch.mean()) if patch.size else 0.0
            cell_stats[(row, col)] = (mean, std)
            if std < 18.0 and text_ratio < 0.03:
                cell_ok[row, col] = True

    comps = connected_components(cell_ok, min_area=1)
    regions = []
    for comp in comps:
        if comp["area"] < 2:
            continue
        x = int(round(comp["x"] * width / cols))
        y = int(round(comp["y"] * height / rows))
        w = int(round(comp["w"] * width / cols))
        h = int(round(comp["h"] * height / rows))
        stats = [
            cell_stats[(r, c)][1]
            for r in range(comp["y"], comp["y"] + comp["h"])
            for c in range(comp["x"], comp["x"] + comp["w"])
            if (r, c) in cell_stats
        ]
        regions.append(
            {
                "bbox": [round(x / width, 5), round(y / height, 5), round(w / width, 5), round(h / height, 5)],
                "cell_area": comp["area"],
                "mean_local_std": round(float(sum(stats) / len(stats)), 3) if stats else None,
            }
        )
    regions.sort(key=lambda r: (-r["bbox"][2] * r["bbox"][3], r["bbox"][1], r["bbox"][0]))
    return regions[:8]


def analyze_pair(completed_path: Path, background_path: Path, *, threshold: int, min_area: int, max_width: int) -> Dict[str, Any]:
    require_image_libs()
    completed_img = Image.open(completed_path).convert("RGB")
    background_img = Image.open(background_path).convert("RGB")
    if completed_img.size != background_img.size:
        background_img = background_img.resize(completed_img.size, Image.Resampling.BILINEAR)

    small_completed = resize_for_analysis(completed_img, max_width)
    small_background = resize_for_analysis(background_img, max_width)
    comp_arr = np.asarray(small_completed).astype(np.int16)
    bg_arr = np.asarray(small_background).astype(np.int16)
    diff = np.abs(comp_arr - bg_arr).mean(axis=2)
    mask = diff > threshold
    min_area_scaled = max(8, int(round(min_area * mask.shape[1] / completed_img.width)))
    comps = connected_components(mask, min_area=min_area_scaled)
    text_regions = []
    for comp in comps:
        text_regions.append(
            {
                "bbox": normalized_bbox(comp, mask.shape[1], mask.shape[0]),
                "area": comp["area"],
                "dominant_text_color": text_color_for_region(np.asarray(small_completed), mask, comp),
            }
        )
    return {
        "completed": str(completed_path),
        "background": str(background_path),
        "width": completed_img.width,
        "height": completed_img.height,
        "dominant_background_color": dominant_color(background_img),
        "diff_threshold": threshold,
        "text_regions": text_regions,
        "blank_regions": blank_regions(np.asarray(small_background), mask),
    }


def cmd_analyze(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    validate_completed_provenance(project, spec)
    validate_background_provenance(project, spec)
    results = []
    for i in range(1, int(spec["slide_count"]) + 1):
        completed = project / "completed" / f"slide_{i:02d}_completed.png"
        background = project / "background" / f"slide_{i:02d}_background.png"
        if not completed.exists() or not background.exists():
            die(f"Missing image pair for slide {i:02d}: {completed} / {background}")
        result = analyze_pair(completed, background, threshold=args.threshold, min_area=args.min_area, max_width=args.max_width)
        write_json(project / "analysis" / f"slide_{i:02d}.json", result)
        results.append({"slide": i, **result})
    manifest = {"created_at": now_iso(), "slides": results}
    write_json(project / "analysis/manifest.json", manifest)
    print(f"Wrote {project / 'analysis/manifest.json'}")


def hex_no_hash(value: str) -> str:
    raw = str(value or "#152033").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) != 6:
        return "152033"
    return raw.upper()


def emu(value: float) -> int:
    return int(round(value * EMU_PER_INCH))


def bbox_to_inches(bbox: Sequence[float], slide_w: float, slide_h: float) -> Tuple[float, float, float, float]:
    x, y, w, h = [float(v) for v in bbox]
    return x * slide_w, y * slide_h, w * slide_w, h * slide_h


def default_bbox(role: str, index: int) -> List[float]:
    if role == "title":
        return [0.07, 0.08, 0.72, 0.12]
    if role == "subtitle":
        return [0.07, 0.24, 0.56, 0.08]
    return [0.09, 0.34 + index * 0.13, 0.52, 0.22]


def choose_regions(analysis: Dict[str, Any], count: int) -> List[List[float]]:
    text_regions = [r["bbox"] for r in analysis.get("text_regions", [])]
    if len(text_regions) >= count:
        return text_regions[:count]
    blanks = [r["bbox"] for r in analysis.get("blank_regions", [])]
    chosen = text_regions[:]
    for blank in blanks:
        if len(chosen) >= count:
            break
        chosen.append(blank)
    return chosen


def text_box_xml(shape_id: int, text: str, bbox: Sequence[float], slide_w: float, slide_h: float, font_size: float, color: str, bold: bool, name: str, wrap: Optional[bool] = None) -> str:
    x, y, w, h = bbox_to_inches(bbox, slide_w, slide_h)
    size = max(600, int(round(font_size * 100)))
    bold_attr = ' b="1"' if bold else ""
    paragraphs = str(text).splitlines() or [""]
    should_wrap = bool(wrap) if wrap is not None else len(paragraphs) > 1
    wrap_attr = "square" if should_wrap else "none"
    runs = []
    for idx, para in enumerate(paragraphs):
        bullet = ""
        clean = para
        if para.lstrip().startswith(("-", "•")):
            clean = para.lstrip("-• ").strip()
            bullet = '<a:pPr marL="228600" indent="-114300"><a:buChar char="•"/></a:pPr>'
        ppr = bullet or '<a:pPr algn="l"/>'
        runs.append(
            f"<a:p>{ppr}<a:r><a:rPr lang=\"zh-CN\" sz=\"{size}\"{bold_attr}>"
            f"<a:solidFill><a:srgbClr val=\"{hex_no_hash(color)}\"/></a:solidFill>"
            "<a:latin typeface=\"Microsoft YaHei\"/><a:ea typeface=\"Microsoft YaHei\"/>"
            f"</a:rPr><a:t>{xml_escape(clean)}</a:t></a:r></a:p>"
        )
        if idx == 0 and len(paragraphs) == 1:
            continue
    body = "".join(runs)
    return f"""
      <p:sp>
        <p:nvSpPr><p:cNvPr id="{shape_id}" name="{xml_escape(name)}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr>
          <a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln>
        </p:spPr>
        <p:txBody><a:bodyPr wrap="{wrap_attr}" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>{body}</p:txBody>
      </p:sp>
"""


def slide_xml(background_rid: str, text_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="{XMLNS['a']}" xmlns:r="{XMLNS['r']}" xmlns:p="{XMLNS['p']}">
  <p:cSld>
    <p:bg><p:bgPr><a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="{background_rid}"/><a:srcRect/><a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      {text_xml}
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""


def relationship_xml(items: Sequence[Tuple[str, str, str]]) -> str:
    body = "\n".join(
        f'  <Relationship Id="{rid}" Type="{typ}" Target="{xml_escape(target)}"/>' for rid, typ, target in items
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{body}
</Relationships>
"""


def content_types_xml(slide_count: int, image_exts: Iterable[str]) -> str:
    defaults = [
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
    ]
    for ext in sorted(set(image_exts)):
        ctype = "image/jpeg" if ext in {"jpg", "jpeg"} else f"image/{ext}"
        defaults.append(f'<Default Extension="{ext}" ContentType="{ctype}"/>')
    overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for i in range(1, slide_count + 1):
        overrides.append(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n  '
        + "\n  ".join(defaults + overrides)
        + "\n</Types>\n"
    )


def presentation_xml(slide_count: int, slide_w: float, slide_h: float) -> str:
    ids = "\n".join(f'    <p:sldId id="{255 + i}" r:id="rId{1 + i}"/>' for i in range(1, slide_count + 1))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="{XMLNS['a']}" xmlns:r="{XMLNS['r']}" xmlns:p="{XMLNS['p']}">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst>
{ids}
  </p:sldIdLst>
  <p:sldSz cx="{emu(slide_w)}" cy="{emu(slide_h)}" type="custom"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>
"""


def minimal_theme() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{XMLNS['a']}" name="Image2Slides">
  <a:themeElements>
    <a:clrScheme name="Image2Slides">
      <a:dk1><a:srgbClr val="001334"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="152033"/></a:dk2><a:lt2><a:srgbClr val="F4F8FB"/></a:lt2>
      <a:accent1><a:srgbClr val="2F80ED"/></a:accent1><a:accent2><a:srgbClr val="1AAE8F"/></a:accent2>
      <a:accent3><a:srgbClr val="CDA349"/></a:accent3><a:accent4><a:srgbClr val="032A5C"/></a:accent4>
      <a:accent5><a:srgbClr val="68778A"/></a:accent5><a:accent6><a:srgbClr val="D6E1EA"/></a:accent6>
      <a:hlink><a:srgbClr val="2F80ED"/></a:hlink><a:folHlink><a:srgbClr val="1AAE8F"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="Image2Slides"><a:majorFont><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:majorFont><a:minorFont><a:latin typeface="Microsoft YaHei"/><a:ea typeface="Microsoft YaHei"/></a:minorFont></a:fontScheme>
    <a:fmtScheme name="Image2Slides"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
  </a:themeElements>
</a:theme>
"""


def slide_master_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="{XMLNS['a']}" xmlns:r="{XMLNS['r']}" xmlns:p="{XMLNS['p']}">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>
"""


def slide_layout_xml() -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="{XMLNS['a']}" xmlns:r="{XMLNS['r']}" xmlns:p="{XMLNS['p']}" type="blank" preserve="1">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>
"""


def doc_props_app(slide_count: int) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Image2Slides</Application><PresentationFormat>Custom</PresentationFormat><Slides>{slide_count}</Slides><Notes>0</Notes><HiddenSlides>0</HiddenSlides>
</Properties>
"""


def doc_props_core(title: str) -> str:
    timestamp = now_iso().replace("+00:00", "Z")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{xml_escape(title)}</dc:title><dc:creator>Image2Slides</dc:creator><cp:lastModifiedBy>Image2Slides</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>
</cp:coreProperties>
"""


def rel_type(name: str) -> str:
    return f"http://schemas.openxmlformats.org/officeDocument/2006/relationships/{name}"


def image_extension(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "jpg":
        return "jpeg"
    if ext not in {"png", "jpeg", "webp"}:
        return ext or "png"
    return ext


def build_pptx(project: Path, out_path: Path) -> None:
    spec = load_project(project)
    validate_completed_provenance(project, spec)
    validate_background_provenance(project, spec)
    plan = slide_plan(project)
    lint_report = lint_visible_text(project)
    write_lint_report(project, lint_report)
    if lint_report["issue_count"]:
        die(
            "Visible text contains control-plane/internal terms. "
            f"See {project / 'reports/internal_text_lint.md'}."
        )
    slide_w = float(spec["slide_size_inches"]["width"])
    slide_h = float(spec["slide_size_inches"]["height"])
    slides = plan["slides"]
    image_exts = []
    title = slides[0].get("title", "Image2Slides") if slides else "Image2Slides"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as pptx:
        pptx.writestr("[Content_Types].xml", content_types_xml(len(slides), ["png", "jpeg", "jpg", "webp"]))
        pptx.writestr("_rels/.rels", relationship_xml([
            ("rId1", rel_type("officeDocument"), "ppt/presentation.xml"),
            ("rId2", rel_type("metadata/core-properties"), "docProps/core.xml"),
            ("rId3", rel_type("extended-properties"), "docProps/app.xml"),
        ]))
        pres_rels = [("rId1", rel_type("slideMaster"), "slideMasters/slideMaster1.xml")]
        for i in range(1, len(slides) + 1):
            pres_rels.append((f"rId{1 + i}", rel_type("slide"), f"slides/slide{i}.xml"))
        pptx.writestr("ppt/_rels/presentation.xml.rels", relationship_xml(pres_rels))
        pptx.writestr("ppt/presentation.xml", presentation_xml(len(slides), slide_w, slide_h))
        pptx.writestr("ppt/theme/theme1.xml", minimal_theme())
        pptx.writestr("ppt/slideMasters/slideMaster1.xml", slide_master_xml())
        pptx.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", relationship_xml([
            ("rId1", rel_type("slideLayout"), "../slideLayouts/slideLayout1.xml"),
            ("rId2", rel_type("theme"), "../theme/theme1.xml"),
        ]))
        pptx.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout_xml())
        pptx.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", relationship_xml([
            ("rId1", rel_type("slideMaster"), "../slideMasters/slideMaster1.xml"),
        ]))
        pptx.writestr("docProps/app.xml", doc_props_app(len(slides)))
        pptx.writestr("docProps/core.xml", doc_props_core(title))

        for i, slide in enumerate(slides, start=1):
            bg_path = project / "background" / f"slide_{i:02d}_background.png"
            if not bg_path.exists():
                die(f"Missing background image: {bg_path}")
            ext = image_extension(bg_path)
            image_exts.append(ext)
            media_name = f"image{i}.{bg_path.suffix.lower().lstrip('.') or 'png'}"
            pptx.writestr(f"ppt/media/{media_name}", bg_path.read_bytes())

            analysis_path = project / "analysis" / f"slide_{i:02d}.json"
            analysis = read_json(analysis_path) if analysis_path.exists() else {"text_regions": [], "blank_regions": []}
            text_items = list(slide.get("text_items", []))
            regions = choose_regions(analysis, len(text_items))
            text_parts = []
            for idx, item in enumerate(text_items):
                role = str(item.get("role", "body"))
                bbox = item.get("bbox") or (regions[idx] if idx < len(regions) else default_bbox(role, idx))
                if len(bbox) != 4:
                    bbox = default_bbox(role, idx)
                color = item.get("color")
                if not color and idx < len(analysis.get("text_regions", [])):
                    color = analysis["text_regions"][idx].get("dominant_text_color")
                if not color:
                    color = "#ffffff" if role in {"title", "subtitle"} and i == 1 else "#152033"
                font_size = float(item.get("font_size") or (30 if role == "title" else 14))
                text_parts.append(
                    text_box_xml(
                        2 + idx,
                        str(item.get("text", "")),
                        bbox,
                        slide_w,
                        slide_h,
                        font_size,
                        str(color),
                        bool(item.get("bold", role == "title")),
                        f"{role}-{idx + 1}",
                        bool_option(item.get("wrap"), len(str(item.get("text", "")).splitlines()) > 1),
                    )
                )
            pptx.writestr(f"ppt/slides/slide{i}.xml", slide_xml("rId1", "".join(text_parts)))
            pptx.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", relationship_xml([
                ("rId1", rel_type("image"), f"../media/{media_name}"),
                ("rId2", rel_type("slideLayout"), "../slideLayouts/slideLayout1.xml"),
            ]))


def cmd_build_pptx(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    out = Path(args.out).resolve() if args.out else project / "pptx/image2slides.pptx"
    build_pptx(project, out)
    print(f"Wrote {out}")


def find_tool(name: str) -> Optional[str]:
    return shutil.which(name)


def cmd_render(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    pptx = Path(args.pptx).resolve() if args.pptx else project / "pptx/image2slides.pptx"
    if not pptx.exists():
        die(f"PPTX not found: {pptx}")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else project / "reports/rendered"
    out_dir.mkdir(parents=True, exist_ok=True)
    soffice = find_tool("soffice") or find_tool("libreoffice")
    pdftoppm = find_tool("pdftoppm")
    if not soffice:
        die("LibreOffice/soffice is required for render.")
    if not pdftoppm:
        die("pdftoppm is required for render.")
    for stale in [out_dir / (pptx.stem + ".pdf"), *out_dir.glob("slide-*.png"), *out_dir.glob("slide-*.ppm")]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)], check=True)
    pdf = out_dir / (pptx.stem + ".pdf")
    if not pdf.exists():
        matches = list(out_dir.glob("*.pdf"))
        if not matches:
            die("PDF export failed; no PDF found")
        pdf = matches[0]
    prefix = out_dir / "slide"
    subprocess.run([pdftoppm, "-png", "-r", str(args.dpi), str(pdf), str(prefix)], check=True)
    print(f"Rendered slides under {out_dir}")


def compare_images(a_path: Path, b_path: Path, max_width: int = 768) -> Dict[str, Any]:
    require_image_libs()
    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    if a.width > max_width:
        height = max(1, round(a.height * max_width / a.width))
        a = a.resize((max_width, height), Image.Resampling.BILINEAR)
        b = b.resize((max_width, height), Image.Resampling.BILINEAR)
    arr_a = np.asarray(a).astype(np.float32)
    arr_b = np.asarray(b).astype(np.float32)
    diff = np.abs(arr_a - arr_b)
    diff_luma = diff.mean(axis=2)
    mae = float(diff.mean())
    pixel_similarity = max(0.0, 1.0 - mae / 255.0)

    patch_scores = []
    for rows, cols in [(4, 8)]:
        for r in range(rows):
            for c in range(cols):
                y0 = int(round(r * arr_a.shape[0] / rows))
                y1 = int(round((r + 1) * arr_a.shape[0] / rows))
                x0 = int(round(c * arr_a.shape[1] / cols))
                x1 = int(round((c + 1) * arr_a.shape[1] / cols))
                pa = arr_a[y0:y1, x0:x1]
                pb = arr_b[y0:y1, x0:x1]
                if pa.size == 0:
                    continue
                patch_mae = float(np.abs(pa.mean(axis=(0, 1)) - pb.mean(axis=(0, 1))).mean())
                patch_scores.append(max(0.0, 1.0 - patch_mae / 255.0))
    detail_patch_mae: List[float] = []
    for rows, cols in [(12, 20)]:
        for r in range(rows):
            for c in range(cols):
                y0 = int(round(r * diff_luma.shape[0] / rows))
                y1 = int(round((r + 1) * diff_luma.shape[0] / rows))
                x0 = int(round(c * diff_luma.shape[1] / cols))
                x1 = int(round((c + 1) * diff_luma.shape[1] / cols))
                patch = diff_luma[y0:y1, x0:x1]
                if patch.size:
                    detail_patch_mae.append(float(patch.mean()))
    detail_patch_array = np.asarray(detail_patch_mae, dtype=np.float32) if detail_patch_mae else np.asarray([0.0])
    return {
        "pixel_similarity": round(pixel_similarity, 5),
        "patch_similarity": round(float(sum(patch_scores) / len(patch_scores)), 5) if patch_scores else None,
        "mae": round(mae, 3),
        "diff_p95": round(float(np.percentile(diff_luma, 95)), 3),
        "diff_p99": round(float(np.percentile(diff_luma, 99)), 3),
        "bad_pixel_ratio_32": round(float((diff_luma > 32).mean()), 5),
        "bad_pixel_ratio_64": round(float((diff_luma > 64).mean()), 5),
        "detail_patch_mae_p90": round(float(np.percentile(detail_patch_array, 90)), 3),
        "detail_patch_mae_max": round(float(detail_patch_array.max()), 3),
        "detail_bad_patch_ratio_32": round(float((detail_patch_array > 32).mean()), 5),
    }


def compare_image_arrays(arr_a: "np.ndarray", arr_b: "np.ndarray") -> Dict[str, Any]:
    diff = np.abs(arr_a.astype(np.float32) - arr_b.astype(np.float32))
    diff_luma = diff.mean(axis=2)
    mae = float(diff.mean())
    return {
        "pixel_similarity": round(max(0.0, 1.0 - mae / 255.0), 5),
        "mae": round(mae, 3),
        "bad_pixel_ratio_32": round(float((diff_luma > 32).mean()), 5),
        "bad_pixel_ratio_64": round(float((diff_luma > 64).mean()), 5),
    }


def compare_image_arrays_masked(
    arr_a: "np.ndarray",
    arr_b: "np.ndarray",
    mask: Optional["np.ndarray"] = None,
) -> Dict[str, Any]:
    if mask is None:
        return compare_image_arrays(arr_a, arr_b)
    selected = mask > 8
    if not selected.any():
        return {
            "pixel_similarity": 1.0,
            "mae": 0.0,
            "bad_pixel_ratio_32": 0.0,
            "bad_pixel_ratio_64": 0.0,
        }
    diff = np.abs(arr_a.astype(np.float32) - arr_b.astype(np.float32))
    diff_selected = diff[selected]
    diff_luma = diff_selected.mean(axis=1)
    mae = float(diff_selected.mean())
    return {
        "pixel_similarity": round(max(0.0, 1.0 - mae / 255.0), 5),
        "mae": round(mae, 3),
        "bad_pixel_ratio_32": round(float((diff_luma > 32).mean()), 5),
        "bad_pixel_ratio_64": round(float((diff_luma > 64).mean()), 5),
    }


def ink_mask_for_alignment(image: "Image.Image") -> "np.ndarray":
    arr = np.asarray(image.convert("RGB")).astype(np.int16)
    luminance = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114
    saturation = arr.max(axis=2) - arr.min(axis=2)
    return ((luminance < 210) & (saturation > 10)) | (luminance < 160)


def mask_bbox(mask: "np.ndarray") -> Optional[Tuple[int, int, int, int]]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def box_iou_px(a: Optional[Tuple[int, int, int, int]], b: Optional[Tuple[int, int, int, int]]) -> float:
    if a is None or b is None:
        return 0.0
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    return inter / max(1, area_a + area_b - inter)


def bbox_center_delta(
    a: Optional[Tuple[int, int, int, int]],
    b: Optional[Tuple[int, int, int, int]],
    width: int,
    height: int,
) -> Tuple[float, float]:
    if a is None or b is None:
        return 1.0, 1.0
    ax = (a[0] + a[2]) / 2.0
    ay = (a[1] + a[3]) / 2.0
    bx = (b[0] + b[2]) / 2.0
    by = (b[1] + b[3]) / 2.0
    return abs(ax - bx) / max(1, width), abs(ay - by) / max(1, height)


def text_crop_box(item: Dict[str, Any], width: int, height: int, pad: float = 0.012) -> Tuple[int, int, int, int]:
    bbox = item.get("bbox") or default_bbox(str(item.get("role", "body")), 0)
    x, y, w, h = [float(v) for v in bbox]
    return clamp_box(
        (
            int(round((x - pad) * width)),
            int(round((y - pad) * height)),
            int(round((x + w + pad) * width)),
            int(round((y + h + pad) * height)),
        ),
        width,
        height,
    )


def audit_text_alignment(project: Path, rendered_dir: Path) -> Dict[str, Any]:
    require_image_libs()
    spec = load_project(project)
    plan = slide_plan(project)
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for slide in plan.get("slides", []):
        slide_no = int(slide["slide"])
        completed_path = completed_image_path(project, slide_no)
        rendered_path = rendered_slide_path(rendered_dir, slide_no)
        if not completed_path.exists() or not rendered_path:
            continue
        completed = Image.open(completed_path).convert("RGB")
        rendered = Image.open(rendered_path).convert("RGB")
        if rendered.size != completed.size:
            rendered = rendered.resize(completed.size, Image.Resampling.BILINEAR)
        for idx, item in enumerate(slide.get("text_items", []), start=1):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            x0, y0, x1, y1 = text_crop_box(item, completed.width, completed.height)
            completed_crop = completed.crop((x0, y0, x1, y1))
            rendered_crop = rendered.crop((x0, y0, x1, y1))
            metrics = compare_image_arrays(np.asarray(completed_crop), np.asarray(rendered_crop))
            completed_ink = mask_bbox(ink_mask_for_alignment(completed_crop))
            rendered_ink = mask_bbox(ink_mask_for_alignment(rendered_crop))
            ink_iou = box_iou_px(completed_ink, rendered_ink)
            center_delta = bbox_center_delta(completed_ink, rendered_ink, max(1, x1 - x0), max(1, y1 - y0))
            row = {
                "slide": slide_no,
                "item": idx,
                "role": item.get("role", "text"),
                "text": text[:80],
                "crop_bbox": list(pixels_to_unit_rect((x0, y0, x1, y1), completed.width, completed.height)),
                **metrics,
                "ink_iou": round(float(ink_iou), 5),
                "center_delta": [round(float(center_delta[0]), 5), round(float(center_delta[1]), 5)],
            }
            rows.append(row)
            failures = []
            if float(metrics["pixel_similarity"]) < DEFAULT_TEXT_MIN_PIXEL_SIMILARITY:
                failures.append("text_pixel_similarity")
            if float(metrics["bad_pixel_ratio_32"]) > DEFAULT_TEXT_MAX_BAD_PIXEL_RATIO_32:
                failures.append("text_bad_pixel_ratio_32")
            if ink_iou < DEFAULT_TEXT_MIN_INK_IOU:
                failures.append("text_ink_iou")
            if max(center_delta) > DEFAULT_TEXT_MAX_CENTER_DELTA:
                failures.append("text_center_delta")
            for failure in failures:
                issues.append(
                    {
                        "slide": slide_no,
                        "item": idx,
                        "kind": failure,
                        "message": f"editable text render does not align with GPT-image-2 completed reference for role `{row['role']}`",
                    }
                )
    return {"created_at": now_iso(), "issue_count": len(issues), "issues": issues, "items": rows}


def write_text_alignment_audit(project: Path, report: Dict[str, Any]) -> None:
    write_json(project / "reports/text_alignment_audit.json", report)
    lines = [
        "# Text Alignment Audit",
        "",
        f"- Issues: {report['issue_count']}",
        "",
        "| Slide | Item | Kind | Message |",
        "| --- | --- | --- | --- |",
    ]
    for issue in report["issues"]:
        lines.append(f"| {issue['slide']} | {issue['item']} | {issue['kind']} | {issue['message']} |")
    (project / "reports/text_alignment_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_source_render_alignment(project: Path, rendered_dir: Path) -> Dict[str, Any]:
    require_image_libs()
    spec = load_project(project)
    plan = slide_plan(project)
    image_size = parse_image_size(spec["image_size"])
    rows: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for slide in plan.get("slides", []):
        slide_no = int(slide["slide"])
        rendered_path = rendered_slide_path(rendered_dir, slide_no)
        if not rendered_path:
            continue
        rendered = Image.open(rendered_path).convert("RGB")
        if rendered.size != image_size:
            rendered = rendered.resize(image_size, Image.Resampling.BILINEAR)
        native_panel_reference = load_native_panel_reference(project, slide_no, image_size)
        current_background = load_background_panel_reference(project, slide_no, image_size)
        panel_reference = native_panel_reference or load_background_panel_reference(project, slide_no, image_size)
        for idx, layer in enumerate(slide.get("source_layers", []), start=1):
            draw_frame = bool_option(layer.get("draw_frame"), False)
            geometry = source_fit_geometry(project, layer, image_size, draw_frame=draw_frame, panel_image=panel_reference)
            source_path = resolve_project_path(project, str(layer.get("path", "")))
            if not source_path.exists():
                continue
            paste_box = geometry["paste_box"]
            source, source_mask = source_image_and_mask_for_layer(source_path, layer, draw_frame=draw_frame)
            target_w = max(1, paste_box[2] - paste_box[0])
            target_h = max(1, paste_box[3] - paste_box[1])
            source = source.resize((target_w, target_h), Image.Resampling.LANCZOS)
            mask_arr = None
            if source_mask is not None:
                source_mask = source_mask.resize((target_w, target_h), Image.Resampling.LANCZOS)
                mask_arr = np.asarray(source_mask)
            crop = rendered.crop(paste_box)
            expected_crop = current_background.crop(paste_box) if current_background is not None else source
            metrics = compare_image_arrays(np.asarray(expected_crop), np.asarray(crop))
            row = {
                "slide": slide_no,
                "layer": idx,
                "path": str(layer.get("path", "")),
                "paste_bbox": list(pixels_to_unit_rect(paste_box, image_size[0], image_size[1])),
                **metrics,
            }
            blank_metrics = None
            if (
                mask_arr is not None
                and native_panel_reference is not None
                and current_background is not None
                and (mask_arr < 8).any()
            ):
                native_crop = native_panel_reference.crop(paste_box)
                current_crop = current_background.crop(paste_box)
                blank_selector = np.where(mask_arr < 8, 255, 0).astype("uint8")
                blank_metrics = compare_image_arrays_masked(np.asarray(native_crop), np.asarray(current_crop), blank_selector)
                row["blank_pixel_similarity"] = blank_metrics["pixel_similarity"]
                row["blank_bad_pixel_ratio_32"] = blank_metrics["bad_pixel_ratio_32"]
            rows.append(row)
            if float(metrics["pixel_similarity"]) < DEFAULT_SOURCE_MIN_RENDERED_SIMILARITY:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "source_render_similarity",
                        "message": "rendered source crop does not match the fitted source image",
                    }
                )
            if float(metrics["bad_pixel_ratio_32"]) > DEFAULT_SOURCE_MAX_RENDERED_BAD_PIXEL_RATIO_32:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "source_render_bad_pixel_ratio",
                        "message": "rendered source crop has too many pixels different from the fitted source image",
                    }
                )
            if blank_metrics is not None and float(blank_metrics["pixel_similarity"]) < DEFAULT_SOURCE_BLANK_MIN_RENDERED_SIMILARITY:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "source_blank_panel_occlusion",
                        "message": "source edge-blank area covers the generated background panel instead of staying transparent",
                    }
                )
            if blank_metrics is not None and float(blank_metrics["bad_pixel_ratio_32"]) > DEFAULT_SOURCE_BLANK_MAX_RENDERED_BAD_PIXEL_RATIO_32:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "source_blank_bad_pixel_ratio",
                        "message": "source transparent blank area differs from the source-free background panel",
                    }
                )
    return {"created_at": now_iso(), "issue_count": len(issues), "issues": issues, "layers": rows}


def write_source_render_audit(project: Path, report: Dict[str, Any]) -> None:
    write_json(project / "reports/source_render_audit.json", report)
    lines = [
        "# Source Render Alignment Audit",
        "",
        f"- Issues: {report['issue_count']}",
        "",
        "| Slide | Layer | Kind | Message |",
        "| --- | --- | --- | --- |",
    ]
    for issue in report["issues"]:
        lines.append(f"| {issue['slide']} | {issue['layer']} | {issue['kind']} | {issue['message']} |")
    (project / "reports/source_render_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_background_uniqueness(project: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    validate_background_provenance(project, spec)
    issues: List[Dict[str, Any]] = []
    hashes: Dict[str, int] = {}
    slide_count = int(spec["slide_count"])
    for slide_no in range(1, slide_count + 1):
        path = background_image_path(project, slide_no)
        digest = sha256_file(path)
        if digest in hashes:
            issues.append(
                {
                    "kind": "background_exact_duplicate",
                    "slide": slide_no,
                    "other_slide": hashes[digest],
                    "message": "background image is byte-identical to another slide",
                }
            )
        else:
            hashes[digest] = slide_no

    for slide_no in range(2, slide_count + 1):
        prev = background_image_path(project, slide_no - 1)
        current = background_image_path(project, slide_no)
        if not prev.exists() or not current.exists():
            continue
        metrics = compare_images(prev, current, max_width=384)
        if (
            float(metrics.get("pixel_similarity", 0.0)) >= 0.995
            and float(metrics.get("bad_pixel_ratio_32", 1.0)) <= 0.005
        ):
            issues.append(
                {
                    "kind": "background_near_duplicate",
                    "slide": slide_no,
                    "other_slide": slide_no - 1,
                    "pixel_similarity": metrics.get("pixel_similarity"),
                    "bad_pixel_ratio_32": metrics.get("bad_pixel_ratio_32"),
                    "message": "background is visually near-identical to the previous slide",
                }
            )
    return {"created_at": now_iso(), "issue_count": len(issues), "issues": issues}


def write_background_audit(project: Path, report: Dict[str, Any]) -> None:
    write_json(project / "reports/background_audit.json", report)
    lines = [
        "# Background Audit",
        "",
        f"- Issues: {report['issue_count']}",
        "",
        "| Slide | Other slide | Kind | Message |",
        "| --- | --- | --- | --- |",
    ]
    for issue in report["issues"]:
        lines.append(
            f"| {issue.get('slide', '')} | {issue.get('other_slide', '')} | {issue['kind']} | {issue['message']} |"
        )
    (project / "reports/background_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def rendered_slide_path(rendered_dir: Path, slide: int) -> Optional[Path]:
    candidates = [
        rendered_dir / f"slide-{slide}.png",
        rendered_dir / f"slide-{slide:02d}.png",
        rendered_dir / f"page-{slide}.png",
        rendered_dir / f"page-{slide:02d}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = sorted(rendered_dir.glob(f"*-{slide}.png")) + sorted(rendered_dir.glob(f"*-{slide:02d}.png"))
    return matches[0] if matches else None


def mask_to_l_image(mask: "np.ndarray") -> "Image.Image":
    return Image.fromarray((mask.astype("uint8") * 255), mode="L")


def box_count_mask(mask: "np.ndarray", radius: int) -> "np.ndarray":
    radius = max(0, int(radius))
    if radius <= 0:
        return mask.astype(np.uint8)
    height, width = mask.shape
    padded = np.pad(mask.astype(np.uint8), ((radius, radius), (radius, radius)), mode="constant")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    y0 = np.arange(height)
    y1 = y0 + radius * 2 + 1
    x0 = np.arange(width)
    x1 = x0 + radius * 2 + 1
    return integral[y1[:, None], x1[None, :]] - integral[y0[:, None], x1[None, :]] - integral[y1[:, None], x0[None, :]] + integral[y0[:, None], x0[None, :]]


def dilate_mask(mask: "np.ndarray", px: int) -> "np.ndarray":
    if px <= 0 or not mask.any():
        return mask
    return box_count_mask(mask, int(px)) > 0


def erode_mask(mask: "np.ndarray", px: int) -> "np.ndarray":
    if px <= 0 or not mask.any():
        return mask
    return box_count_mask(mask, int(px)) >= (int(px) * 2 + 1) ** 2


def open_mask(mask: "np.ndarray", px: int) -> "np.ndarray":
    return dilate_mask(erode_mask(mask, px), px)


def close_mask(mask: "np.ndarray", px: int) -> "np.ndarray":
    return erode_mask(dilate_mask(mask, px), px)


def dominant_blank_color_for_boundary(image: "Image.Image") -> Tuple[int, int, int]:
    sample = image.convert("RGB")
    if sample.width > 320:
        sample = sample.resize((320, max(1, round(sample.height * 320 / sample.width))), Image.Resampling.BILINEAR)
    arr = np.asarray(sample).astype(np.int16)
    luminance = arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114
    saturation = arr.max(axis=2) - arr.min(axis=2)
    selector = (luminance > 170) & (saturation < 80)
    pixels = arr[selector] if selector.any() else arr.reshape(-1, 3)
    if pixels.size == 0:
        return (255, 255, 255)
    quantized = (pixels // 8) * 8
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    color = colors[int(counts.argmax())] + 4
    return tuple(int(max(0, min(255, value))) for value in color.tolist())  # type: ignore[return-value]


def unit_xyxy_to_pixels(values: Sequence[float], width: int, height: int) -> Tuple[int, int, int, int]:
    if len(values) != 4:
        die(f"bbox must have four values: {values}")
    x0, y0, x1, y1 = [float(v) for v in values]
    return clamp_box((round(x0 * width), round(y0 * height), round(x1 * width), round(y1 * height)), width, height)


def add_unique_box(boxes: List[Tuple[int, int, int, int]], box: Tuple[int, int, int, int]) -> None:
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    for existing in boxes:
        if max(abs(a - b) for a, b in zip(existing, box)) <= 3:
            return
    boxes.append(box)


def source_panel_boxes_for_boundary(project: Path, slide: Dict[str, Any], width: int, height: int) -> List[Tuple[int, int, int, int]]:
    boxes: List[Tuple[int, int, int, int]] = []
    for boundary in slide.get("layout_boundaries", []):
        if boundary.get("kind") != "non_editable_image_panel":
            continue
        bbox = boundary.get("bbox")
        if bbox and len(bbox) == 4:
            add_unique_box(boxes, normalized_to_pixels(bbox, width, height))
    for layer in slide.get("source_layers", []):
        bbox = layer.get("panel_bbox") or layer.get("bbox")
        if bbox and len(bbox) == 4:
            add_unique_box(boxes, normalized_to_pixels(bbox, width, height))

    audit_path = project / "reports/source_layer_audit.json"
    if audit_path.exists():
        try:
            audit = read_json(audit_path)
        except SystemExit:
            audit = {}
        slide_no = int(slide.get("slide", 0))
        for row in audit.get("layers", []):
            if int(row.get("slide", -1)) != slide_no:
                continue
            bbox = row.get("visible_panel_bbox") or row.get("actual_panel_bbox") or row.get("panel_bbox")
            if bbox and len(bbox) == 4:
                add_unique_box(boxes, unit_xyxy_to_pixels(bbox, width, height))
    return boxes


def fill_boxes_mask(shape: Tuple[int, int], boxes: Sequence[Tuple[int, int, int, int]]) -> "np.ndarray":
    height, width = shape
    mask = np.zeros((height, width), dtype=bool)
    for box in boxes:
        x0, y0, x1, y1 = clamp_box(box, width, height)
        mask[y0:y1, x0:x1] = True
    return mask


def boundary_masks(
    background: "Image.Image",
    rendered: "Image.Image",
    source_panel_boxes: Sequence[Tuple[int, int, int, int]],
    *,
    blank_distance: float,
    text_diff_threshold: float,
    forbidden_dilate_px: int,
    safe_margin_ratio: float,
) -> Dict[str, Any]:
    width, height = background.size
    bg = np.asarray(background.convert("RGB")).astype(np.int16)
    rd = np.asarray(rendered.convert("RGB")).astype(np.int16)
    if rd.shape != bg.shape:
        rendered = rendered.resize(background.size, Image.Resampling.BILINEAR)
        rd = np.asarray(rendered.convert("RGB")).astype(np.int16)

    main_color = dominant_blank_color_for_boundary(background)
    color_arr = np.asarray(main_color, dtype=np.float32)
    color_distance = np.sqrt(((bg.astype(np.float32) - color_arr) ** 2).sum(axis=2))
    luminance = bg[:, :, 0] * 0.299 + bg[:, :, 1] * 0.587 + bg[:, :, 2] * 0.114
    saturation = bg.max(axis=2) - bg.min(axis=2)

    blank = (color_distance <= blank_distance) | ((luminance > 220) & (saturation < 90))
    blank = close_mask(open_mask(blank, 1), 2)
    safe = np.zeros((height, width), dtype=bool)
    margin = int(round(min(width, height) * safe_margin_ratio))
    safe[margin : height - margin, margin : width - margin] = True
    source_panel = fill_boxes_mask((height, width), source_panel_boxes)
    structural = (~blank) & ((saturation > 70) | (luminance < 230))
    structural = open_mask(structural, 1)
    forbidden = dilate_mask(structural, forbidden_dilate_px) | dilate_mask(source_panel, 6) | (~safe)
    fill = blank & safe & (~forbidden)
    diff = np.abs(rd - bg).mean(axis=2)
    text = dilate_mask(diff > text_diff_threshold, 1) & (~dilate_mask(source_panel, 3))
    return {
        "main_blank_color": color_hex(main_color),
        "blank": blank,
        "safe": safe,
        "source_panel": source_panel,
        "structural": structural,
        "forbidden": forbidden,
        "fill": fill,
        "text": text,
    }


def candidate_fill_rectangles(fill: "np.ndarray", forbidden: "np.ndarray", *, max_count: int = 6) -> List[Dict[str, Any]]:
    height, width = fill.shape
    cols = BOUNDARY_GRID_COLS
    rows = BOUNDARY_GRID_ROWS
    grid = np.zeros((rows, cols), dtype=bool)
    for row in range(rows):
        for col in range(cols):
            x0 = int(round(col * width / cols))
            x1 = int(round((col + 1) * width / cols))
            y0 = int(round(row * height / rows))
            y1 = int(round((row + 1) * height / rows))
            fill_patch = fill[y0:y1, x0:x1]
            forbidden_patch = forbidden[y0:y1, x0:x1]
            if fill_patch.size and float(fill_patch.mean()) >= 0.70 and float(forbidden_patch.mean()) <= 0.04:
                grid[row, col] = True
    components = connected_components(grid, min_area=1)
    rectangles = []
    for comp in components:
        if comp["area"] < 2:
            continue
        x0 = int(round(comp["x"] * width / cols))
        x1 = int(round((comp["x"] + comp["w"]) * width / cols))
        y0 = int(round(comp["y"] * height / rows))
        y1 = int(round((comp["y"] + comp["h"]) * height / rows))
        area = max(0, x1 - x0) * max(0, y1 - y0)
        rectangles.append(
            {
                "bbox": [round(x0 / width, 5), round(y0 / height, 5), round((x1 - x0) / width, 5), round((y1 - y0) / height, 5)],
                "cell_area": int(comp["area"]),
                "pixel_area": int(area),
            }
        )
    rectangles.sort(key=lambda item: (-int(item["pixel_area"]), item["bbox"][1], item["bbox"][0]))
    return rectangles[:max_count]


def boundary_clearance_summary(text: "np.ndarray", forbidden: "np.ndarray") -> Dict[str, Any]:
    text_pixels = int(text.sum())
    if text_pixels <= 0:
        return {"text_pixels": 0, "p10_px": None, "within_12px_ratio": 0.0}
    radii = [0, 4, 8, 12, 16, 24, 32, 48, 64]
    ratios = []
    p10 = None
    for radius in radii:
        expanded = forbidden if radius == 0 else dilate_mask(forbidden, radius)
        ratio = float((text & expanded).sum() / max(1, text_pixels))
        ratios.append({"radius_px": radius, "text_ratio": round(ratio, 5)})
        if p10 is None and ratio >= 0.10:
            p10 = radius
    return {
        "text_pixels": text_pixels,
        "p10_px": p10,
        "within_12px_ratio": next(item["text_ratio"] for item in ratios if item["radius_px"] == 12),
        "radius_curve": ratios,
    }


def write_boundary_overlay(
    background: "Image.Image",
    masks: Dict[str, Any],
    source_boxes: Sequence[Tuple[int, int, int, int]],
    candidates: Sequence[Dict[str, Any]],
    out_path: Path,
) -> None:
    base = np.asarray(background.convert("RGB")).astype(np.float32)
    overlay = base.copy()
    for mask, color, alpha in (
        (masks["fill"], np.asarray([33, 166, 93], dtype=np.float32), 0.18),
        (masks["forbidden"], np.asarray([236, 72, 72], dtype=np.float32), 0.22),
        (masks["text"], np.asarray([6, 182, 212], dtype=np.float32), 0.58),
    ):
        overlay[mask] = overlay[mask] * (1.0 - alpha) + color * alpha
    image = Image.fromarray(np.clip(overlay, 0, 255).astype("uint8"), mode="RGB")
    draw = ImageDraw.Draw(image)
    for box in source_boxes:
        draw.rectangle(box, outline="#ef4444", width=5)
    for candidate in candidates:
        x, y, w, h = candidate["bbox"]
        box = normalized_to_pixels([x, y, w, h], image.width, image.height)
        draw.rectangle(box, outline="#facc15", width=4)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def audit_content_boundaries(
    project: Path,
    rendered_dir: Path,
    *,
    out_dir: Optional[Path] = None,
    write_overlays: bool = True,
    blank_distance: float = DEFAULT_BOUNDARY_BLANK_DISTANCE,
    text_diff_threshold: float = DEFAULT_BOUNDARY_TEXT_DIFF_THRESHOLD,
    forbidden_dilate_px: int = DEFAULT_BOUNDARY_FORBIDDEN_DILATE_PX,
    safe_margin_ratio: float = DEFAULT_BOUNDARY_SAFE_MARGIN_RATIO,
    max_text_outside_fill: float = DEFAULT_BOUNDARY_MAX_TEXT_OUTSIDE_FILL,
    max_text_forbidden_overlap: float = DEFAULT_BOUNDARY_MAX_TEXT_FORBIDDEN_OVERLAP,
    min_clearance_p10_px: int = DEFAULT_BOUNDARY_MIN_CLEARANCE_P10_PX,
) -> Dict[str, Any]:
    require_image_libs()
    spec = load_project(project)
    plan = slide_plan(project)
    out_dir = out_dir or project / "reports"
    overlay_dir = out_dir / "content_boundary_overlays"
    slides: List[Dict[str, Any]] = []
    issues: List[Dict[str, Any]] = []
    for slide in plan.get("slides", []):
        slide_no = int(slide["slide"])
        background_path = background_image_path(project, slide_no)
        rendered_path = rendered_slide_path(rendered_dir, slide_no)
        if not background_path.exists() or not rendered_path:
            slides.append(
                {
                    "slide": slide_no,
                    "status": "missing",
                    "background": str(background_path),
                    "rendered": str(rendered_path) if rendered_path else None,
                }
            )
            issues.append(
                {
                    "slide": slide_no,
                    "kind": "missing_render_or_background",
                    "message": "content-boundary audit requires the text-free background and current rendered PPTX page",
                }
            )
            continue
        background = Image.open(background_path).convert("RGB")
        rendered = Image.open(rendered_path).convert("RGB")
        if rendered.size != background.size:
            rendered = rendered.resize(background.size, Image.Resampling.BILINEAR)
        source_boxes = source_panel_boxes_for_boundary(project, slide, background.width, background.height)
        masks = boundary_masks(
            background,
            rendered,
            source_boxes,
            blank_distance=blank_distance,
            text_diff_threshold=text_diff_threshold,
            forbidden_dilate_px=forbidden_dilate_px,
            safe_margin_ratio=safe_margin_ratio,
        )
        text = masks["text"]
        text_pixels = int(text.sum())
        outside_fill_ratio = float((text & (~masks["fill"])).sum() / max(1, text_pixels)) if text_pixels else 0.0
        forbidden_overlap_ratio = float((text & masks["forbidden"]).sum() / max(1, text_pixels)) if text_pixels else 0.0
        fill_ratio = float(masks["fill"].mean())
        forbidden_ratio = float(masks["forbidden"].mean())
        source_ratio = float(masks["source_panel"].mean())
        clearance = boundary_clearance_summary(text, masks["forbidden"])
        candidates = candidate_fill_rectangles(masks["fill"], masks["forbidden"])
        slide_issues = []
        if outside_fill_ratio > max_text_outside_fill:
            slide_issues.append("text_outside_fill_zone")
        if forbidden_overlap_ratio > max_text_forbidden_overlap:
            slide_issues.append("text_overlaps_forbidden_zone")
        p10 = clearance.get("p10_px")
        if text_pixels and p10 is not None and int(p10) < min_clearance_p10_px and forbidden_overlap_ratio > 0.01:
            slide_issues.append("text_too_close_to_forbidden_zone")
        if text_pixels and not candidates:
            slide_issues.append("no_candidate_text_fill_zone")
        for kind in slide_issues:
            issues.append(
                {
                    "slide": slide_no,
                    "kind": kind,
                    "message": "editable text should stay inside main blank-color fill zones and outside source panels/illustrations",
                }
            )
        overlay_path = overlay_dir / f"slide_{slide_no:02d}_boundary_overlay.png"
        if write_overlays:
            write_boundary_overlay(background, masks, source_boxes, candidates, overlay_path)
        slides.append(
            {
                "slide": slide_no,
                "status": "warn" if slide_issues else "ok",
                "main_blank_color": masks["main_blank_color"],
                "text_pixels": text_pixels,
                "text_outside_fill_zone_ratio": round(outside_fill_ratio, 5),
                "text_forbidden_overlap_ratio": round(forbidden_overlap_ratio, 5),
                "fill_zone_area_ratio": round(fill_ratio, 5),
                "forbidden_zone_area_ratio": round(forbidden_ratio, 5),
                "source_panel_area_ratio": round(source_ratio, 5),
                "text_clearance_to_forbidden_px": clearance,
                "source_panel_boxes": [list(pixels_to_unit_rect(box, background.width, background.height)) for box in source_boxes],
                "candidate_text_fill_rectangles": candidates,
                "overlay": project_relative(project, overlay_path) if write_overlays else None,
                "issues": slide_issues,
            }
        )
    text_slide_count = sum(1 for slide in slides if int(slide.get("text_pixels", 0)) > 0)
    mean_outside = sum(float(slide.get("text_outside_fill_zone_ratio", 0.0)) for slide in slides) / max(1, text_slide_count)
    mean_forbidden = sum(float(slide.get("text_forbidden_overlap_ratio", 0.0)) for slide in slides) / max(1, text_slide_count)
    return {
        "created_at": now_iso(),
        "rendered_dir": str(rendered_dir),
        "definition": {
            "blank_zone": "pixels close to the dominant/main blank color and inside the safe slide margin",
            "forbidden_zone": "source panels, illustration/structural regions, slide margins, and their dilated neighborhoods",
            "text_fill_zone": "blank_zone minus forbidden_zone; editable PowerPoint text should live here",
            "asset_internal_text_policy": "text printed inside a source image or illustration is preserved as image content and excluded from editable-text placement diagnostics",
        },
        "thresholds": {
            "blank_distance": blank_distance,
            "text_diff_threshold": text_diff_threshold,
            "forbidden_dilate_px": forbidden_dilate_px,
            "safe_margin_ratio": safe_margin_ratio,
            "max_text_outside_fill": max_text_outside_fill,
            "max_text_forbidden_overlap": max_text_forbidden_overlap,
            "min_clearance_p10_px": min_clearance_p10_px,
        },
        "issue_count": len(issues),
        "issues": issues,
        "aggregate": {
            "text_slide_count": text_slide_count,
            "mean_text_outside_fill_zone_ratio": round(mean_outside, 5),
            "mean_text_forbidden_overlap_ratio": round(mean_forbidden, 5),
            "warning_slides": [slide["slide"] for slide in slides if slide.get("status") == "warn"],
        },
        "slides": slides,
    }


def write_content_boundary_audit(out_dir: Path, report: Dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "content_boundary_audit.json", report)
    lines = [
        "# Content Boundary Audit",
        "",
        f"- Issues: {report['issue_count']}",
        f"- Mean text outside fill zone: {report['aggregate']['mean_text_outside_fill_zone_ratio']}",
        f"- Mean text forbidden overlap: {report['aggregate']['mean_text_forbidden_overlap_ratio']}",
        f"- Warning slides: {report['aggregate']['warning_slides']}",
        "",
        "## Definitions",
        "",
        "- `blank_zone`: main blank-color region detected from each text-free background.",
        "- `forbidden_zone`: source panels, structural illustration regions, slide margins, and dilated nearby pixels.",
        "- `text_fill_zone`: editable text target, defined as `blank_zone - forbidden_zone`.",
        "- Source-panel internal labels are image content and are excluded from editable-text diagnostics.",
        "",
        "| Slide | Status | Main blank | Outside fill | Forbidden overlap | Source panel area | Overlay |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for slide in report["slides"]:
        overlay = slide.get("overlay") or ""
        lines.append(
            f"| {slide['slide']} | {slide.get('status')} | {slide.get('main_blank_color', '')} | "
            f"{slide.get('text_outside_fill_zone_ratio', '')} | {slide.get('text_forbidden_overlap_ratio', '')} | "
            f"{slide.get('source_panel_area_ratio', '')} | `{overlay}` |"
        )
    lines.extend(["", "## Issues", "", "| Slide | Kind | Message |", "| --- | --- | --- |"])
    for issue in report["issues"]:
        lines.append(f"| {issue['slide']} | {issue['kind']} | {issue['message']} |")
    (out_dir / "content_boundary_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_audit_boundaries(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    rendered_dir = Path(args.rendered_dir).resolve() if args.rendered_dir else project / "reports/rendered"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else project / "reports"
    report = audit_content_boundaries(
        project,
        rendered_dir,
        out_dir=out_dir,
        write_overlays=not args.no_overlays,
        blank_distance=args.blank_distance,
        text_diff_threshold=args.text_diff_threshold,
        forbidden_dilate_px=args.forbidden_dilate_px,
        safe_margin_ratio=args.safe_margin_ratio,
        max_text_outside_fill=args.max_text_outside_fill,
        max_text_forbidden_overlap=args.max_text_forbidden_overlap,
        min_clearance_p10_px=args.min_clearance_p10_px,
    )
    write_content_boundary_audit(out_dir, report)
    print(f"Wrote {out_dir / 'content_boundary_audit.json'}")
    print(f"Wrote {out_dir / 'content_boundary_audit.md'}")
    if not args.no_overlays:
        print(f"Wrote overlays under {out_dir / 'content_boundary_overlays'}")
    if args.strict and report["issue_count"]:
        die(f"content boundary audit found {report['issue_count']} issue(s)")


def unit_rect(values: Sequence[float]) -> Tuple[float, float, float, float]:
    if len(values) != 4:
        die(f"bbox must have four values: {values}")
    x, y, w, h = [float(v) for v in values]
    return x, y, x + w, y + h


def rect_area(rect: Tuple[float, float, float, float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def rect_intersection(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def rect_inside(inner: Tuple[float, float, float, float], outer: Tuple[float, float, float, float], tolerance: float = 0.01) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def visible_text_length(slide: Dict[str, Any]) -> int:
    total = 0
    for item in slide.get("text_items", []):
        text = str(item.get("text", ""))
        total += sum(1 for ch in text if not ch.isspace())
    return total


def box_ratio(box: Tuple[int, int, int, int]) -> float:
    return max(1, box[2] - box[0]) / max(1, box[3] - box[1])


def relative_ratio_error(observed: float, expected: float) -> float:
    return abs(observed - expected) / max(expected, 0.0001)


def panel_inset_ratio_error(
    panel: Tuple[int, int, int, int],
    source_size: Tuple[int, int],
    margin_px: int,
) -> float:
    source_ratio = source_size[0] / max(1, source_size[1])
    return relative_ratio_error(box_ratio(inset_box(panel, margin_px)), source_ratio)


def source_layer_paste_rect(
    project: Path,
    layer: Dict[str, Any],
    image_size: Tuple[int, int],
    panel_image: Optional["Image.Image"] = None,
) -> Tuple[float, float, float, float]:
    width, height = image_size
    draw_frame = bool_option(layer.get("draw_frame"), False)
    geometry = source_fit_geometry(project, layer, image_size, draw_frame=draw_frame, panel_image=panel_image)
    return pixels_to_unit_rect(geometry["paste_box"], width, height)


def load_background_panel_reference(project: Path, slide_no: int, image_size: Tuple[int, int]) -> Optional["Image.Image"]:
    path = background_image_path(project, slide_no)
    if not path.exists() or Image is None:
        return None
    try:
        image = Image.open(path).convert("RGB")
        if image.size != image_size:
            image = image.resize(image_size, Image.Resampling.LANCZOS)
        return image
    except Exception:
        return None


def audit_source_layers(project: Path) -> Dict[str, Any]:
    require_image_libs()
    spec = load_project(project)
    plan = slide_plan(project)
    image_size = parse_image_size(spec["image_size"])
    issues: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    native_base_present = (project / "tmp/native_imagegen").exists()

    for slide in plan.get("slides", []):
        slide_no = int(slide["slide"])
        panel_reference = load_native_panel_reference(project, slide_no, image_size) or load_background_panel_reference(project, slide_no, image_size)
        detection_reference = (
            source_panel_detection_reference(slide, panel_reference, image_size)
            if panel_reference is not None
            else None
        )
        text_rects = []
        layer_rects_for_slide: List[Dict[str, Any]] = []
        text_area = 0.0
        for item in slide.get("text_items", []):
            bbox = item.get("bbox") or default_bbox(str(item.get("role", "body")), 0)
            text_rect = unit_rect(bbox)
            text_rects.append((str(item.get("role", "text")), text_rect))
            text_area += rect_area(text_rect)
        for idx, layer in enumerate(slide.get("source_layers", []), start=1):
            layer_rect = unit_rect(layer.get("bbox", []))
            draw_frame = bool_option(layer.get("draw_frame"), False)
            geometry = source_fit_geometry(project, layer, image_size, draw_frame=draw_frame, panel_image=detection_reference)
            width, height = image_size
            paste_rect = pixels_to_unit_rect(geometry["paste_box"], width, height)
            fit_rect = pixels_to_unit_rect(geometry["fit_region"], width, height)
            panel_rect = pixels_to_unit_rect(geometry["panel_box"], width, height)
            actual_panel_rect = pixels_to_unit_rect(geometry["actual_panel_box"], width, height)
            declared_panel_rect = pixels_to_unit_rect(geometry["declared_panel_box"], width, height)
            visible_panel_box = geometry["panel_box"] if draw_frame else geometry["actual_panel_box"]
            visible_panel_rect = pixels_to_unit_rect(visible_panel_box, width, height)
            visible_fit_box = inset_box(visible_panel_box, geometry["margin_px"])
            source_ratio = geometry["source_size"][0] / max(1, geometry["source_size"][1])
            visible_panel_ratio = box_ratio(visible_panel_box)
            visible_fit_ratio = box_ratio(visible_fit_box)
            panel_aspect_error = relative_ratio_error(visible_fit_ratio, source_ratio)
            detected_panel = geometry.get("detected_panel_box")
            rejected_detected_panel = geometry.get("rejected_detected_panel_box")
            detected_panel_rect = pixels_to_unit_rect(detected_panel, width, height) if detected_panel else None
            rejected_detected_panel_rect = (
                pixels_to_unit_rect(rejected_detected_panel, width, height) if rejected_detected_panel else None
            )
            row = {
                "slide": slide_no,
                "layer": idx,
                "path": layer.get("path"),
                "bbox": list(layer_rect),
                "paste_bbox": list(paste_rect),
                "fit_bbox": list(fit_rect),
                "panel_bbox": list(panel_rect),
                "visible_panel_bbox": list(visible_panel_rect),
                "actual_panel_bbox": list(actual_panel_rect),
                "declared_panel_bbox": list(declared_panel_rect),
                "detected_panel_bbox": list(detected_panel_rect) if detected_panel_rect else None,
                "rejected_detected_panel_bbox": list(rejected_detected_panel_rect) if rejected_detected_panel_rect else None,
                "draw_frame": draw_frame,
                "margin_px": geometry["margin_px"],
                "panel_aspect_from_source": geometry["panel_aspect_from_source"],
                "source_size_px": list(geometry["source_size"]),
                "source_aspect_ratio": round(float(source_ratio), 5),
                "visible_panel_aspect_ratio": round(float(visible_panel_ratio), 5),
                "visible_inset_aspect_ratio": round(float(visible_fit_ratio), 5),
                "panel_aspect_error": round(float(panel_aspect_error), 5),
                "slack_px": list(geometry["slack_px"]),
                "center_delta_px": [round(float(v), 3) for v in geometry["center_delta_px"]],
            }
            rows.append(row)
            layer_rects_for_slide.append(
                {
                    "layer": idx,
                    "visible_panel_rect": visible_panel_rect,
                    "paste_rect": paste_rect,
                }
            )

            if not rect_inside(layer_rect, (0.0, 0.0, 1.0, 1.0), tolerance=0.0):
                issues.append({"slide": slide_no, "layer": idx, "kind": "out_of_slide", "message": "source layer bbox is outside the slide"})
            if panel_reference is not None and not draw_frame and not panel_detection_enabled(layer):
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "panel_detection_disabled",
                        "message": "generated source panels must use detected panel edges; `detect_panel: false` bypasses the four-edge fitting algorithm",
                    }
                )
            if panel_reference is not None and not draw_frame and panel_detection_enabled(layer) and detected_panel is None:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "panel_detection_missing",
                        "message": "source layer uses a generated panel, but no source-free panel edge was detected; declared bbox is only a search hint",
                    }
                )
            if not rect_inside(paste_rect, actual_panel_rect, tolerance=0.002):
                issues.append({"slide": slide_no, "layer": idx, "kind": "outside_actual_panel", "message": "source image paste area is not fully inside the detected or declared panel"})
            if not rect_inside(paste_rect, panel_rect, tolerance=0.002):
                issues.append({"slide": slide_no, "layer": idx, "kind": "outside_aspect_panel", "message": "source image paste area is not fully inside the source-aspect panel"})
            if not rect_inside(paste_rect, fit_rect, tolerance=0.004):
                issues.append({"slide": slide_no, "layer": idx, "kind": "margin_violation", "message": "source image paste area is not inside the panel inset by margin d"})
            clearance = [
                paste_rect[0] - actual_panel_rect[0],
                paste_rect[1] - actual_panel_rect[1],
                actual_panel_rect[2] - paste_rect[2],
                actual_panel_rect[3] - paste_rect[3],
            ]
            required_clearance = [
                geometry["margin_px"] / width,
                geometry["margin_px"] / height,
                geometry["margin_px"] / width,
                geometry["margin_px"] / height,
            ]
            if any(observed + 0.001 < required for observed, required in zip(clearance, required_clearance)):
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "panel_margin_violation",
                        "message": "source image is not inset from all four detected panel edges by the configured margin d",
                    }
                )
            if max(abs(float(v)) for v in geometry["center_delta_px"]) > 1.0:
                issues.append({"slide": slide_no, "layer": idx, "kind": "center_misaligned", "message": "source image center is not aligned with the inset panel center"})
            if native_base_present and draw_frame:
                issues.append({"slide": slide_no, "layer": idx, "kind": "duplicate_frame_risk", "message": "native imagegen base is present but source layer requests an extra drawn frame"})
            aspect_tolerance = float(layer.get("panel_aspect_tolerance", DEFAULT_PANEL_ASPECT_TOLERANCE))
            if geometry["panel_aspect_from_source"] and panel_aspect_error > aspect_tolerance:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": idx,
                        "kind": "panel_aspect_mismatch",
                        "message": (
                            "visible/native panel inset ratio does not match trimmed source ratio "
                            f"({visible_fit_ratio:.3f}:1 vs {source_ratio:.3f}:1); "
                            "regenerate the GPT-image-2 source-free panel or update the plan before source-locking"
                        ),
                    }
                )

            for role, text_rect in text_rects:
                overlap = rect_intersection(paste_rect, text_rect)
                if overlap <= 0:
                    continue
                denom = max(0.0001, min(rect_area(paste_rect), rect_area(text_rect)))
                if overlap / denom > 0.01:
                    issues.append(
                        {
                            "slide": slide_no,
                            "layer": idx,
                            "kind": "text_overlap",
                            "role": role,
                            "message": f"source image paste area overlaps editable text role `{role}`",
                        }
                    )

        for left_index, left in enumerate(layer_rects_for_slide):
            for right in layer_rects_for_slide[left_index + 1 :]:
                panel_overlap = rect_intersection(left["visible_panel_rect"], right["visible_panel_rect"])
                if panel_overlap > 0:
                    denom = max(
                        0.0001,
                        min(rect_area(left["visible_panel_rect"]), rect_area(right["visible_panel_rect"])),
                    )
                    if panel_overlap / denom > 0.005:
                        issues.append(
                            {
                                "slide": slide_no,
                                "layer": left["layer"],
                                "kind": "source_panel_overlap",
                                "message": f"source panel overlaps layer {right['layer']}",
                            }
                        )
                paste_overlap = rect_intersection(left["paste_rect"], right["paste_rect"])
                if paste_overlap > 0:
                    denom = max(0.0001, min(rect_area(left["paste_rect"]), rect_area(right["paste_rect"])))
                    if paste_overlap / denom > 0.005:
                        issues.append(
                            {
                                "slide": slide_no,
                                "layer": left["layer"],
                                "kind": "source_image_overlap",
                                "message": f"source image paste area overlaps layer {right['layer']}",
                            }
                        )

        if layer_rects_for_slide and bool_option(slide.get("figure_first"), True):
            source_area = sum(rect_area(item["visible_panel_rect"]) for item in layer_rects_for_slide)
            text_chars = visible_text_length(slide)
            min_source_area = float(slide.get("figure_first_min_source_area", DEFAULT_FIGURE_FIRST_MIN_SOURCE_AREA))
            min_source_text_ratio = float(
                slide.get("figure_first_min_source_text_ratio", DEFAULT_FIGURE_FIRST_MIN_SOURCE_TEXT_RATIO)
            )
            max_text_chars = int(slide.get("figure_first_max_text_chars", DEFAULT_FIGURE_FIRST_MAX_TEXT_CHARS))
            if source_area < min_source_area:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": 0,
                        "kind": "figure_not_primary",
                        "message": (
                            "source figures are not the primary visual area "
                            f"({source_area:.3f} slide area; required >= {min_source_area:.3f})"
                        ),
                    }
                )
            if text_area > 0 and source_area / max(text_area, 0.0001) < min_source_text_ratio:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": 0,
                        "kind": "text_dominates_figure",
                        "message": (
                            "editable text layout area is too large for a figure-first slide "
                            f"(source/text area ratio {source_area / max(text_area, 0.0001):.2f}; "
                            f"required >= {min_source_text_ratio:.2f})"
                        ),
                    }
                )
            if text_chars > max_text_chars:
                issues.append(
                    {
                        "slide": slide_no,
                        "layer": 0,
                        "kind": "figure_slide_text_overload",
                        "message": (
                            "figure-first slide text is too heavy "
                            f"({text_chars} non-space chars; required <= {max_text_chars})"
                        ),
                    }
                )

    return {"created_at": now_iso(), "issue_count": len(issues), "issues": issues, "layers": rows}


def write_source_layer_audit(project: Path, report: Dict[str, Any]) -> None:
    write_json(project / "reports/source_layer_audit.json", report)
    lines = [
        "# Source Layer Layout Audit",
        "",
        f"- Issues: {report['issue_count']}",
        "",
        "| Slide | Layer | Kind | Message |",
        "| --- | --- | --- | --- |",
    ]
    for issue in report["issues"]:
        lines.append(f"| {issue['slide']} | {issue['layer']} | {issue['kind']} | {issue['message']} |")
    (project / "reports/source_layer_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_audit_layout(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    report = audit_source_layers(project)
    write_source_layer_audit(project, report)
    print(f"Wrote {project / 'reports/source_layer_audit.json'}")
    print(f"Wrote {project / 'reports/source_layer_audit.md'}")
    if args.strict and report["issue_count"]:
        die(f"source layer layout audit found {report['issue_count']} issue(s)")


def cmd_qa(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    validate_completed_provenance(project, spec)
    validate_background_provenance(project, spec)
    pptx_path = Path(args.pptx).resolve() if args.pptx else project / "pptx/image2slides.pptx"
    rendered_dir = Path(args.rendered_dir).resolve() if args.rendered_dir else project / "reports/rendered"
    if args.render:
        cmd_render(argparse.Namespace(project=str(project), pptx=str(pptx_path), out_dir=str(rendered_dir), dpi=args.dpi))
    rows = []
    for i in range(1, int(spec["slide_count"]) + 1):
        completed = project / "completed" / f"slide_{i:02d}_completed.png"
        rendered = rendered_slide_path(rendered_dir, i)
        if not completed.exists() or not rendered:
            rows.append({"slide": i, "status": "missing", "completed": str(completed), "rendered": str(rendered) if rendered else None})
            continue
        metrics = compare_images(completed, rendered)
        strict_failures = []
        if float(metrics.get("bad_pixel_ratio_32", 0.0)) > args.max_bad_pixel_ratio:
            strict_failures.append("bad_pixel_ratio_32")
        if float(metrics.get("detail_patch_mae_p90", 0.0)) > args.max_patch_p90_mae:
            strict_failures.append("detail_patch_mae_p90")
        if float(metrics.get("detail_bad_patch_ratio_32", 0.0)) > args.max_bad_patch_ratio:
            strict_failures.append("detail_bad_patch_ratio_32")
        rows.append(
            {
                "slide": i,
                "status": "strict_fail" if strict_failures else "ok",
                "strict_failures": strict_failures,
                "completed": str(completed),
                "rendered": str(rendered),
                **metrics,
            }
        )
    report = {
        "created_at": now_iso(),
        "pptx": str(pptx_path),
        "threshold": args.min_similarity,
        "slides": rows,
    }
    write_json(project / "reports/qa_similarity.json", report)
    layout_report = audit_source_layers(project)
    write_source_layer_audit(project, layout_report)
    text_report = audit_text_alignment(project, rendered_dir)
    write_text_alignment_audit(project, text_report)
    source_render_report = audit_source_render_alignment(project, rendered_dir)
    write_source_render_audit(project, source_render_report)
    background_report = audit_background_uniqueness(project, spec)
    write_background_audit(project, background_report)
    boundary_report = audit_content_boundaries(project, rendered_dir, out_dir=project / "reports")
    write_content_boundary_audit(project / "reports", boundary_report)
    failing = [
        r for r in rows
        if r.get("status") != "ok" or float(r.get("pixel_similarity", 0.0)) < args.min_similarity
    ]
    lines = [
        "# QA Report",
        "",
        f"- PPTX: `{pptx_path}`",
        f"- Similarity threshold: {args.min_similarity}",
        f"- Slides checked: {len(rows)}",
        f"- Failing or missing slides: {len(failing)}",
        f"- Source layer layout issues: {layout_report['issue_count']}",
        f"- Text alignment issues: {text_report['issue_count']}",
        f"- Source render issues: {source_render_report['issue_count']}",
        f"- Background issues: {background_report['issue_count']}",
        f"- Content boundary issues: {boundary_report['issue_count']}",
        "",
        f"- Max bad-pixel ratio (>32): {args.max_bad_pixel_ratio}",
        f"- Max detail patch p90 MAE: {args.max_patch_p90_mae}",
        f"- Max bad detail patch ratio (>32): {args.max_bad_patch_ratio}",
        "",
        "| Slide | Status | Pixel similarity | Bad px >32 | Patch p90 MAE | Strict failures |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['slide']} | {row.get('status')} | {row.get('pixel_similarity', '')} | "
            f"{row.get('bad_pixel_ratio_32', '')} | {row.get('detail_patch_mae_p90', '')} | "
            f"{', '.join(row.get('strict_failures', []))} |"
        )
    (project / "reports/qa_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {project / 'reports/qa_similarity.json'}")
    print(f"Wrote {project / 'reports/qa_report.md'}")
    print(f"Wrote {project / 'reports/source_layer_audit.json'}")
    print(f"Wrote {project / 'reports/source_layer_audit.md'}")
    print(f"Wrote {project / 'reports/text_alignment_audit.json'}")
    print(f"Wrote {project / 'reports/text_alignment_audit.md'}")
    print(f"Wrote {project / 'reports/source_render_audit.json'}")
    print(f"Wrote {project / 'reports/source_render_audit.md'}")
    print(f"Wrote {project / 'reports/background_audit.json'}")
    print(f"Wrote {project / 'reports/background_audit.md'}")
    print(f"Wrote {project / 'reports/content_boundary_audit.json'}")
    print(f"Wrote {project / 'reports/content_boundary_audit.md'}")
    print(f"Wrote overlays under {project / 'reports/content_boundary_overlays'}")
    if args.strict and failing:
        die(f"{len(failing)} slide(s) failed similarity threshold")
    if args.strict and layout_report["issue_count"]:
        die(f"source layer layout audit found {layout_report['issue_count']} issue(s)")
    if args.strict and text_report["issue_count"]:
        die(f"text alignment audit found {text_report['issue_count']} issue(s)")
    if args.strict and source_render_report["issue_count"]:
        die(f"source render alignment audit found {source_render_report['issue_count']} issue(s)")
    if args.strict and background_report["issue_count"]:
        die(f"background audit found {background_report['issue_count']} issue(s)")
    if args.boundary_strict and boundary_report["issue_count"]:
        die(f"content boundary audit found {boundary_report['issue_count']} issue(s)")


def cmd_doctor(args: argparse.Namespace) -> None:
    checks = {
        "native_imagegen_default": True,
        "pillow": Image is not None,
        "numpy": np is not None,
        "soffice": bool(find_tool("soffice") or find_tool("libreoffice")),
        "pdftoppm": bool(find_tool("pdftoppm")),
        "api_fallback_openai_sdk": importlib.util.find_spec("openai") is not None,
        "api_fallback_openai_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "api_fallback_imagegen_cli": default_imagegen_cli().exists(),
    }
    print(json.dumps(checks, indent=2, sort_keys=True))
    required = ("native_imagegen_default", "pillow", "numpy")
    if args.strict and not all(checks[key] for key in required):
        die("Doctor checks failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Image2Slides helper CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create a deck project wiki and directory structure")
    p.add_argument("--project", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("queue", help="Write GPT-image-2 prompt queues")
    p.add_argument("--project", required=True)
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("normalize-source-panels", help="Normalize source-layer panel bbox ratios to trimmed source image ratios")
    p.add_argument("--project", required=True)
    p.add_argument("--check", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_normalize_source_panels)

    p = sub.add_parser("lint-visible", help="Check that control-plane inputs do not appear as slide text")
    p.add_argument("--project", required=True)
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_lint_visible)

    p = sub.add_parser("compose-source-locked", help="Patch exact source figures into existing image_gen completed/background refs")
    p.add_argument("--project", required=True)
    p.add_argument("--base-dir", help=argparse.SUPPRESS)
    p.add_argument("--force", action="store_true", help="Allow re-patching an already source-locked image pair")
    p.set_defaults(func=cmd_compose_source_locked)

    p = sub.add_parser("audit-layout", help="Audit source layers for panel placement, duplicate frames, and text overlap")
    p.add_argument("--project", required=True)
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_audit_layout)

    p = sub.add_parser("audit-boundaries", help="Audit blank-zone, forbidden-zone, and editable text placement boundaries")
    p.add_argument("--project", required=True)
    p.add_argument("--rendered-dir")
    p.add_argument("--out-dir")
    p.add_argument("--blank-distance", type=float, default=DEFAULT_BOUNDARY_BLANK_DISTANCE)
    p.add_argument("--text-diff-threshold", type=float, default=DEFAULT_BOUNDARY_TEXT_DIFF_THRESHOLD)
    p.add_argument("--forbidden-dilate-px", type=int, default=DEFAULT_BOUNDARY_FORBIDDEN_DILATE_PX)
    p.add_argument("--safe-margin-ratio", type=float, default=DEFAULT_BOUNDARY_SAFE_MARGIN_RATIO)
    p.add_argument("--max-text-outside-fill", type=float, default=DEFAULT_BOUNDARY_MAX_TEXT_OUTSIDE_FILL)
    p.add_argument("--max-text-forbidden-overlap", type=float, default=DEFAULT_BOUNDARY_MAX_TEXT_FORBIDDEN_OVERLAP)
    p.add_argument("--min-clearance-p10-px", type=int, default=DEFAULT_BOUNDARY_MIN_CLEARANCE_P10_PX)
    p.add_argument("--no-overlays", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_audit_boundaries)

    p = sub.add_parser("imagegen", help="Run or preview GPT-image-2 imagegen calls")
    p.add_argument("--project", required=True)
    p.add_argument("--phase", choices=["completed", "background"], required=True)
    p.add_argument("--imagegen-cli")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(func=cmd_imagegen)

    p = sub.add_parser("register-completed", help="Record GPT-image-2 provenance for completed images copied from native image_gen")
    p.add_argument("--project", required=True)
    p.add_argument("--method", default="registered_native_image_gen", choices=sorted(COMPLETED_ALLOWED_METHODS))
    p.add_argument("--source", default="Codex native image_gen GPT-image-2 output")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_register_completed)

    p = sub.add_parser("register-background", help="Record GPT-image-2 edit provenance for background images copied from native image_gen")
    p.add_argument("--project", required=True)
    p.add_argument("--method", default="registered_native_image_gen_edit", choices=sorted(BACKGROUND_ALLOWED_METHODS))
    p.add_argument("--source", default="Codex native image_gen GPT-image-2 text-free background edit output")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_register_background)

    p = sub.add_parser("analyze", help="Analyze completed/background pairs")
    p.add_argument("--project", required=True)
    p.add_argument("--threshold", type=int, default=24)
    p.add_argument("--min-area", type=int, default=500)
    p.add_argument("--max-width", type=int, default=1280)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("build-pptx", help="Build editable PPTX from backgrounds and analysis")
    p.add_argument("--project", required=True)
    p.add_argument("--out")
    p.set_defaults(func=cmd_build_pptx)

    p = sub.add_parser("render", help="Render PPTX to PNG slides through LibreOffice and pdftoppm")
    p.add_argument("--project", required=True)
    p.add_argument("--pptx")
    p.add_argument("--out-dir")
    p.add_argument("--dpi", type=int, default=144)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("qa", help="Compare rendered PPTX pages with completed images")
    p.add_argument("--project", required=True)
    p.add_argument("--pptx")
    p.add_argument("--rendered-dir")
    p.add_argument("--render", action="store_true", default=True)
    p.add_argument("--no-render", action="store_false", dest="render")
    p.add_argument("--dpi", type=int, default=144)
    p.add_argument("--min-similarity", type=float, default=0.90)
    p.add_argument("--max-bad-pixel-ratio", type=float, default=0.25)
    p.add_argument("--max-patch-p90-mae", type=float, default=56.0)
    p.add_argument("--max-bad-patch-ratio", type=float, default=0.36)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--boundary-strict", action="store_true", help="Also fail when blank-zone/content-boundary diagnostics warn")
    p.set_defaults(func=cmd_qa)

    p = sub.add_parser("doctor", help="Check local tool availability")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
