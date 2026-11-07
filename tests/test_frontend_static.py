"""Exercise the shipped website through the production Python entry point."""
import json
import re

import pytest

from backend.config import ROOT


@pytest.mark.parametrize("route", ["/", "/workspace"])
def test_built_page_and_bundles_are_available(client, route):
    response = client.get(route)
    assert response.status_code == 200
    bundles = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', response.text)
    assert bundles
    for bundle in bundles:
        assert client.get(bundle).status_code == 200, bundle


def test_public_demo_manifest_and_evidence_images_are_available(client):
    demo = ROOT / "frontend/dist/demo"
    response = client.get("/demo/demo-public-fixture.json")
    assert response.status_code == 200
    assert response.json() == json.loads((demo / "demo-public-fixture.json").read_text(encoding="utf-8"))
    images = sorted([*demo.glob("posters/*.jpg"), *demo.glob("keyframes/*.jpg")])
    referenced = {case['posterSrc'].split('?')[0] for case in response.json()['cases']}
    for case in response.json()['cases']:
        referenced.update(frame['image'].split('?')[0]
                          for frame in (case.get('event') or {}).get('referencedFrames', []))
    assert referenced
    assert referenced <= {'/demo/' + image.relative_to(demo).as_posix() for image in images}
    for image in images:
        response = client.get("/demo/" + image.relative_to(demo).as_posix())
        assert response.status_code == 200, image.name
        assert response.headers["content-type"].startswith("image/jpeg")
        assert response.content == image.read_bytes()


@pytest.mark.parametrize("name", ["fall", "help", "fire"])
def test_public_demo_videos_support_range_requests(client, name):
    video = ROOT / f"frontend/dist/demo/videos/{name}.mp4"
    response = client.get(f"/demo/videos/{name}.mp4", headers={"Range": "bytes=0-127"})
    assert response.status_code == 206
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["content-range"] == f"bytes 0-127/{video.stat().st_size}"
    with video.open("rb") as stream:
        assert response.content == stream.read(128)


def test_public_demo_mount_does_not_expose_repository_files(client):
    for route in ["/.env", "/README.md", "/data", "/demo/..%2F..%2FREADME.md"]:
        assert client.get(route).status_code == 404, route
