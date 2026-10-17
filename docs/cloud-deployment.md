# 云端部署与恢复

2026-10-18，Asia/Shanghai。用户已确认电脑和旧 Android 局域网包验收通过，授权部署和分别推送前后端。运行代码版本为 `68f4fd9`，云端域名 `xinxin.zhixuedu.cn`。

## 当前结论

后端、静态前端、有效 TLS 证书、开机启动、失败重启、资源限制、定时备份均已部署。真实 TLS 回环验收通过登录、401、Android CORS、39 项技能和 E5、上传重试/下载、Agent 本机命令与产物、固定主机公钥 SSH、附件读取及生日。服务重启后会话和文件保留；实际备份恢复验证通过。证据见 [cloud-runtime.json](cloud-runtime.json)、[cloud-recovery.json](cloud-recovery.json)。

此前公网返回阿里云 `Non-compliance ICP Filing` 页面，用户确认域名尚未备案。HTTP为上游403，HTTPS被上游中断；当时服务器内使用相同域名校验证书并访问正常。主人最新要求保留按量付费实例，改用自己的手机通过Tailscale私有网络访问，详见 [手机私有连接](private-mobile.md)。Tailscale已安装并绑定账号，网关改为只监听回环8088，精确CORS已验证；当前等待主人启用Serve/HTTPS和实际手机验收。不能把先前服务内TLS测试当作私有手机连接已通过。

阿里云要求用于备案的 ECS 为中国内地、具有公网带宽的包年包月实例，购买时长满足其条件。当前实例为按量付费；备案前须按控制台核验或转换资源，转换可能涉及付费，由主人自行决定。参考：[备案前期准备](https://help.aliyun.com/zh/icp-filing/basic-icp-service/user-guide/overview)、[访问阻断说明](https://help.aliyun.com/en/icp-filing/basic-icp-service/support/web-site-suddenly-appeared-for-the-record-to-block-or-hang-and-so-on-and-so-forth)。

## 运行布局

| 内容 | 云端位置 / 服务 |
| --- | --- |
| 后端运行代码 | `/opt/xinxin/backend`，指向版本化 release |
| 独立 Python 3.12 | `/opt/xinxin/venv`，运行依赖固定版本、逐包校验 |
| 私人数据与模型 | `/var/lib/xinxin`，主人为 `xinxin`，目录 0700、文件 0600 |
| 后端 API | `xinxin-backend.service`，仅 `127.0.0.1:8000` |
| Web 网关 | `xinxin-web.service`，独立 `xinxin-web` 用户，当前仅 `127.0.0.1:8088` |
| 私有网络 / TLS | `tailscaled.service`，已绑定账号；等待主人启用Serve/HTTPS |
| 私人备份 | `/var/backups/xinxin`，`xinxin` 用户、0700，归档 0600 |
| 每日备份 | `xinxin-backup.timer`，上海时间 04:20 加最多 15 分钟随机延迟，保留最近 7 份成功归档 |
| 有限日志 | journald 命名空间 `xinxin`，128MB / 14 天；Tina 私人文件日志另由 logrotate 管理 |

本机 Docker Hub 连接失败，因此平台使用 systemd + 独立 Python 部署。Python 3.12.15、53 个 Linux 运行 wheel 的公开来源与哈希已核验，`pip check` 通过；Caddy 2.11.7 的上游校验值核验通过。Docker Engine / Compose 已另行安装供主人授权任务使用；Docker Hub 网络仍是部署其他容器项目时需要单独处理的外部依赖，不声称任意仓库已经验收。

既有宝塔服务保留。API 1536MiB / 150% CPU，Web 256MiB / 40% CPU，单任务执行。实测服务内存约 API 612MiB、Web 39MiB，宿主剩余可用约 2.4GiB；是当前样本，不是大项目构建的峰值承诺。代码目录只读；Tina 相对日志写入私人工作目录，通过 `PYTHONPATH` 导入固定代码。

## Agent 的云端权限

普通“本机”命令现在运行在云服务器 `xinxin` 账号中。已登记“杭州云服务器（管理权限）”，经回环 SSH、固定真实主机公钥连接 root；专用私钥加密保存在私人 vault，授权公钥限制来源为 127.0.0.1。只有任务明确选择该服务器并给 full 权限才可调用 SSH 工具。后台进程没有加入 Docker 组，没有通过挂载 Docker socket 获得隐式管理员权限。

只读 / 工作区权限仍拒绝 shell 和 SSH；任务权限、预算与服务器在创建时固定。云端命令不会自动控制原 Windows 电脑，通用桌面鼠标/屏幕自动化也没有被描述为已实现。

## 日常维护

```bash
systemctl status xinxin-backend xinxin-web
journalctl --namespace=xinxin -u xinxin-backend --since '20 minutes ago'
systemctl restart xinxin-backend
systemctl reload xinxin-web
systemctl start xinxin-backup.service
systemctl list-timers xinxin-backup.timer
```

`systemctl restart` 前先结束活动任务；重启不会自动重做副作用，未完成任务标中断。不要同时运行多个 API worker。原始聊天、凭据、私人数据库不进入源码、镜像、静态目录或安装包。备份只保存在本机云盘，尚未配置异地灾备或云快照。

## 恢复

1. 结束任务并停止 `xinxin-backend`。选定一份成功的 `xinxin-*.tar.gz` 归档，保留当前数据目录，不直接覆盖。
2. 将归档以 Python `tarfile.extractall(..., filter='data')` 解到新建的私人临时目录。验证 SQLite `PRAGMA integrity_check`、vault 存在、每个数据库登记文件的大小/SHA-256，并用恢复的 vault 验证配置密文可解密。实际操作样本已通过，见恢复证据。
3. 同机恢复时把原 `/var/lib/xinxin` 改为有日期的备份名，再把恢复的 `data` 放回该绝对路径；所有者恢复为 `xinxin:xinxin`，目录 0700、文件 0600，最后启动 API。不要先删掉原件。
4. 换机器或数据根路径时先运行 `scripts/prepare_cloud_data.py` 重写路径；SSH 服务器换主机后重新核验主机公钥，不静默跳过校验。E5 可按固定 manifest 重新安装，不包含在每日归档；私人技能包含在备份。

在线归档核验数据库和登记资源后才发布或轮换；缺钥、并发删除、字节变化会使当次备份失败并保留旧归档。运行中任务的工作区不保证原子快照，精确整目录恢复应先停服务备份。

## 待外部完成

主人绑定 Tailscale 账号、私有 HTTPS 和手机换网复测；新 Release APK 的实体手机安装、分享、网络恢复；QQ SMTP 授权与实际送达；图片生成服务；其他目标仓库的部署/回滚。若将来改为内地公开网站，仍需另行备案/接入。已验证的服务内 TLS/SSH 功能不替代这些验收。
