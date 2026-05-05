from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import zipfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/image2slides/scripts"))
import image2slides


def write_spec(path: Path, slide_count: int = 2) -> None:
    path.write_text(
        json.dumps(
            {
                "style_tone": "deep navy, medical blue, clean academic",
                "aspect_ratio": "16:9",
                "slide_count": slide_count,
                "purpose": "speech",
                "scene": "academic",
                "knowledge_base": ["user notes", "reference image paths"],
            }
        ),
        encoding="utf-8",
    )


def run_cli(args: list[str]) -> None:
    assert image2slides.main(args) == 0


def make_pair(project: Path, slide: int, text: str = "TITLE") -> None:
    size = (640, 360)
    background = Image.new("RGB", size, "#dceaf7")
    draw = ImageDraw.Draw(background)
    draw.rounded_rectangle((380, 60, 590, 300), radius=18, fill="#2f80ed")
    draw.ellipse((420, 94, 540, 214), fill="#1aae8f")
    completed = background.copy()
    draw_completed = ImageDraw.Draw(completed)
    draw_completed.text((52, 70), text, fill="#001334")
    draw_completed.text((52, 130), "one\ntwo\nthree", fill="#152033")
    background.save(project / "background" / f"slide_{slide:02d}_background.png")
    completed.save(project / "completed" / f"slide_{slide:02d}_completed.png")


def register_completed(project: Path) -> None:
    run_cli(
        [
            "register-completed",
            "--project",
            str(project),
            "--method",
            "test_image_gen_fixture",
            "--source",
            "Codex native image_gen GPT-image-2 output",
        ]
    )


def register_background(project: Path) -> None:
    run_cli(
        [
            "register-background",
            "--project",
            str(project),
            "--method",
            "test_image_gen_edit_fixture",
            "--source",
            "Codex native image_gen GPT-image-2 text-free background edit output",
        ]
    )


