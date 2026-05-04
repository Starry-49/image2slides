from __future__ import annotations

import json
from pathlib import Path
import zipfile
import unittest

from PIL import Image, ImageDraw

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


class tempfile_dir:
    def __enter__(self) -> Path:
        import tempfile

        self._ctx = tempfile.TemporaryDirectory()
        return Path(self._ctx.__enter__())

    def __exit__(self, exc_type, exc, tb) -> bool:
        return self._ctx.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
