"""Validate preparation artifacts; does not test the unimplemented product."""
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from jsonschema import Draft202012Validator


def main():
    root = Path(__file__).resolve().parents[1]
    required = ["README.md", "docs/PREPARATION_REPORT.md", "docs/PRODUCT_BRIEF.md",
                "docs/COMPETITOR_RESEARCH.md", "docs/RESEARCH_PAPERS.md",
                "docs/TECHNICAL_FEASIBILITY.md", "docs/MVP_SCOPE.md", "docs/ARCHITECTURE.md",
                "docs/DEVELOPMENT_PLAN.md", "docs/TEST_PLAN.md", "docs/DEMO_STORYBOARD.md",
                "docs/SUBMISSION_CHECKLIST.md", "docs/DESIGN_GUIDE.md", "design/cover.svg",
                "design/ui-wireframe.html", "design/architecture.mmd", "design/cover-preview.png",
                "design/ui-preview.png", "schemas/memory.schema.json", "examples/design_fixture.json",
                "evaluation/results_template.csv", "evidence/environment.json", "evidence/video_probe.json"]
    missing = [name for name in required if not (root / name).is_file() or not (root / name).stat().st_size]
    assert not missing, missing
    broken = []
    for md in root.rglob("*.md"):
        if set(md.relative_to(root).parts) & {"node_modules", ".git", "data", ".venv", "dist"}:
            continue
        for link in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", md.read_text(encoding="utf-8")):
            if link.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = link.split("#", 1)[0].strip("<>")
            if target and not (md.parent / target).exists():
                broken.append({"file": str(md.relative_to(root)), "link": target})
    assert not broken, broken
    schema = json.loads((root / "schemas/memory.schema.json").read_text(encoding="utf-8"))
    fixture = json.loads((root / "examples/design_fixture.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(fixture)
    rejected = []
    for case in ["observed_without_evidence", "inference_without_source", "unknown_without_reason", "model_analysis_without_provenance"]:
        invalid = copy.deepcopy(fixture)
        event = invalid["events"][0]
        if case == "observed_without_evidence":
            event["evidence_ids"] = []
        elif case == "inference_without_source":
            event["epistemic_type"], event["rule_id"] = "inferred", "example_rule"
        elif case == "unknown_without_reason":
            event["epistemic_type"], event["uncertainty_reasons"] = "unknown", []
        else:
            invalid["fixture_kind"] = "model_analysis"
        assert list(validator.iter_errors(invalid)), case
        rejected.append(case)
    with (root / "evaluation/results_template.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert len(rows) == 15 and all(len(row) == len(rows[0]) == 22 for row in rows)
    status_index = rows[0].index("status")
    assert all(row[status_index] == "NOT_RUN" for row in rows[1:])
    ET.parse(root / "design/cover.svg")
    pdf = root / "天猫AI黑客松高校挑战赛-活动须知.pdf"
    pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest().upper()
    assert pdf_hash == "A332DCD589CECA938011431EFF62750F6A36FC8CEF4896D01C3D150801AEE6C1"
    intro_doc = (root / "docs/PRODUCT_BRIEF.md").read_text(encoding="utf-8")
    intro = re.search(r"## 赛事作品介绍初稿[^\n]*\n\n([^\n]+)", intro_doc).group(1)
    assert 150 <= len(intro) <= 250
    result = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Preparation artifacts only; no product or model inference test",
        "required_artifacts": len(required), "missing_artifacts": missing,
        "broken_local_markdown_links": broken,
        "schema_draft": "2020-12", "manual_fixture_valid": True,
        "structurally_invalid_cases_rejected": rejected,
        "cross_record_semantic_validation": "NOT_ASSESSED_BY_THIS_SCRIPT; implemented in backend/events.py and tested separately",
        "test_template_rows": len(rows) - 1, "real_video_evaluation_template": "NOT_RUN; automated software tests are recorded separately",
        "introduction_characters_including_punctuation": len(intro),
        "cover_svg_xml_valid": True, "original_pdf_sha256": pdf_hash,
        "original_pdf_unchanged": True,
        "visual_review": "NOT_ASSESSED_BY_THIS_SCRIPT; manual review is documented in docs/PREPARATION_REPORT.md"
    }
    out = root / "evidence/preparation_checks.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
