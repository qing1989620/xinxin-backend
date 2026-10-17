# 单主人部署准备（2 CPU / 4 GB）

默认只有一个 API 进程、一个同时执行的任务；其他任务持久排队。模型通过 DeepSeek 远程调用，不在机器加载大模型。图表按需加载 matplotlib/pandas，最多读取 10 万行 CSV；柱状图最多 200 行。知识库采用本地词法检索，适合个人规模；大量长文档需另作容量压测。

容器镜像只包含本仓库 `app/`、`vendor/`、`resources/`、`scripts/` 和锁定依赖。数据目录挂载 `/var/lib/xinxin`，包含数据库、文件、密钥与技能；不要打进镜像、提交或公开下载。密钥的解密依赖同目录 `vault.key`，备份时必须一起保留。

集成目录的 `compose.yaml` 分别从两个独立仓库构建，前端用 Caddy 托管静态页面并代理 API/SSE，后端不映射公网端口。API 内存上限 1536 MB，前端 256 MB；总共给宿主与其他任务保留约 2 GB。默认并发 1、BLAS 单线程；完整权限工具具有后端进程账号的权限，容器内仅能修改挂载的私人数据和工作区，远程服务器通过 SSH 单独授权。

## 等服务器准备好后

1. 安装 Docker Engine/Compose，放入前后端仓库及集成 compose。可先在本地构建镜像再传服务器，减少 2 核机器上构建造成的争用。
2. 域名解析到服务器，开放 80/443；设置 `XINXIN_SITE_ADDRESS=你的域名`，执行 `docker compose up -d --build`。示例默认 `http://localhost` 仅供本机使用，公网与手机需有效 HTTPS。
3. 选择迁移当前 `data/` 或在新数据卷首次初始化；迁移前停止后端，完整复制，设置目录所有者 UID/GID 10001。不要同时运行两个 API 访问同一数据卷。
4. 在自己的设置页配置模型、SMTP、图像服务。邮箱用 QQ SMTP 授权码，465 SSL/TLS 或 587 STARTTLS，证书验证始终开启。未配置 SMTP 不发送验证码；只在点击发送时发送邮件。
5. 验收 HTTPS 登录、Agent 工具执行、SSH 主机公钥、事件重连、文件上传下载、重启中断、定时任务、APK 连接、PWA 安装与 SMTP 送达，再开始长期运行。

本机尚无 Docker、云服务器和真机，因此这些部署文件没有被描述为线上验收。参考：[Compose 服务资源限制](https://docs.docker.com/reference/compose-file/services/#mem_limit)、[Caddy SSE 代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#streaming)、[Python TLS SMTP](https://docs.python.org/3.12/library/smtplib.html)。

单人 API 对话、资料整理和轻量绘图预计适合 2 核 4 GB。大型依赖安装、多个网站构建、容器编译或视频处理可能占满 CPU/内存；由任务本身决定，不能用平台的空闲占用推断这些负载。购买后先按实际项目压测，暂不需要为远程大模型购买 GPU。
