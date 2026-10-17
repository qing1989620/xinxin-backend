# Xinxin Backend

单主人的私人 Agent 后端。FastAPI + SQLite 持久任务、事件、内容与文件；Tina 工具循环；SkillNet 检索和有证据的反馈学习。与前端通过 `/api/v1` 和受认证 SSE 通信，运行时不读取前端或参考仓库。

## 本地启动

需要 Python 3.12+、Git；命令工具与 SSH 分别依赖宿主 shell 和 OpenSSH。

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.lock
Copy-Item .env.example .env
.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

新数据目录首次打开前端创建主人账号，初始化码在 `data/bootstrap.code`。当前主人的本地数据已单独初始化，没有公开注册接口。设置页配置 DeepSeek 密钥；默认模型 `deepseek-flash`，接口为完整的 Chat Completions URL。密钥本地加密后持久化，不回传前端。

仅运行一个 API 进程（不要配置多个 Uvicorn workers）。启动时把未完成任务标记中断，不自动重做副作用。事件可回放；定时任务停机期间不补跑。

## 权限

| 等级 | 范围 |
| --- | --- |
| read | 阅读、检索、分析；拒绝写文件和命令 |
| workspace | 创建会话工作区文件、图表、图片及私人草稿；拒绝 shell/SSH |
| full | 后端主机 shell 与本次选定 SSH 服务器命令 |

权限和服务器在创建任务时固定，后端每次工具调用再次检查。full 的 shell 具有后端运行账号的系统权限，适合专用账号或隔离私有机器。技能分数不授予权限。

## 私人数据与迁移

`data/` 包括数据库、文件、工作区、凭据、vault.key 和可选技能，均被 Git 忽略。停服务后备份整个目录，必须包含 `vault.key`。

```powershell
# 先停后端，明确选择包含 dev.db 和 public/ 的目录
python scripts/migrate_myself.py --source <myself目录>
python scripts/migrate_myself.py --source <myself目录> --apply
python scripts/install_zine.py
```

重复迁移保留现有记录，不覆盖之后的编辑。不导入旧账号、密码、会话、`.env`，不抓取外部图片。运行时不依赖迁移源。拾景技能只供个人非商业使用，安装在忽略目录；图像转换需另配兼容 `images/edits`、返回 `b64_json` 的图像服务。

## 验证

```powershell
python -m pip install -e '.[test]'
python -m pytest -q
```

离线模型替身测试真实 Tina 工具循环，不消耗模型费用或连接真实服务器。[API 契约](docs/openapi.json)、[需求](docs/product-spec.md)、[来源许可](THIRD_PARTY.md)、[双轴审查](docs/code-review.md)。DeepSeek 真正调用与写文件另外验收成功；SSH、SMTP、图像服务及云部署等待相应环境。

邮箱支持绑定单主人、验证码登录及找回密码，QQ SMTP 默认 465 SSL/TLS；授权码在设置中加密保存。没有授权码会明确提示不可用。6 位验证码 10 分钟有效、单次消费、最多 5 次失败、60 秒冷却与持久发送配额；重置密码撤销旧会话。

[2 核 4GB 部署准备](docs/deployment.md)：默认单个任务执行，其余持久排队。容器与 TLS 配置已准备，未在服务器实际运行。
