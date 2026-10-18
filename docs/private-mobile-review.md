# 私有手机接入双轴审查

2026-10-18，Asia/Shanghai。使用主人指定的 [mattpocock code-review](https://github.com/mattpocock/skills/blob/49dd158d1076134a641b33efb035946536778336/skills/engineering/code-review/SKILL.md)。两个独立只读审查代理，固定比较 `68f4fd9...36f46ad`，后续复验 `36f46ad...537bdd1`；需求 `private-mobile-spec.md`，保留原 `cloud-deployment-spec.md` 的部署与恢复要求。

## Standards

两轮均为硬规则违反0、可能气味0。配置、部署helper、运维文档与脱敏证据属于后端范围，没有混入前端源码、私人原文、凭据或签名密钥。

Aliyun兼容helper限定默认网卡、指定的厂商内部地址和ESTABLISHED/RELATED回包；插入前检查存在性，不删除原CGNAT DROP，不允许这些来源发起新连接，不改DHCP DNS或默认路由。drop-in保留资源限制，按network-online排序并在启动后应用helper。原始授权链接、网络状态私钥和账号身份不进入Git。

运行证据明确服务自身通过Tailscale地址发起，私有名称来自认证节点映射，标准证书/域名验证保持开启；没有把它写作另一节点或实体手机验收。Standards审查没有操作防火墙或远端，实际运行证据由实施方独立提供。

## Spec

初查1项P2：恢复说明错误地要求把解压归档交给源迁移脚本。在线归档可以包含活动任务状态，且数据库文件路径仍指向原数据根，源迁移脚本会拒绝这样的离线归档副本。独立合成样本复现；同根路径恢复不受影响。

已修复说明：换机器归档恢复沿用 `/var/lib/xinxin`，保留原绝对文件路径；明确源迁移脚本不处理提取归档，改变根路径需另行迁移验证。原问题独立复验解决；后续helper/drop-in与需求一致，没有新增具体缺陷或范围扩张。

Spec剩余具体缺陷0。外部依赖的另一节点、手机换网/分享/键盘测试保留待验收，不作为已通过，也没有将已明确记录的待验收项冒充代码缺陷。

## 实际验证边界

服务内真实Tailscale HTTPS、证书链和域名、401/短期验收会话、精确CORS、39项技能、上传幂等/认证下载、真实DeepSeek与SSE回放、任务幂等、APK MIME与字节散列通过。服务重启后阿里云DNS及内网包镜像恢复，例外规则恰为两条且位于原DROP前。当前没有执行整机重启；服务开机启用与依赖顺序已核对。

前端10项测试、生产/Release构建和签名的部分审查在前端独立仓库记录。后端API源码仍为完整84项回归已通过的版本，本轮新增部署脚本通过shell语法和上述真实环境检查。证据见 [private-mobile-runtime.json](private-mobile-runtime.json)；原备份审查和恢复证据见 [cloud-review.md](cloud-review.md)、[cloud-recovery.json](cloud-recovery.json)。

Standards：硬违反0、气味0；Spec：初始P2共1项已解决，剩余0。
