from __future__ import annotations

import base64
import io
import json
import threading
from pathlib import Path

import httpx

from .config import ROOT
from .model import endpoint
from .store import uid

PLOT_LOCK = threading.Lock()


def gallery():
    result = []
    root = ROOT / "resources" / "plot" / "gallery"
    for path in sorted(root.glob("*.png")):
        card = path.with_suffix(".md")
        if not card.exists():
            continue
        import yaml

        metadata = (
            yaml.safe_load(card.read_text(encoding="utf-8").split("---", 2)[1]) or {}
        )
        result.append(
            {
                "id": path.stem,
                "title": metadata.get("title", path.stem),
                "kind": metadata.get("kind"),
                "purpose": metadata.get("purpose", []),
                "palettes": metadata.get("palettes", []),
                "has_code": path.with_suffix(".py").exists(),
            }
        )
    return sorted(result, key=lambda row: not row["has_code"])


def plot_csv(
    library,
    file_id,
    x,
    y,
    kind="line",
    title="Data study",
    palette="teal",
    run_id=None,
    cancelled=None,
):
    def check_cancelled():
        if cancelled and cancelled.is_set():
            raise RuntimeError("绘图已取消")

    check_cancelled()
    if kind not in {"line", "bar", "scatter", "hist"}:
        raise ValueError("不支持的图表类型")
    import pandas as pd

    frame = pd.read_csv(library.path(file_id), nrows=100000)
    if kind == "bar" and len(frame) > 200:
        raise ValueError("柱状图最多绘制 200 行，请先汇总或筛选数据")
    if x not in frame.columns or y not in frame.columns:
        raise ValueError("所选列不存在")
    colours = {
        "teal": "#256d63",
        "clay": "#b65b43",
        "indigo": "#555e99",
        "ink": "#333c43",
    }
    color = colours.get(palette, colours["teal"])
    with PLOT_LOCK:
        check_cancelled()
        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from matplotlib import font_manager

        available = {font.name for font in font_manager.fontManager.ttflist}
        font = next(
            (
                name
                for name in ("Microsoft YaHei", "Noto Sans CJK SC", "SimHei")
                if name in available
            ),
            "DejaVu Sans",
        )
        with plt.rc_context(
            {
                "font.family": font,
                "axes.unicode_minus": False,
                "axes.spines.top": False,
                "axes.spines.right": False,
                "figure.facecolor": "#faf8f3",
                "axes.facecolor": "#faf8f3",
                "axes.edgecolor": "#d8d5cd",
                "axes.labelcolor": "#373c3a",
                "text.color": "#373c3a",
            }
        ):
            fig, ax = plt.subplots(figsize=(9, 5.5), layout="constrained")
            try:
                values = pd.to_numeric(frame[y], errors="coerce")
                keep = values.notna()
                if not keep.any():
                    raise ValueError("Y 列没有可绘制的数值")
                a, b = frame.loc[keep, x], values[keep]
                if kind == "line":
                    ax.plot(a, b, color=color, linewidth=2)
                elif kind == "bar":
                    ax.bar(a.astype(str), b, color=color, width=0.65)
                elif kind == "scatter":
                    ax.scatter(a, b, color=color, s=30, alpha=0.7, edgecolors="none")
                else:
                    ax.hist(b, bins="auto", color=color, alpha=0.85)
                ax.set_title(title[:100], loc="left", fontsize=18, pad=24)
                ax.set_xlabel(x if kind != "hist" else y)
                ax.set_ylabel(y if kind != "hist" else "Count")
                ax.grid(axis="y", color="#dedbd3", linewidth=0.6, alpha=0.65)
                ax.set_axisbelow(True)
                outputs = []
                for ext in ("png", "pdf"):
                    check_cancelled()
                    stream = io.BytesIO()
                    fig.savefig(stream, format=ext, dpi=250)
                    check_cancelled()
                    outputs.append(
                        library.save(
                            "xinxin-figure." + ext,
                            stream.getvalue(),
                            run_id=run_id,
                            extract=False,
                        )
                    )
            finally:
                plt.close(fig)
    recipe = {"x": x, "y": y, "kind": kind, "title": title, "color": color}
    script = (
        "# Xinxin reproducible figure. Use the original CSV as input.csv.\nimport json\nimport pandas as pd\nimport matplotlib.pyplot as plt\nc = json.loads("
        + repr(json.dumps(recipe, ensure_ascii=False))
        + ")\ndf = pd.read_csv('input.csv')\nfig, ax = plt.subplots(figsize=(9,5.5), layout='constrained')\ny = pd.to_numeric(df[c['y']], errors='coerce')\nmask = y.notna()\nx, y = df.loc[mask,c['x']], y[mask]\nif c['kind'] == 'line': ax.plot(x,y,color=c['color'])\nelif c['kind'] == 'bar': ax.bar(x.astype(str),y,color=c['color'])\nelif c['kind'] == 'scatter': ax.scatter(x,y,color=c['color'])\nelse: ax.hist(y,bins='auto',color=c['color'])\nax.set(title=c['title'],xlabel=c['x'],ylabel=c['y'])\nfig.savefig('figure.png',dpi=300)\nfig.savefig('figure.pdf')\n"
    )
    check_cancelled()
    outputs.append(library.save("xinxin-figure.py", script.encode(), run_id=run_id))
    return {"files": outputs, "rows": int(keep.sum()), "source_file_id": file_id}


async def transform_image(
    library, auth, file_id, style, instruction, run_id=None, transport=None
):
    names = {
        "collage": "scenes-gathered-zine-v1-3",
        "distillation": "scene-distillation-zine-v1-3",
    }
    if style not in names:
        raise ValueError("未知转换方式")
    url = auth.store.setting("image_url", "")
    key = auth.secret("image_api_key")
    model = auth.store.setting("image_model", "gpt-image-1")
    if not url or not key:
        raise ValueError(
            "请先在设置中配置图像生成服务；DeepSeek 对话接口不负责图片生成"
        )
    skill = auth.store.directory / "skills" / names[style] / "SKILL.md"
    if not skill.exists():
        raise ValueError("拾景技能尚未安装，请运行后端的可选技能安装脚本")
    path = library.path(file_id)
    from PIL import Image

    with Image.open(path) as im:
        if im.width * im.height > 40_000_000:
            raise ValueError("图片像素过大")
        im.verify()
    prompt = (
        skill.read_text(encoding="utf-8") + "\n\nUser direction: " + instruction[:3000]
    )
    async with httpx.AsyncClient(
        timeout=300, transport=transport, follow_redirects=False
    ) as client:
        with path.open("rb") as image:
            response = await client.post(
                endpoint(url),
                headers={"Authorization": "Bearer " + key},
                data={"model": model, "prompt": prompt, "n": "1"},
                files={
                    "image": (
                        "reference" + path.suffix,
                        image,
                        library.get(file_id)["mime"],
                    )
                },
            )
        if response.status_code >= 300:
            raise ValueError(f"图像服务返回异常状态 {response.status_code}")
        item = response.json().get("data", [{}])[0]
        if not item.get("b64_json"):
            raise ValueError(
                "图像接口需返回 b64_json；未自动访问服务返回的外部下载地址"
            )
        image_data = base64.b64decode(item["b64_json"], validate=True)
        result = library.save(
            "xinxin-zine.png", image_data, "image/png", run_id, extract=False
        )
        return {
            "files": [result],
            "style": style,
            "credit": "Visual Skill by @Zeejay0",
            "source_sent_to_configured_image_service": True,
        }