class Image2SlidesTests(unittest.TestCase):
    def test_declares_required_python_dependencies(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        package_json = json.loads((root / "package.json").read_text(encoding="utf-8"))

        self.assertIn('"numpy>=1.24"', pyproject)
        self.assertIn('"Pillow>=10.0"', pyproject)
        self.assertIn("image2slides = \"image2slides:main\"", pyproject)
        self.assertIn("pyproject.toml", package_json["files"])
        self.assertIn("setup:python", package_json["scripts"])

    def test_doctor_fails_when_required_numpy_missing(self) -> None:
        original_np = image2slides.np
        try:
            image2slides.np = None
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    run_cli(["doctor"])
            self.assertEqual(raised.exception.code, 1)

            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(image2slides.main(["doctor", "--warn-only"]), 0)
            checks = json.loads(stdout.getvalue())
            self.assertFalse(checks["required_dependencies_ok"])
            self.assertIn("numpy", checks["missing_required_dependencies"])
        finally:
            image2slides.np = original_np

    def test_init_requires_all_fields(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "bad.json"
            spec.write_text(json.dumps({"style_tone": "blue"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                run_cli(["init", "--project", str(tmp / "deck"), "--spec", str(spec)])

    def test_init_and_queue(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=2)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            run_cli(["queue", "--project", str(project)])

            project_json = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(project_json["image_size"], "2048x1152")
            self.assertTrue((project / "wiki/02_content_boundary.md").exists())
            completed_jobs = (project / "prompts/completed_prompts.jsonl").read_text(encoding="utf-8").splitlines()
            background_jobs = (project / "prompts/background_edit_prompts.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(completed_jobs), 2)
            self.assertEqual(len(background_jobs), 2)
            self.assertEqual(json.loads(completed_jobs[0])["model"], "gpt-image-2")

    def test_analyze_and_build_pptx(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            make_pair(project, 1)
            register_completed(project)
            register_background(project)

            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0]["text_items"] = [
                {"role": "title", "text": "TITLE", "font_size": 30, "bold": True},
                {"role": "body", "text": "one\ntwo\nthree", "font_size": 14},
            ]
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            run_cli(["analyze", "--project", str(project), "--threshold", "18", "--min-area", "20"])
            analysis = json.loads((project / "analysis/slide_01.json").read_text(encoding="utf-8"))
            self.assertTrue(analysis["dominant_background_color"].startswith("#"))
            self.assertTrue(analysis["text_regions"])

            run_cli(["build-pptx", "--project", str(project)])
            pptx = project / "pptx/image2slides.pptx"
            self.assertTrue(pptx.exists())
            with zipfile.ZipFile(pptx) as archive:
                names = set(archive.namelist())
                self.assertIn("ppt/slides/slide1.xml", names)
                self.assertIn("ppt/slides/_rels/slide1.xml.rels", names)
                slide_xml = archive.read("ppt/slides/slide1.xml").decode("utf-8")
                self.assertIn("<p:bg>", slide_xml)
                self.assertIn("TITLE", slide_xml)
                self.assertIn("<a:noAutofit/>", slide_xml)
                self.assertNotIn("<a:spAutoFit/>", slide_xml)

    def test_build_blocks_control_plane_text(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            make_pair(project, 1, text="deep navy")
            register_completed(project)
            register_background(project)

            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0]["text_items"] = [
                {"role": "title", "text": "deep navy, medical blue, clean academic", "font_size": 24, "bold": True}
            ]
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            run_cli(["analyze", "--project", str(project), "--threshold", "18", "--min-area", "20"])
            with self.assertRaises(SystemExit):
                run_cli(["build-pptx", "--project", str(project)])
            lint = json.loads((project / "reports/internal_text_lint.json").read_text(encoding="utf-8"))
            self.assertGreater(lint["issue_count"], 0)

    def test_analyze_rejects_unregistered_completed_reference(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            make_pair(project, 1)

            with self.assertRaises(SystemExit):
                run_cli(["analyze", "--project", str(project), "--threshold", "18", "--min-area", "20"])

    def test_analyze_rejects_unregistered_background_edit(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            make_pair(project, 1)
            register_completed(project)

            with self.assertRaises(SystemExit):
                run_cli(["analyze", "--project", str(project), "--threshold", "18", "--min-area", "20"])

    def test_background_audit_blocks_duplicate_backgrounds(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=2)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            make_pair(project, 1)
            make_pair(project, 2)
            register_completed(project)
            register_background(project)

            report = image2slides.audit_background_uniqueness(project, image2slides.load_project(project))
            self.assertGreater(report["issue_count"], 0)
            self.assertIn("background_exact_duplicate", {issue["kind"] for issue in report["issues"]})

    def test_compose_source_locked_patches_existing_imagegen_pairs(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/source.png"
            source_image = Image.new("RGB", (160, 120), "#ffffff")
            draw_source = ImageDraw.Draw(source_image)
            draw_source.rectangle((40, 30, 120, 90), fill="#2f80ed")
            source_image.save(source)
            base_dir = project / "tmp/native_imagegen"
            base_dir.mkdir(parents=True)
            Image.new("RGB", (2048, 1152), "#eef6ff").save(base_dir / "slide_01_base.png")
            Image.new("RGB", (2048, 1152), "#eef6ff").save(project / "completed/slide_01_completed.png")
            Image.new("RGB", (2048, 1152), "#eef6ff").save(project / "background/slide_01_background.png")
            register_completed(project)
            register_background(project)
            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0].update(
                {
                    "figure_first": False,
                    "background_color": "#fbfcfe",
                    "source_layers": [
                        {
                            "path": "wiki/sources/source.png",
                            "bbox": [0.55, 0.2, 0.32, 0.42],
                            "panel_bbox": [0.52, 0.16, 0.4, 0.5],
                            "fit_margin_px": 32,
                            "draw_frame": False,
                        }
                    ],
                    "text_items": [
                        {"role": "title", "text": "Source-locked result", "bbox": [0.08, 0.13, 0.42, 0.12]},
                        {"role": "body", "text": "Exact figure remains a placed source layer.", "bbox": [0.08, 0.28, 0.42, 0.2]},
                    ],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            run_cli(["normalize-source-panels", "--project", str(project)])
            run_cli(["compose-source-locked", "--project", str(project), "--base-dir", str(base_dir)])
            with self.assertRaises(SystemExit):
                run_cli(["compose-source-locked", "--project", str(project), "--base-dir", str(base_dir)])
            run_cli(["audit-layout", "--project", str(project), "--strict"])
            self.assertTrue((project / "completed/slide_01_completed.png").exists())
            self.assertTrue((project / "background/slide_01_background.png").exists())
            background = Image.open(project / "background/slide_01_background.png")
            self.assertEqual(background.getpixel((1126, 231)), (238, 246, 255))
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            layer = audit["layers"][0]
            paste = layer["paste_bbox"]
            blank_x = int(round(paste[0] * 2048)) + 3
            blank_y = int(round(paste[1] * 1152)) + 3
            self.assertEqual(background.getpixel((blank_x, blank_y)), (238, 246, 255))
            self.assertLessEqual(max(abs(v) for v in layer["center_delta_px"]), 1.0)
            self.assertLess(layer["source_size_px"][0], 160)
            self.assertLess(layer["source_size_px"][1], 120)
            self.assertTrue(
                image2slides.rect_inside(tuple(layer["paste_bbox"]), tuple(layer["fit_bbox"]), tolerance=0.004)
            )

    def test_source_layer_uses_detected_native_panel_and_aspect_plan(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/source.png"
            Image.new("RGB", (400, 200), "#2f80ed").save(source)
            base_dir = project / "tmp/native_imagegen"
            base_dir.mkdir(parents=True)
            base = Image.new("RGB", (2048, 1152), "#fbfcfe")
            draw_base = ImageDraw.Draw(base)
            actual_panel = (1100, 140, 1900, 572)
            draw_base.rounded_rectangle(actual_panel, radius=24, fill="#ffffff", outline="#cddceb", width=3)
            base.save(base_dir / "slide_01_base.png")
            base.save(project / "completed/slide_01_completed.png")
            base.save(project / "background/slide_01_background.png")
            register_completed(project)
            register_background(project)

            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0].update(
                {
                    "figure_first": False,
                    "source_layers": [
                        {
                            "path": "wiki/sources/source.png",
                            "bbox": [0.48, 0.16, 0.35, 0.42],
                            "panel_bbox": [0.48, 0.12, 0.35, 0.48],
                            "fit_margin_px": 32,
                            "draw_frame": False,
                        }
                    ],
                    "text_items": [
                        {"role": "title", "text": "Source panel", "bbox": [0.08, 0.13, 0.30, 0.10]},
                    ],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            run_cli(["queue", "--project", str(project)])
            prompt = json.loads((project / "prompts/completed_prompts.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("Source-locked panel plan", prompt["prompt"])
            self.assertIn("source aspect 2.000:1", prompt["prompt"])

            run_cli(["compose-source-locked", "--project", str(project), "--base-dir", str(base_dir)])
            run_cli(["audit-layout", "--project", str(project), "--strict"])
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            layer = audit["layers"][0]
            self.assertIsNotNone(layer["detected_panel_bbox"])
            self.assertGreater(layer["actual_panel_bbox"][0], layer["declared_panel_bbox"][0])
            self.assertTrue(
                image2slides.rect_inside(tuple(layer["paste_bbox"]), tuple(layer["actual_panel_bbox"]), tolerance=0.002)
            )
            self.assertTrue(
                image2slides.rect_inside(tuple(layer["paste_bbox"]), tuple(layer["fit_bbox"]), tolerance=0.004)
            )
            self.assertLess(layer["slack_px"][0], 3)
            self.assertLess(layer["slack_px"][1], 3)

    def test_panel_detection_ignores_wrong_declared_hint(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/source.png"
            Image.new("RGB", (400, 200), "#2f80ed").save(source)
            base = Image.new("RGB", (2048, 1152), "#fbfcfe")
            draw_base = ImageDraw.Draw(base)
            actual_panel = (1050, 180, 1900, 560)
            draw_base.rounded_rectangle(actual_panel, radius=24, fill="#ffffff", outline="#cddceb", width=3)
            layer = {
                "path": "wiki/sources/source.png",
                "bbox": [800 / 2048, 180 / 1152, 800 / 2048, 380 / 1152],
                "panel_bbox": [800 / 2048, 180 / 1152, 800 / 2048, 380 / 1152],
                "fit_margin_px": 32,
                "draw_frame": False,
            }

            geometry = image2slides.source_fit_geometry(
                project,
                layer,
                (2048, 1152),
                draw_frame=False,
                panel_image=base,
            )

            detected = geometry["detected_panel_box"]
            self.assertIsNotNone(detected)
            self.assertGreater(detected[0], 1000)
            self.assertGreater(detected[2], 1850)
            self.assertLess(abs(detected[0] - actual_panel[0]), 12)
            self.assertTrue(
                image2slides.rect_inside(
                    image2slides.pixels_to_unit_rect(geometry["paste_box"], 2048, 1152),
                    image2slides.pixels_to_unit_rect(detected, 2048, 1152),
                    tolerance=0.002,
                )
            )

    def test_normalize_source_panels_blocks_mismatched_panel_aspect(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/wide.png"
            Image.new("RGB", (400, 200), "#2f80ed").save(source)
            Image.new("RGB", (2048, 1152), "#fbfcfe").save(project / "completed/slide_01_completed.png")
            Image.new("RGB", (2048, 1152), "#fbfcfe").save(project / "background/slide_01_background.png")
            register_completed(project)
            register_background(project)

            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0]["text_items"] = []
            plan["slides"][0]["figure_first"] = False
            plan["slides"][0]["source_layers"] = [
                {
                    "path": "wiki/sources/wide.png",
                    "bbox": [0.50, 0.15, 0.30, 0.45],
                    "panel_bbox": [0.50, 0.15, 0.30, 0.45],
                    "fit_margin_px": 32,
                    "draw_frame": False,
                }
            ]
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            with self.assertRaises(SystemExit):
                run_cli(["audit-layout", "--project", str(project), "--strict"])
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            self.assertIn("panel_aspect_mismatch", {issue["kind"] for issue in audit["issues"]})

            run_cli(["normalize-source-panels", "--project", str(project)])
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            panel = image2slides.normalized_to_pixels(plan["slides"][0]["source_layers"][0]["panel_bbox"], 2048, 1152)
            base = Image.new("RGB", (2048, 1152), "#fbfcfe")
            draw_base = ImageDraw.Draw(base)
            draw_base.rounded_rectangle(panel, radius=24, fill="#ffffff", outline="#cddceb", width=3)
            base.save(project / "completed/slide_01_completed.png")
            base.save(project / "background/slide_01_background.png")
            run_cli(["audit-layout", "--project", str(project), "--strict"])
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["issue_count"], 0)

    def test_source_panels_become_non_editable_content_boundaries(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/result.png"
            Image.new("RGB", (320, 180), "#ffffff").save(source)
            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0].update(
                {
                    "figure_first": False,
                    "source_layers": [
                        {
                            "path": "wiki/sources/result.png",
                            "bbox": [0.56, 0.20, 0.34, 0.46],
                            "panel_bbox": [0.56, 0.20, 0.34, 0.46],
                            "draw_frame": False,
                            "panel_aspect_from_source": False,
                        }
                    ],
                    "text_items": [
                        {"role": "title", "text": "Editable title", "bbox": [0.08, 0.20, 0.34, 0.12]},
                    ],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            run_cli(["queue", "--project", str(project)])
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            boundaries = plan["slides"][0]["layout_boundaries"]
            self.assertEqual(boundaries[0]["kind"], "non_editable_image_panel")
            self.assertFalse(boundaries[0]["editable_text_allowed"])
            self.assertIn("preserve_internal_text_as_image_content", plan["slides"][0]["illustration_text_policy"])

            background = Image.new("RGB", (640, 360), "#ffffff")
            draw_background = ImageDraw.Draw(background)
            panel = (358, 72, 576, 238)
            draw_background.rounded_rectangle(panel, radius=12, outline="#cddceb", width=3, fill="#ffffff")
            background.save(project / "background/slide_01_background.png")

            rendered = background.copy()
            draw_rendered = ImageDraw.Draw(rendered)
            draw_rendered.text((56, 84), "Editable title", fill="#102033")
            draw_rendered.text((420, 132), "asset label", fill="#102033")
            rendered_dir = project / "reports/rendered"
            rendered_dir.mkdir(parents=True)
            rendered.save(rendered_dir / "slide-1.png")

            run_cli(
                [
                    "audit-boundaries",
                    "--project",
                    str(project),
                    "--rendered-dir",
                    str(rendered_dir),
                    "--strict",
                ]
            )
            audit = json.loads((project / "reports/content_boundary_audit.json").read_text(encoding="utf-8"))
            slide = audit["slides"][0]
            self.assertEqual(audit["issue_count"], 0)
            self.assertGreater(slide["source_panel_area_ratio"], 0.10)
            self.assertLess(slide["text_forbidden_overlap_ratio"], 0.02)
            self.assertTrue((project / "reports/content_boundary_overlays/slide_01_boundary_overlay.png").exists())

    def test_layout_audit_blocks_source_overlap(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/source.png"
            Image.new("RGB", (160, 90), "#2f80ed").save(source)
            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0].update(
                {
                    "source_layers": [
                        {
                            "path": "wiki/sources/source.png",
                            "bbox": [0.08, 0.12, 0.30, 0.20],
                            "panel_bbox": [0.08, 0.12, 0.30, 0.20],
                            "draw_frame": False,
                        }
                    ],
                    "text_items": [
                        {"role": "title", "text": "Source-locked result", "bbox": [0.08, 0.13, 0.42, 0.12]},
                    ],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            with self.assertRaises(SystemExit):
                run_cli(["audit-layout", "--project", str(project), "--strict"])
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            kinds = {issue["kind"] for issue in audit["issues"]}
            self.assertIn("text_overlap", kinds)

    def test_layout_audit_blocks_source_panel_overlap(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source_a = project / "wiki/sources/source-a.png"
            source_b = project / "wiki/sources/source-b.png"
            Image.new("RGB", (240, 160), "#2f80ed").save(source_a)
            Image.new("RGB", (240, 160), "#1aae8f").save(source_b)
            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0]["text_items"] = []
            plan["slides"][0]["source_layers"] = [
                {
                    "path": "wiki/sources/source-a.png",
                    "bbox": [0.45, 0.20, 0.30, 0.30],
                    "panel_bbox": [0.45, 0.20, 0.30, 0.30],
                    "draw_frame": False,
                    "panel_aspect_from_source": False,
                },
                {
                    "path": "wiki/sources/source-b.png",
                    "bbox": [0.58, 0.34, 0.30, 0.30],
                    "panel_bbox": [0.58, 0.34, 0.30, 0.30],
                    "draw_frame": False,
                    "panel_aspect_from_source": False,
                },
            ]
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            with self.assertRaises(SystemExit):
                run_cli(["audit-layout", "--project", str(project), "--strict"])
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            self.assertIn("source_panel_overlap", {issue["kind"] for issue in audit["issues"]})

    def test_layout_audit_blocks_non_primary_figure_layout(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])

            source = project / "wiki/sources/result.png"
            Image.new("RGB", (320, 180), "#2f80ed").save(source)
            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0].update(
                {
                    "text_items": [
                        {"role": "title", "text": "Figure slide", "bbox": [0.06, 0.06, 0.80, 0.12]},
                        {
                            "role": "body",
                            "text": "This slide has too much supporting prose for a figure-first layout. " * 4,
                            "bbox": [0.06, 0.22, 0.52, 0.56],
                        },
                    ],
                    "source_layers": [
                        {
                            "path": "wiki/sources/result.png",
                            "bbox": [0.66, 0.30, 0.20, 0.18],
                            "panel_bbox": [0.66, 0.30, 0.20, 0.18],
                            "draw_frame": False,
                            "panel_aspect_from_source": False,
                        }
                    ],
                }
            )
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            with self.assertRaises(SystemExit):
                run_cli(["audit-layout", "--project", str(project), "--strict"])
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            kinds = {issue["kind"] for issue in audit["issues"]}
            self.assertIn("figure_not_primary", kinds)
            self.assertIn("text_dominates_figure", kinds)
            self.assertIn("figure_slide_text_overload", kinds)


class tempfile_dir:
    def __enter__(self) -> Path:
        import tempfile

        self._ctx = tempfile.TemporaryDirectory()
        return Path(self._ctx.__enter__())

    def __exit__(self, exc_type, exc, tb) -> bool:
        return self._ctx.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
