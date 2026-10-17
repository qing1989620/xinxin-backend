# Xinxin Backend

私人 Agent 平台后端和任务执行器，独立仓库。

目前为本地调研基线，尚未实现 API、数据库或 Agent 集成。主人确认功能和自动执行范围后开始实现。

远程仓库：<https://github.com/qing1989620/xinxin-backend>

拟选 Python 3.12 + FastAPI，结合 myself 个人内容领域、SkillNet 技能选择与任务证据、Tina 对话和工具调用。

zhishi 先作为知识库、学习助手和移动端衔接的参考；它使用 AGPL-3.0，直接复用代码的范围仍待确定。

模型密钥、服务器密钥、数据库和执行产物不写入仓库。部署工作后续按主人指示进行。

审查流程：[audit-plan.md](docs/audit-plan.md)。
