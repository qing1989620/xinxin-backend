# 技能语义模型与人格：独立双轴审查

2026-10-17，按 mattpocock `code-review`，Standards / Spec 由两个独立并行子代理执行。标准为根目录和本仓库 AGENTS.md、README 运行契约及 Skill 的代码气味基线。规格为 [skill-persona-spec.md](skill-persona-spec.md)。

固定输入：`git diff fdf4ab7f297dcd1c71ff555a5df1356dfefffad2...16c0c2a9ed451b22a8e1d79471bacbc82dc62339`，提交 `16c0c2a Add semantic SkillNet routing and Xinxin agent persona`；基线解析、差异非空已核实。修复定点复验：`git diff 16c0c2a9ed451b22a8e1d79471bacbc82dc62339...ff89c6a0439922baaa1ef421244b94740397f085`。

## Standards

初审 1 项硬规则问题、1 项判断性气味，最严重为 P2 自动降级失效。

- **P2，硬规则：**`make_index` 仅捕获 Python RuntimeError 等，ONNX Runtime 的 Fail、RuntimeException、NotImplemented 不继承 RuntimeError；auto 模式可能启动失败，违反 README 的明确降级契约。独立审查使用实际异常类型和假 DenseIndex 复现。
- **可能的 Duplicated Code，判断性建议：**直接依赖循环与新 DFS 重复维护 names / dependencies，存在规则分叉风险。

修复：在可选第三方编码器的初始化边界捕获 Exception，不捕获 BaseException；把首次真实推理预热放在这一边界内；dense 模式仍明确失败，不暴露异常输入。依赖统一为单次 DFS。

Standards 审查者对 ff89c6a 的独立复验：实际 ORT 三类异常模拟首次推理失败，auto 回退、dense 明确失败；KeyboardInterrupt / SystemExit 保持向上传播。链式依赖无重复、循环被拒绝。**原硬违规与判断性气味均消除，本次范围未发现新增具体问题。**仅使用假推理会话与临时目录。

## Spec

初审 1 项 P2，最严重为领域权重没有实际贡献。

- 规格要求领域标签加权融合、dense 35/55/10 与 lexical 70/20/10；默认领域与标签相同，命中等分时上游 min-max 把整个结构通路归零。“开发和绘图”的结构权重从 0 改为 0.1 甚至 100，分数原本不变，只剩一致性奖励。
- 未确认其他缺失或范围扩张。人格进入实际系统提示构造路径，权限执行器没有随人格修改；少量样本不能证明普遍检索效果。

修复：正值同分/单命中归一化为 1，零分为 0，变分通路保留上游公式；代码内联标记，THIRD_PARTY 说明这一适配。

Spec 审查者对 ff89c6a 的独立复验：“开发和绘图”的真实结构命中将权重从 0 调为 0.1 时，各自分数增加 0.1；单命中、零分与变分行为正确，传递依赖与循环检测保持正确。**领域权重缺陷已消除，新增发现 0 项，最严重项：无。**未重新运行真实 E5 样本或外部服务。

## 执行证据与范围

后端 **65 项回归通过**（`docs/test-results.xml`），包含实际本地 E5 五类查询、坏缓存重建、同尺寸模型篡改拒绝、原生 ORT 异常、融合等分与传递依赖。`pip check` 无损坏依赖。

`docs/persona-execution.json` 为真实 DeepSeek 的独立合成数据验收：介绍身份 1 次模型调用；full 任务两条本机命令创建和独立验证文件，再 publish_file，4 次调用；read 任务拒绝文本扩大权限，1 次调用且没有工具执行。它证明记录的案例，不是通用人格稳定性或任意桌面自动化证明。

仅本地实现；真实 SSH、SMTP、图像服务、容器与 Android 真机仍待相应环境。

本轮统计：Standards 1 项硬规则发现 + 1 项判断性建议（均修复并独立复验），Spec 1 项发现（修复并独立复验）。两轴最严重的初审问题均为各自的 P2；定点复验两轴新增问题均为 0。
