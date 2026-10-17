from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import yaml

from .config import ROOT
from .skillnet.catalog import SkillLibrary
from .skillnet.index import BM25Index, VectorIndex, weighted_fuse
from .skillnet.schema import Skill
from .skillnet.bandit import SharedLinUCB

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
                    capability=description,
                    tags=[domain],
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
            self.bm25, self.vector = BM25Index(), VectorIndex()
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
                    "name": next((b[1] for b in BUILTINS if b[0] == s.name), s.name),
                    "description": s.description,
                    "domain": s.domain,
                    "tools": self.tools[s.name],
                    "relations": s.relations,
                    "source": (
                        "personal"
                        if s.domain in {"图片", "自定义"}
                        and s.name not in {b[0] for b in BUILTINS}
                        else "xinxin"
                    ),
                    "enabled": True,
                }
                for s in self.library
            ]

    def search(self, query, k=4):
        with self.lock:
            fused = weighted_fuse(
                [
                    self.bm25.search(query, top_k=20),
                    self.vector.search(query, top_k=20),
                ],
                [0.75, 0.25],
            )
            if self.bandit.n_updates:
                fused = sorted(
                    [
                        (name, score + 0.1 * self.bandit.score(query, name)[1])
                        for name, score in fused
                    ],
                    key=lambda item: -item[1],
                )
            by_name = {s["id"]: s for s in self.all()}
            selected = [
                {**by_name[name], "score": round(score, 4)} for name, score in fused[:k]
            ]
            names = {s["id"] for s in selected}
            dependencies = []
            for s in selected:
                for relation, target in s["relations"]:
                    if (
                        relation == "depend_on"
                        and target not in names
                        and target in by_name
                    ):
                        dependencies.append(by_name[target])
                        names.add(target)
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
                        if relation == "depend_on" and target in names:
                            visit(target)
                visiting.remove(name)
                visited.add(name)
                ordered.append(name)

            for name in sorted(names):
                visit(name)
            return {
                "query": query,
                "skills": selected,
                "dependencies": dependencies,
                "order": ordered,
                "learning_updates": self.bandit.n_updates,
                "algorithm": "SkillNet BM25 + lexical vector weighted fusion + evidence-gated shared LinUCB",
                "authorization": "independent",
                "execution_verified": False,
            }

    def load(self, name):
        with self.lock:
            if name not in self.bodies:
                raise ValueError("技能不存在")
            return {
                "name": name,
                "markdown": self.bodies[name],
                "tools": self.tools[name],
            }
