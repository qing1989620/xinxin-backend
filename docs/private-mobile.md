# 手机私有连接

2026-10-18，Asia/Shanghai。主人选择保留杭州按量实例，通过 Tailscale 供自己的手机访问；偶尔需要 Clash，可以轮流切换。需求见 [private-mobile-spec.md](private-mobile-spec.md)。

## 连接方式

手机和服务器加入主人自己的 Tailscale 网络，Xinxin 通过该网络的 HTTPS 地址访问。手机换 Wi-Fi 或切到移动流量时，私有地址保持不变；连接可能短暂中断后恢复，云端任务继续运行。上传需完成到保存成功，当前支持整文件幂等重试，不是跨应用重启断点续传。

Tailscale Serve 仅供同一私有网络内、被访问规则允许的设备使用。加入网络后仍需 Xinxin 主人账号登录；不把网络身份当作产品登录，也不启用 Funnel 公网发布。域名的公共 DNS 及原 ICP 阻塞不承担本次私有访问流量。

服务器端采用官方签名 apt 源安装 Tailscale 1.104.1，保留包管理器维护。`deploy/tailscaled-xinxin.conf` 限制服务内存 256MiB / CPU50%，关闭设备连接诊断上传。默认不接收其他节点 DNS 或路由，不启用出口节点、子网转发或 Tailscale SSH，现有部署 SSH 不改。

## 配置顺序

1. 在服务器安装 Tailscale 官方 Ubuntu 22.04 包，启用 `tailscaled`。将资源限制文件安装到 `/etc/systemd/system/tailscaled.service.d/xinxin.conf`，执行 daemon-reload 和服务重启。
2. 执行 `tailscale up --hostname=xinxin-cloud --accept-dns=false --accept-routes=false --ssh=false`，由主人打开一次性登录链接，把服务器加入自己的个人网络；不要将登录链接、认证密钥或 Tailscale 状态文件提交 Git。
3. 手机安装官方 Android 客户端，登录同一账号，允许建立 VPN 连接。官方客户端要求 Android 8.0 或更新；Xinxin APK 本身最低 Android 7.0，两者要求分别检查。确认控制台只有自己的授权设备，按需要启用设备审批和最小访问规则。
4. 前端的 `deploy/Caddyfile.tailnet` 部署为 `/etc/xinxin/Caddyfile`，先 validate 再 reload。网关只监听 `127.0.0.1:8088`，API仍为 `127.0.0.1:8000`，静态目录仅前端发布文件。原公网配置保存在服务器私人备份目录，切换失败可以恢复。
5. 执行 `tailscale serve --bg --https=443 http://127.0.0.1:8088`，若提示启用 HTTPS，由主人在自己的控制台完成设置。用 `tailscale serve status` 核对显示为 tailnet only，并确认没有 Funnel。
6. 从 `tailscale status --json` 获取实际机器 DNS 名，把该精确 HTTPS origin 追加到 `/etc/xinxin/backend.env` 的 `XINXIN_CORS_ORIGINS`，保留原生 `https://localhost,http://localhost,capacitor://localhost`，不使用通配符。结束活动任务后重启 API，使配置生效。
7. 在 Xinxin 连接页填写实际私有 HTTPS 地址。现有 APK 支持任意有效 HTTPS 后端，不需要放宽清文或关闭证书校验。构建预填私有地址的新发布包时，沿用本机既有发布签名和前端构建脚本的 BackendUrl 参数；密钥不离开本机。

## 手机使用

开启 Tailscale，再打开 Xinxin；无需电脑在线，无需与服务器处于同一 Wi-Fi。保留正常互联网路由，不选择出口节点。第一次填写私有地址后会保留设置，并按原主人账号登录。关闭私有连接后，无法访问此服务是预期行为。

Android 同一时间只有一个系统 VPN 通道。开启 Clash 会断开 Tailscale，此时 Xinxin 暂时无法连接；切回 Tailscale 后再获取任务结果。当前未实现两者同时运行。云端任务不因手机切换或离线自动停止。

## 验证和运行维护

已校验私有网关配置、回环健康检查、任意私有Host静态页面、私人API401、no-store与回环绑定；实际私有origin和原生origin预检通过，无关origin被拒绝。前端10项测试通过。服务器已绑定主人账号，当前等待主人在Tailscale中启用Serve/HTTPS；另一私有设备的实际连通、APK新默认地址和实体手机换网仍待完成。前期真实云端Agent/备份恢复证据属于服务器内部验收，不能替代这些测试。

```bash
systemctl status tailscaled xinxin-web xinxin-backend
tailscale status
tailscale serve status
tailscale netcheck
```

服务重启不等于重新注册设备；保留 `/var/lib/tailscale`，丢失时在主人账号下重新绑定。该目录属于网络凭据，不能放入 Xinxin 静态目录或 Git。Xinxin 私人数据与定时备份继续使用 `/var/lib/xinxin` 和 `/var/backups/xinxin`，详见 [云端恢复](cloud-deployment.md)。服务器备份目前没有异地副本。

参考：[Android 安装](https://tailscale.com/docs/install/android)、[Serve 私有 HTTPS](https://tailscale.com/docs/features/tailscale-serve)、[切换网络时地址保持](https://tailscale.com/docs/concepts/tailscale-ip-addresses)、[其他 VPN 兼容限制](https://tailscale.com/docs/reference/faq/other-vpns)。
