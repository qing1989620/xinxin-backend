import hashlib
import json

import pytest

from app.config import ROOT
from app.skillnet import semantic
from app.skills import SkillRouter
from app.store import Store


def test_missing_encoder_is_observable_and_dense_mode_never_silently_falls_back(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XINXIN_SKILL_ENCODER", "auto")
    fallback = semantic.make_index(tmp_path)
    assert fallback.status["kind"] == "lexical-hashing" and fallback.status["degraded"]
    assert str(tmp_path) not in json.dumps(fallback.status)
    monkeypatch.setenv("XINXIN_SKILL_ENCODER", "dense")
    with pytest.raises(RuntimeError, match="语义模型"):
        semantic.make_index(tmp_path)


def test_pinned_manifest_rejects_same_size_tampering(tmp_path, monkeypatch):
    model = tmp_path / "model.bin"
    model.write_bytes(b"good")
    manifest = tmp_path / "pinned.json"
    manifest.write_text(
        json.dumps(
            {
                "files": {
                    "model.bin": {
                        "bytes": 4,
                        "sha256": hashlib.sha256(b"good").hexdigest(),
                    }
                }
            }
        )
    )
    monkeypatch.setattr(semantic, "MANIFEST", manifest)
    assert semantic.verify_model(tmp_path)
    model.write_bytes(b"evil")
    with pytest.raises(ValueError, match="integrity"):
        semantic.verify_model(tmp_path)


def test_transitive_dependencies_are_loaded_before_the_selected_skill(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XINXIN_SKILL_ENCODER", "lexical")
    router = SkillRouter(Store(tmp_path))
    router.library.skills["coding-workspace"].relations = [
        ("depend_on", "private-documents")
    ]
    router.library.skills["private-documents"].relations = [
        ("depend_on", "owner-memory")
    ]
    monkeypatch.setattr(
        router.bm25, "search", lambda *_args, **_kwargs: [("coding-workspace", 1)]
    )
    monkeypatch.setattr(router.vector, "search", lambda *_args, **_kwargs: [])
    result = router.search("build", k=1)
    assert result["order"] == ["owner-memory", "private-documents", "coding-workspace"]
    assert {s["id"] for s in result["dependencies"]} == {
        "owner-memory",
        "private-documents",
    }
    router.library.skills["owner-memory"].relations = [
        ("depend_on", "coding-workspace")
    ]
    with pytest.raises(ValueError, match="循环"):
        router.search("build", k=1)


@pytest.mark.skipif(
    not (ROOT / "data/semantic-model/onnx/model_quantized.onnx").is_file(),
    reason="Pinned optional model not installed",
)
def test_real_multilingual_encoder_routes_and_recovers_a_damaged_cache(
    tmp_path, monkeypatch
):
    import numpy as np
    import app.skills as skills

    encoder = semantic.DenseIndex(ROOT / "data/semantic-model", tmp_path / "cache")
    monkeypatch.setattr(skills, "make_index", lambda _directory: encoder)
    router = SkillRouter(Store(tmp_path / "store"))
    for query, expected in [
        ("替我把 GitHub 上的项目上线", "server-deployment"),
        ("turn measurements into a scatter diagram", "scientific-plotting"),
        ("看一下扫描的资料里说了什么", "private-documents"),
        ("帮我在电脑上修复 Python 程序并跑测试", "coding-workspace"),
        ("请记住我喜欢深青色", "owner-memory"),
    ]:
        result = router.search(query)
        assert result["skills"][0]["id"] == expected, result
        assert result["encoder"]["kind"] == "dense-onnx"
        assert (
            result["authorization"] == "independent"
            and not result["execution_verified"]
        )
        assert "vector" in result["skills"][0]["channels"]
    cache = next((tmp_path / "cache").glob("*.npz"))
    with np.load(cache, allow_pickle=False) as data:
        shape = data["embeddings"].shape
    np.savez_compressed(cache, embeddings=np.full(shape, np.nan, dtype=np.float32))
    router.refresh()
    assert np.isfinite(encoder.snapshot[1]).all()
    cache.write_bytes(b"PK\x03\x04broken")
    router.refresh()
    assert np.isfinite(encoder.snapshot[1]).all()
    assert router.search("部署 GitHub 网站")["skills"][0]["id"] == "server-deployment"
