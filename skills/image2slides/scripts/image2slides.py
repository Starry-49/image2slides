#!/usr/bin/env python3
"""Deterministic helpers for the Image2Slides Codex plugin.

The agent owns the creative and factual judgment. This CLI owns repeatable file
layout, GPT-image-2 queue construction, image pair analysis, PPTX assembly, and
similarity reports.
"""

from __future__ import annotations

import argparse
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
    from PIL import Image
except Exception:  # pragma: no cover - exercised by users without Pillow.
    Image = None  # type: ignore

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

XMLNS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def require_image_libs() -> None:
    if Image is None:
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


def base_prompt(spec: Dict[str, Any]) -> str:
    return (
        f"Use case: productivity-visual. Asset type: full-slide {spec['aspect_ratio_normalized']} "
        f"presentation composition. Style and tone: {spec['style_tone']}. Purpose: {spec['purpose']}. "
        f"Scene: {spec['scene']}. Use the supplied wiki as the knowledge boundary. "
        "Respect source boundaries: factual claims must come from grep_required material; generated content "
        "may only provide visual metaphor, pacing, and non-factual scaffolding."
    )


def completed_prompt(spec: Dict[str, Any], slide: Dict[str, Any]) -> str:
    texts = []
    for item in slide.get("text_items", []):
        text = str(item.get("text", "")).strip()
        if text:
            texts.append(text)
    text_block = "\n".join(texts)
    return (
        base_prompt(spec)
        + f"\nSlide {slide['slide']:02d}: {slide.get('title', '')}\n"
        + f"Layout: {slide.get('layout', 'content')}\n"
        + f"Visual intent: {slide.get('visual_intent', '')}\n"
        + f"Source boundary: {slide.get('source_boundary', 'mixed')}\n"
        + "Create a polished complete slide reference image with the following exact visible text.\n"
        + f"Text (verbatim):\n{text_block}\n"
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


def cmd_queue(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    slides = slide_plan(project)["slides"]
    completed_path = project / "prompts/completed_prompts.jsonl"
    background_path = project / "prompts/background_edit_prompts.jsonl"
    with completed_path.open("w", encoding="utf-8") as handle:
        for slide in slides:
            idx = int(slide["slide"])
            job = {
                "prompt": completed_prompt(spec, slide),
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


def default_imagegen_cli() -> Path:
    if os.getenv("IMAGE_GEN"):
        return Path(os.environ["IMAGE_GEN"]).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    return codex_home / "skills/.system/imagegen/scripts/image_gen.py"


def run_command(command: Sequence[str], *, dry_run: bool) -> None:
    if dry_run:
        print(json.dumps({"command": list(command)}, ensure_ascii=False))
        return
    subprocess.run(command, check=True)


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
        return

    jobs = [
        json.loads(line)
        for line in (project / "prompts/background_edit_prompts.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
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


def text_box_xml(shape_id: int, text: str, bbox: Sequence[float], slide_w: float, slide_h: float, font_size: float, color: str, bold: bool, name: str) -> str:
    x, y, w, h = bbox_to_inches(bbox, slide_w, slide_h)
    size = max(600, int(round(font_size * 100)))
    bold_attr = ' b="1"' if bold else ""
    paragraphs = str(text).splitlines() or [""]
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
        <p:txBody><a:bodyPr wrap="square" lIns="0" tIns="0" rIns="0" bIns="0" anchor="mid"><a:spAutoFit/></a:bodyPr><a:lstStyle/>{body}</p:txBody>
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
    plan = slide_plan(project)
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
    mae = float(np.abs(arr_a - arr_b).mean())
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
    return {
        "pixel_similarity": round(pixel_similarity, 5),
        "patch_similarity": round(float(sum(patch_scores) / len(patch_scores)), 5) if patch_scores else None,
        "mae": round(mae, 3),
    }


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


def cmd_qa(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    spec = load_project(project)
    pptx_path = Path(args.pptx).resolve() if args.pptx else project / "pptx/image2slides.pptx"
    rendered_dir = Path(args.rendered_dir).resolve() if args.rendered_dir else project / "reports/rendered"
    if args.render and not rendered_dir.exists():
        cmd_render(argparse.Namespace(project=str(project), pptx=str(pptx_path), out_dir=str(rendered_dir), dpi=args.dpi))
    rows = []
    for i in range(1, int(spec["slide_count"]) + 1):
        completed = project / "completed" / f"slide_{i:02d}_completed.png"
        rendered = rendered_slide_path(rendered_dir, i)
        if not completed.exists() or not rendered:
            rows.append({"slide": i, "status": "missing", "completed": str(completed), "rendered": str(rendered) if rendered else None})
            continue
        metrics = compare_images(completed, rendered)
        rows.append({"slide": i, "status": "ok", "completed": str(completed), "rendered": str(rendered), **metrics})
    report = {
        "created_at": now_iso(),
        "pptx": str(pptx_path),
        "threshold": args.min_similarity,
        "slides": rows,
    }
    write_json(project / "reports/qa_similarity.json", report)
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
        "",
        "| Slide | Status | Pixel similarity | Patch similarity |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['slide']} | {row.get('status')} | {row.get('pixel_similarity', '')} | {row.get('patch_similarity', '')} |"
        )
    (project / "reports/qa_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {project / 'reports/qa_similarity.json'}")
    print(f"Wrote {project / 'reports/qa_report.md'}")
    if args.strict and failing:
        die(f"{len(failing)} slide(s) failed similarity threshold")


def cmd_doctor(args: argparse.Namespace) -> None:
    checks = {
        "pillow": Image is not None,
        "numpy": np is not None,
        "openai_sdk": importlib.util.find_spec("openai") is not None,
        "soffice": bool(find_tool("soffice") or find_tool("libreoffice")),
        "pdftoppm": bool(find_tool("pdftoppm")),
        "openai_api_key": bool(os.getenv("OPENAI_API_KEY")),
        "imagegen_cli": default_imagegen_cli().exists(),
    }
    print(json.dumps(checks, indent=2, sort_keys=True))
    if args.strict and not all(checks.values()):
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

    p = sub.add_parser("imagegen", help="Run or preview GPT-image-2 imagegen calls")
    p.add_argument("--project", required=True)
    p.add_argument("--phase", choices=["completed", "background"], required=True)
    p.add_argument("--imagegen-cli")
    p.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.set_defaults(func=cmd_imagegen)

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
    p.add_argument("--min-similarity", type=float, default=0.82)
    p.add_argument("--strict", action="store_true")
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
