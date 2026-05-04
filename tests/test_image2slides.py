from __future__ import annotations

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


class Image2SlidesTests(unittest.TestCase):
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

    def test_build_blocks_control_plane_text(self) -> None:
        with tempfile_dir() as tmp:
            spec = tmp / "spec.json"
            project = tmp / "deck"
            write_spec(spec, slide_count=1)
            run_cli(["init", "--project", str(project), "--spec", str(spec)])
            make_pair(project, 1, text="deep navy")

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

    def test_compose_source_locked_creates_image_pairs(self) -> None:
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
            plan_path = project / "wiki/04_slide_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["slides"][0].update(
                {
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

            run_cli(["compose-source-locked", "--project", str(project), "--base-dir", str(base_dir)])
            run_cli(["audit-layout", "--project", str(project), "--strict"])
            self.assertTrue((project / "completed/slide_01_completed.png").exists())
            self.assertTrue((project / "background/slide_01_background.png").exists())
            background = Image.open(project / "background/slide_01_background.png")
            self.assertEqual(background.getpixel((1126, 231)), (238, 246, 255))
            audit = json.loads((project / "reports/source_layer_audit.json").read_text(encoding="utf-8"))
            layer = audit["layers"][0]
            self.assertLessEqual(max(abs(v) for v in layer["center_delta_px"]), 1.0)
            self.assertLess(layer["source_size_px"][0], 160)
            self.assertLess(layer["source_size_px"][1], 120)
            self.assertTrue(
                image2slides.rect_inside(tuple(layer["paste_bbox"]), tuple(layer["fit_bbox"]), tolerance=0.004)
            )

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


class tempfile_dir:
    def __enter__(self) -> Path:
        import tempfile

        self._ctx = tempfile.TemporaryDirectory()
        return Path(self._ctx.__enter__())

    def __exit__(self, exc_type, exc, tb) -> bool:
        return self._ctx.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
