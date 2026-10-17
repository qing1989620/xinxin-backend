# 后端双轴审查与复验

使用 [mattpocock code-review](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/code-review/SKILL.md)。Standards 与 Spec 由独立并行审查代理完成，分别报告，不合并问题或跨轴排名。审查只访问源码与合成数据，没有读取正式数据、凭据或连接外部 SMTP/SSH。

初审固定比较：`a8ce66c4c12a3ebef2f9fef572888fb39004f402...0b3b60f94daf4f4456bf59961480504db06e2f5c`。复审比较：`0b3b60f...b9d0e61e3943c9be163dd4809643c3a92545a2db`，命令及 Git 基线已解析，差异非空。规格：`docs/product-spec.md`；标准：本仓库与集成目录 AGENTS、README、THIRD_PARTY、deployment.md。

## Standards

初审确认 3 项硬规则问题，2 项判断性代码气味。

| 发现 | 修复与证据 |
| --- | --- |
| P1：422 参数与提供商异常可能回显秘密 | 422 不返回 input/context；异常和工具事件经过凭据脱敏；合成凭据回归 |
| P1：命令父进程退出后子进程可越过预算或继续运行 | Windows 在主线程恢复前绑定 Job Object，Linux 管理进程组，等待并终止整个受管范围；父进程退出、继承/关闭 stdout、超时与取消回归 |
| P2：过期自动化在重启后补跑 | 启动把过期 next_at 跳到下一个周期，保留历史，不补发任务；重启回归 |
| 判断：绘图分支和导出脚本重复、样式不同 | 提取 `figure_recipe.render_figure`；平台与导出脚本使用同一函数，子进程运行脚本得到字节一致的 PNG |
| 判断：create_app 的职责变化多 | 路由仍集中装配，邮箱规则已移到 MailCodes；后续可按路由域继续拆分。未作为硬违规 |

复审报告（固定 HEAD b9d0e61，轻度整理）：

- **P1，JSON 转义后的凭据仍进入事件。** `agent.py` 的 redactor 仅替换原始秘密，工具参数/字典结果先 JSON 编码；带换行的 SSH 私钥可从事件恢复。违反 AGENTS 凭据不得写入日志与 README 不回显密钥的规则。
- **P1，并发修改 SMTP 模式可绕过 TLS。** `mail.py` 两次读取 smtp_mode；若发送期间由 STARTTLS 改成 SSL，可能先建立普通连接、后跳过升级就认证。假 SMTP 离线复现确认。违反 deployment.md 的 TLS 约束。
- **判断性建议：可能的 Divergent Change。** 认证、统计与资源交付仍因不同原因修改 create_app，可渐进拆分；这是维护性建议。

复审后的修复：脱敏覆盖原始与 JSON 转义形式、PEM 块和 vault 密钥；SMTP 保存为原子事务，发送取得同一提交的配置快照，TLS 模式与认证凭据在连接前固定。新增测试覆盖 JSON/SSE 持久事件、配置在连接建立期间变化、先完成 TLS 再认证与凭据快照。

## Spec

初审确认 4 项：过期定时补跑、未经明确授权保存记忆、长文尾部无法检索读取、路由未使用 UCB 的探索部分。均已修复并加入回归：每轮独立 allow_memory、全文块检索和分页、正确 UCB 分数、启动跳过错过的定时周期。

复审独立结论：**未发现可确认的新规格缺陷**。四项旧问题已复验；HMAC/冷却/配额/单次消费/密码重置撤销会话均符合补充规格。额外合成任务验证了默认并发 1、排队取消只发一个结束事件、归档恢复保留历史。没有确认范围扩张。

真实 SSH、图像服务、SMTP 送达、Linux 容器和手机仍待相应环境验收，不能以本地替身代替。

## 本地验证

`python -m pytest -q --junitxml=docs/test-results.xml`：**56 passed**，包含真实 Tina 离线工具循环、权限/路径/凭据、断流/幂等/回放、进程树、队列取消、绘图复现、迁移、邮箱竞态等。存在一条 Starlette TestClient 依赖弃用提示，不影响本次测试。

DeepSeek 官方接口与实际工具执行已完成独立真实验证，见 `model-connection.json`、`model-execution.json`；浏览器另完成一次 2 次调用的真实写文件任务。没有真实 SSH、SMTP 或图像服务的通过记录。

本轮复审统计：Standards 2 项硬规则发现（已修复回归）、1 项判断性建议；Spec 0 项新增发现。最严重问题分别为 Standards 的 P1 凭据/TLS，Spec 无新增问题。
