from io import BytesIO

from PIL import Image

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
        assert max(preview.size) <= 1600 and not preview.getexif()
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
