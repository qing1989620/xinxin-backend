from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import yaml

from .config import ROOT
from .skillnet.catalog import SkillLibrary
from .skillnet.index import BM25Index, weighted_fuse
from .skillnet.semantic import make_index, DENSE_WEIGHTS, WEIGHTS
from .skillnet.schema import Skill
from .skillnet.bandit import SharedLinUCB
from .skill_packages import SkillPackages

BUILTINS = [
    (
        "server-deployment",
        "服务器部署",
        "从 GitHub 仓库检查构建方式，使用 Docker Compose Nginx HTTPS 部署网站并核验健康状态",
        "运维",
        ["list_servers", "run_remote_command", "deploy_repository"],
        [],
    ),
    (
        "private-documents",
        "私人资料",
        "搜索私人文件 文档 PDF Word Markdown CSV，引用原始文件片段回答问题",
        "资料",
        ["search_documents", "read_document", "copy_document_to_workspace"],
        [],
    ),
    (
        "coding-workspace",
        "代码工作区",
        "编写修改代码 创建文件 分析仓库运行测试 修复错误",
        "开发",
        ["list_files", "read_file", "write_file", "execute_command", "publish_file"],
        [],
    ),
    (
        "scientific-plotting",
        "数据绘图",
        "读取 CSV 表格 销售数据 选择质感参考图 绘制趋势 折线 柱状 散点 分布图 导出 PNG PDF 可复现脚本",
        "绘图",
        ["plot_csv_tool", "list_gallery", "read_plot_resource"],
        [("depend_on", "private-documents")],
    ),
    (
        "personal-content",
        "个人内容",
        "管理文章 合集 相册 诗词 简历 保存内容草稿",
        "内容",
        ["search_content", "create_content_draft"],
        [],
    ),
    (
        "owner-memory",
        "主人记忆",
        "记住主人的偏好 习惯 项目事实 长期记忆",
        "记忆",
        ["list_memories", "remember"],
        [],
    ),
    (
        "gathered-scenes-zine",
        "拾景 · 图片转换",
        "将照片进行实景拼贴或影像蒸馏，生成有纸张质感的 zine 海报 图片风格转换",
        "图片",
        ["transform_image_tool"],
        [],
    ),
]

# Task-shaped contracts improve the encoder input; no duplicated description.
# These are activation examples, not execution certification or extra authority.
CONTRACTS = {
    "server-deployment": [
        "把 GitHub 上的项目上线，发布网站，部署云服务器",
        "配置域名、HTTPS、Docker Compose，排查网站服务故障",
        "Deploy a GitHub repository to a server and check website health",
    ],
    "private-documents": [
        "阅读上传的 PDF、Word、扫描资料或文档，提炼内容并引用来源",
        "从私人文件查找问题的答案，检查完整文档而非只看开头",
        "Read and summarize private documents with citations",
    ],
    "coding-workspace": [
        "在本地电脑创建或修改程序，修复 Python、JavaScript 等代码，运行测试",
        "检查本地仓库，编写脚本，整理工程文件",
        "Write code, fix a bug, run tests, inspect a local repository",
    ],
    "scientific-plotting": [
        "把月度销售额、实验结果等 CSV 数据画成折线图、柱状图或散点图",
        "查看图表参考样式，输出可下载图片、PDF 和可复现代码",
        "Visualize monthly revenue in a line chart; plot scientific data",
    ],
    "personal-content": [
        "编辑个人文章、合集、诗词、相册和简历，保存新的内容草稿",
        "查找已写的文章或整理作品集",
        "Manage personal posts, albums, poems and resume content",
    ],
    "owner-memory": [
        "用户明确要求记住偏好或事实，留待以后使用",
        "查看已经保存的长期记忆",
        "Explicitly remember a preference or recall stored long-term memory",
    ],
    "gathered-scenes-zine": [
        "把照片转换为拼贴海报、纸张质感 zine 或插画，处理图片风格",
        "Transform a photograph into an illustrated zine or collage",
    ],
}


