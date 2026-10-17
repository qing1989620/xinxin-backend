# 后端审查计划

指定技能来源：[mattpocock/skills](https://github.com/mattpocock/skills/tree/49dd158d1076134a641b33efb035946536778336)。

已阅读：

- [codebase-design](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/codebase-design/SKILL.md)，用于职责、接口和可测试性评估。
- [code-review](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/code-review/SKILL.md)，用于实现后的 Standards / Spec 双轴审查。

本阶段只有准备材料，尚无功能实现差异，不能宣称已完成正式代码审计。

实施后固定可解析的 Git 基线和需求文档，先确认 diff 非空，再按 code-review 技能分别审查：

| 轴 | 内容 |
| --- | --- |
| Standards | 本仓库约定、职责与接口、错误处理、并发、凭据处理、执行限制；区分硬规则与代码气味判断 |
| Spec | 最终功能评分、主人具体任务、Agent 一级入口所需接口、实际执行结果与验收证据 |

技能要求两轴通过独立并行审查子代理执行；实际进入该审查时按技能执行，不把两轴混成一个评分。

本地需求与追踪使用文件，不创建 GitHub Issue，不向第三方发送数据。正式审查保留输入基线、命令、发现、修复和复验证据。
