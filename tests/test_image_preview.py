from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import random
import threading
import time
import asyncio

import httpx
import pytest

from PIL import Image
from app import image_preview as previews

from test_platform import client


def picture(size=(2400, 1800)):
    data = BytesIO()
    image = Image.new("RGB", size, (24, 115, 102))
    exif = Image.Exif()
    exif[270] = "synthetic private metadata"
    image.save(data, "JPEG", exif=exif)
    return data.getvalue()


def test_preview_requires_auth_reduces_size_strips_metadata_and_preserves_original(client):
    original = picture()
    item = client.post("/api/v1/files", files={"file": ("synthetic.jpg", original, "image/jpeg")}).json()
    path = f"/api/v1/files/{item['id']}/preview"
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    response = client.get(path)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/webp'
    assert response.headers['cache-control'] == 'no-store'
    assert len(response.content) < len(original)
    with Image.open(BytesIO(response.content)) as preview:
        assert max(preview.size) <= 1024 and not preview.getexif()
    assert len(response.content) <= 64 * 1024
    assert client.get(f"/api/v1/files/{item['id']}/download?inline=true").content == response.content
    assert client.get(f"/api/v1/files/{item['id']}/download?inline=true", headers={"Authorization": ""}).status_code == 401
    assert client.get(f"/api/v1/files/{item['id']}/download").content == original
    # A cached preview must not bypass a missing/deleted or relocated original.
    client.app.state.library.path(item['id']).unlink()
    assert client.get(path).status_code == 400


def test_corrupt_and_non_image_previews_report_errors_without_affecting_download(client):
    for filename, data, mime in [("bad.png", b"invalid image", "image/png"), ("plain.txt", b"text", "text/plain")]:
        item = client.post("/api/v1/files", files={"file": (filename, data, mime)}).json()
        response = client.get(f"/api/v1/files/{item['id']}/preview")
        assert response.status_code == 400
        assert client.get(f"/api/v1/files/{item['id']}/download").content == data


@pytest.mark.parametrize("mode,format", [("RGB", "JPEG"), ("RGBA", "PNG")])
def test_high_entropy_images_stay_within_transfer_cap_without_modifying_original(tmp_path, mode, format):
    channels = 4 if mode == "RGBA" else 3
    picture = Image.frombytes(mode, (1400, 1400), random.Random(8).randbytes(1400 * 1400 * channels))
    source = tmp_path / ("noisy." + format.lower())
    picture.save(source, format)
    original = source.read_bytes()
    output = previews.image_preview(source, "synthetic-high-entropy")
    assert len(output) <= 64 * 1024
    with Image.open(BytesIO(output)) as decoded:
        assert decoded.format == "WEBP" and max(decoded.size) <= 1024
        if mode == "RGBA":
            assert decoded.mode == "RGBA"
    assert source.read_bytes() == original


def test_simultaneous_cold_requests_encode_once_and_hot_hits_do_not_wait_for_cold_work(tmp_path, monkeypatch):
    monkeypatch.setattr(previews, "_preview_cache", OrderedDict())
    calls = []
    entered, release = threading.Event(), threading.Event()
    def encode(path):
        calls.append(path)
        if path.name == "cold":
            entered.set()
            assert release.wait(5)
        else:
            time.sleep(.04)
        return path.name.encode()
    monkeypatch.setattr(previews, "_encode_preview", encode)
    hot, cold = tmp_path / "hot", tmp_path / "cold"
    with ThreadPoolExecutor(max_workers=6) as pool:
        outputs = list(pool.map(lambda _: previews.image_preview(hot, "v1"), range(6)))
        assert outputs == [b"hot"] * 6 and calls.count(hot) == 1
        pending = pool.submit(previews.image_preview, cold, "v1")
        assert entered.wait(5)
        try:
            # A cached request completes while the other image's encoder is blocked.
            assert pool.submit(previews.image_preview, hot, "v1").result(timeout=1) == b"hot"
        finally:
            release.set()
        assert pending.result(timeout=5) == b"cold"
    for number in range(previews.MAX_CACHED_PREVIEWS + 1):
        previews.image_preview(tmp_path / str(number), "v1")
    assert len(previews._preview_cache) == previews.MAX_CACHED_PREVIEWS
    assert (hot, "v1") not in previews._preview_cache


def test_gallery_legacy_display_is_small_and_original_png_stays_available(client):
    from app.config import ROOT
    card = client.get('/api/v1/tools/gallery').json()[0]
    path = '/api/v1/tools/gallery/' + card['id']
    response = client.get(path)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/webp'
    assert len(response.content) <= 64 * 1024
    assert client.get(path, headers={'Authorization':''}).status_code == 401
    original = client.get(path + '?original=true')
    assert original.headers['content-type'] == 'image/png'
    assert original.content == (ROOT / 'resources/plot/gallery' / (card['id'] + '.png')).read_bytes()
    assert client.get('/api/v1/tools/gallery/not-an-asset').status_code == 404


def test_grayscale_alpha_survives_legacy_preview(client):
    data = BytesIO()
    picture = Image.new("LA", (100, 100), (255, 0))
    picture.save(data, "PNG")
    item = client.post('/api/v1/files', files={'file':('synthetic-gray-alpha.png',data.getvalue(),'image/png')}).json()
    response = client.get(f"/api/v1/files/{item['id']}/download?inline=true")
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as decoded:
        assert decoded.mode == "RGBA" and decoded.getpixel((50,50))[3] == 0


def test_cold_http_requests_do_not_exhaust_shared_http_threads_or_cancel_other_waiters(client, monkeypatch):
    monkeypatch.setattr(previews, "_preview_cache", OrderedDict())
    library = client.app.state.library
    hot = library.save('hot.jpg', picture())
    cold = library.save('cold.jpg', picture())
    cold_path = library.path(cold['id'])
    entered, release = threading.Event(), threading.Event()
    calls = []
    def encode(path):
        calls.append(path)
        if path == cold_path:
            entered.set()
            assert release.wait(10)
        return b'synthetic-preview'
    monkeypatch.setattr(previews, "_encode_preview", encode)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url='http://test', headers=dict(client.headers)) as browser:
            assert (await browser.get(f"/api/v1/files/{hot['id']}/preview")).status_code == 200
            requests=[asyncio.create_task(browser.get(f"/api/v1/files/{cold['id']}/preview")) for _ in range(45)]
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                await asyncio.sleep(.1)
                requests[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await requests[0]
                ready = await asyncio.wait_for(browser.get(f"/api/v1/files/{hot['id']}/preview"), timeout=1)
                assert ready.status_code == 200
            finally:
                release.set()
                results = await asyncio.gather(*requests[1:], return_exceptions=True)
            assert all(isinstance(response,httpx.Response) and response.status_code == 200 for response in results)
            assert calls.count(cold_path) == 1
    asyncio.run(exercise())