class SkillRouter:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.directory = store.directory / "skills"
        self.directory.mkdir(exist_ok=True)
        self.refresh()

    def refresh(self):
        with self.lock:
            skills, bodies, tools = [], {}, {}
            for name, title, description, domain, tool_names, relations in BUILTINS:
                skill = Skill(
                    name=name,
                    description=description,
                    domain=domain,
                    capability=title,
                    tags=[domain],
                    use_when=CONTRACTS[name],
                    relations=relations,
                )
                skills.append(skill)
                bodies[name] = (
                    f"# {title}\n\n{description}\n\n只调用平台注册的工具。先检查输入和授权，记录真实结果。不能用推测代替实际执行验收。\n\n工具：{', '.join(tool_names)}"
                )
                tools[name] = tool_names
            plot_entry = ROOT / "resources/plot/SKILL.md"
            if plot_entry.exists():
                bodies["scientific-plotting"] = (
                    plot_entry.read_text(encoding="utf-8")
                    + "\n\n平台工具：plot_csv_tool 生成基础图表，list_gallery 查看样图，read_plot_resource 按相对路径读取样式卡、阶段说明与参考脚本。复杂图可用 copy_document_to_workspace 读取所选 CSV，在获准完整权限后编写脚本执行，publish_file 交付实际生成的文件。"
                )
            self.packages = SkillPackages()
            builtin_tools = dict(tools)
            for card in self.packages.cards.values():
                name, base = card["id"], card["base_skill"]
                skills.append(
                    Skill(
                        name=name,
                        description=card["description"],
                        domain=card["domain"],
                        tags=[card["domain"]],
                        capability=card["name"],
                        use_when=[card["original_description"]],
                        relations=[("depend_on", base)],
                        source="github",
                        license=card["license"],
                    )
                )
                # Full reference bodies are loaded lazily and rehashed on every read.
                tools[name] = list(builtin_tools[base])
            for path in sorted(self.directory.glob("*/SKILL.md")):
                text = path.read_text(encoding="utf-8")
                if len(text) > 150000:
                    continue
                header = (
                    yaml.safe_load(text.split("---", 2)[1])
                    if text.startswith("---")
                    else {}
                )
                name = (header or {}).get("name", path.parent.name)
                if not isinstance(name, str) or not re.fullmatch(
                    r"[a-z0-9]+(?:-[a-z0-9]+)*", name
                ):
                    continue
                skills.append(
                    Skill(
                        name=name,
                        description=str((header or {}).get("description", "")),
                        domain="图片" if "zine" in name else "自定义",
                        capability=str((header or {}).get("description", "")),
                    )
                )
                bodies[name] = text
                tools[name] = ["transform_image_tool"] if "zine" in name else []
            self.library = SkillLibrary(skills)
            self.bodies, self.tools = bodies, tools
            self.bm25 = BM25Index()
            if not hasattr(self, "vector"):
                self.vector = make_index(self.store.directory)
            ids = [s.name for s in skills]
            texts = [s.l1_text() for s in skills]
            self.bm25.fit(ids, texts)
            self.vector.fit(ids, texts)
            self.bandit = SharedLinUCB(self.library)
            state = self.store.setting("skill-policy")
            if state:
                try:
                    self.bandit.load_state(state)
                except (ValueError, KeyError, TypeError):
                    pass

    def all(self):
        with self.lock:
            return [
                {
                    "id": s.name,
                    "name": next(
                        (b[1] for b in BUILTINS if b[0] == s.name),
                        {
                            "scene-distillation-zine-v1-3": "拾景 · 影像蒸馏",
                            "scenes-gathered-zine-v1-3": "拾景 · 实景拼贴",
                        }.get(
                            s.name,
                            self.packages.cards.get(s.name, {}).get("name", s.name),
                        ),
                    ),
                    "description": {
                        "scene-distillation-zine-v1-3": "从照片提炼情绪、形状和色彩，以源自原图的插画重新构成留白充足的独立海报。",
                        "scenes-gathered-zine-v1-3": "保留真实照片，结合撕纸边缘、简化插画和纸张质感，制作安静的实景拼贴海报。",
                    }.get(s.name, s.description),
                    "domain": s.domain,
                    "tools": self.tools[s.name],
                    "relations": s.relations,
                    "source": "skillnet-community"
                    if s.name in self.packages.cards
                    else (
                        "personal"
                        if s.domain in {"图片", "自定义"}
                        and s.name not in {b[0] for b in BUILTINS}
                        else "xinxin"
                    ),
                    "provenance": {
                        key: self.packages.cards[s.name][key]
                        for key in (
                            "repository",
                            "revision",
                            "source_url",
                            "license",
                            "execution_verified",
                        )
                    }
                    if s.name in self.packages.cards
                    else None,
                    "enabled": True,
                }
                for s in self.library
            ]

    def search(self, query, k=4):
        with self.lock:
            bm25 = self.bm25.search(query, top_k=20)
            vector = self.vector.search(query, top_k=20)
            structural = self._structural_hits(query)
            weights = self.status()["weights"]
            fused = weighted_fuse(
                [bm25, vector, structural],
                weights,
            )
            if self.bandit.n_updates:
                fused = sorted(
                    [
                        (name, score + 0.1 * self.bandit.score(query, name)[0])
                        for name, score in fused
                    ],
                    key=lambda item: -item[1],
                )
            # An explicit registered ID from the skill chooser is an intentional
            # selection, not a grant of authority or evidence of execution.
            explicit = [
                s.name
                for s in self.library
                if re.search(
                    r"(?<![a-z0-9-])" + re.escape(s.name) + r"(?![a-z0-9-])",
                    query.lower(),
                )
            ]
            if explicit:
                lookup = dict(fused)
                fused = [(name, lookup.get(name, 0)) for name in explicit] + [
                    (name, score) for name, score in fused if name not in explicit
                ]
            by_name = {s["id"]: s for s in self.all()}
            channels = {
                "bm25": dict(bm25),
                "vector": dict(vector),
                "structural": dict(structural),
            }
            selected = [
                {
                    **by_name[name],
                    "score": round(score, 4),
                    "channels": {
                        channel: hits[name]
                        for channel, hits in channels.items()
                        if name in hits
                    },
                }
                for name, score in fused[:k]
            ]
            names = {s["id"] for s in selected}
            dependencies = []
            ordered, visiting, visited = [], set(), set()

            def visit(name):
                if name in visiting:
                    raise ValueError("技能依赖包含循环")
                if name in visited:
                    return
                visiting.add(name)
                skill = self.library.get(name)
                if skill:
                    for relation, target in skill.relations:
                        if relation == "depend_on" and target in by_name:
                            if target not in names:
                                names.add(target)
                                dependencies.append(by_name[target])
                            visit(target)
                visiting.remove(name)
                visited.add(name)
                ordered.append(name)

            # Preserve relevance order among independent candidates. DFS only
            # moves genuine prerequisites ahead of their dependent skill.
            for candidate in selected:
                visit(candidate["id"])
            return {
                "query": query,
                "skills": selected,
                "dependencies": dependencies,
                "order": ordered,
                "learning_updates": self.bandit.n_updates,
                **self.status(),
                "score_kind": "relative_ranking_not_probability",
                "authorization": "independent",
                "execution_verified": False,
            }

    def _structural_hits(self, query):
        # Same exact domain/tag matching as SkillNet Retriever, not substring tokens.
        q, hits = query.lower(), []
        for skill in self.library:
            segments = [s for s in re.split(r"[与、，,/]", skill.domain) if len(s) >= 2]
            score = 0.7 if any(s.lower() in q for s in segments) else 0
            score += sum(
                0.3 for tag in skill.tags if len(tag) >= 2 and tag.lower() in q
            )
            if score:
                hits.append((skill.name, round(min(score, 2.0), 4)))
        return sorted(hits, key=lambda item: -item[1])[:20]

    def status(self):
        with self.lock:
            encoder = dict(self.vector.status)
            dense = encoder["kind"] == "dense-onnx"
            return {
                "encoder": encoder,
                "weights": list(DENSE_WEIGHTS if dense else WEIGHTS),
                "skill_count": len(self.library),
                "learning_updates": self.bandit.n_updates,
                "algorithm": "SkillNet BM25 + "
                + ("E5 semantic" if dense else "lexical")
                + " + structural weighted fusion + evidence-gated shared LinUCB",
                "source_revision": "0af2de6a9c8b129e174e3442b67c5f7cb4324db9",
                "components": {
                    "bm25": True,
                    "vector": True,
                    "structural": True,
                    "dependency_graph": True,
                    "llm_rerank": False,
                },
            }

    def load(self, name, offset=None):
        with self.lock:
            if offset is not None and not 0 <= offset <= 2_000_000:
                raise ValueError("技能读取偏移无效")
            if name in self.packages.cards:
                card = self.packages.cards[name]
                if offset is None:
                    pages, cursor = [], 0
                    while True:
                        page = self.packages.read(name, offset=cursor)
                        pages.append(page["text"])
                        if page["next_offset"] is None:
                            break
                        cursor = page["next_offset"]
                    text = "".join(pages)
                else:
                    page = self.packages.read(name, offset=offset)
                    text = page["text"]
                return {
                    "name": name,
                    "markdown": text,
                    "tools": self.tools[name],
                    "next_offset": None if offset is None else page["next_offset"],
                    "resources": [f["path"] for f in card["files"]],
                    "provenance": {
                        key: card[key]
                        for key in ("repository", "revision", "source_url", "license")
                    },
                    "adaptation": "这是只读方法与脚本参考。用 read_skill_resource 分页读取列出的资源；不要直接执行包中的脚本。正文提到的外部技能、工具、服务不一定已安装，应先检查。平台工具权限独立判断，需安装依赖或运行脚本时检查环境与内容，再按本轮授权处理。",
                }
            if name not in self.bodies:
                raise ValueError("技能不存在")
            return {
                "name": name,
                "markdown": self.bodies[name]
                if offset is None
                else self.bodies[name][offset : offset + 12000],
                "next_offset": offset + 12000
                if offset is not None and offset + 12000 < len(self.bodies[name])
                else None,
                "tools": self.tools[name],
            }
