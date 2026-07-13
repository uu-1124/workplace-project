# 统一发布门禁 GitHub App 配置

## 目的

根仓库 `workplace-project` 的发布门禁需要读取固定版本的两个私有子仓库：

- `workplace-api`
- `workplace-desktop-electron`

仓库自带的 `GITHUB_TOKEN` 不能跨私有仓库读取，因此统一发布门禁使用专用 GitHub App 生成一小时内有效的安装访问令牌。令牌只授予 `Contents: Read-only`，不具备写代码、管理仓库、读取 Actions Secret 或发布 Release 的权限。

## GitHub App 最小配置

在 GitHub 的 **Settings -> Developer settings -> GitHub Apps -> New GitHub App** 创建专用 App。

建议值：

| 配置项 | 值 |
| --- | --- |
| GitHub App name | `SoloOps Release Gate`，如名称被占用可增加账号后缀 |
| Homepage URL | 根仓库页面 |
| Webhook | 关闭 `Active`；本门禁不接收 webhook |
| Repository permissions / Contents | `Read-only` |
| Repository permissions / Metadata | GitHub 默认只读 |
| Account permissions | 全部保持 `No access` |
| Where can this GitHub App be installed? | `Only on this account` |

创建完成后：

1. 将 App 安装到账号 `uu-1124`。
2. 选择 **Only select repositories**。
3. 仅选择：
   - `workplace-project`
   - `workplace-api`
   - `workplace-desktop-electron`
4. 在 App 页面生成 Private Key，浏览器会下载 `.pem` 文件。
5. 不要把 `.pem` 放入任何项目目录，不要提交到 Git。

## 根仓库 Actions 配置

打开 `workplace-project -> Settings -> Secrets and variables -> Actions`。

### Repository variable

新增普通变量：

```text
RELEASE_APP_CLIENT_ID
```

值为 GitHub App 页面显示的 **Client ID**。它不是 App ID。

### Repository secret

新增 Secret：

```text
RELEASE_APP_PRIVATE_KEY
```

值为下载的 `.pem` 文件完整内容，包括：

```text
-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
```

GitHub 也可能生成 `BEGIN PRIVATE KEY` 格式，两种格式均应按文件原文完整保存。

## `main` 发布门禁

`release-readiness-gate` 保留手工触发，并已最小接入 `main` 分支的发布相关路径触发：只在统一 workflow、`.gitmodules`、发布清单、发布验证脚本或两个子模块 gitlink 变化时自动运行。普通文档提交不消耗完整门禁资源。该自动触发已由 2026-07-13 的首次 `main` push 完整绿灯验证。

配置完成后：

1. 打开根仓库 **Actions**。
2. 选择 `release-readiness-gate`。
3. 选择 `Run workflow`，分支使用 `main`。
4. 等待两个 job 完成：
   - `PostgreSQL and phase-one UI gate`
   - `Windows packaged application and installer gate`

首次完整手工远端执行已于 2026-07-13 通过：`release-readiness-gate #7`（run `29218981517`，Root `2e7055d`）。随后 Root `e4bdcbe` 的发布相关 workflow 变更推送到 `main`，自动创建 `release-readiness-gate #8`（run `29220005160`），页面记录 `Triggered via push` 与 `on: push`，并在 11 分 19 秒内完整成功：Linux job `86723116709`（6 分 03 秒，诊断构件 `8267907140`，digest `sha256:46e13738034948499d76109bdd2964eeb9176bbef553f73d2b548fd84a681a72`）和 Windows job `86723712676`（5 分 11 秒，79.3 MB 安装器构件 `8267960570`，digest `sha256:482bc83924493696f8a4fc0d835ecfc0fbf3e6f8d06295545a1b77a63bd66ca1`）均成功。该证据证明 `main` 发布相关路径自动触发已经生效，但不证明 PR 门禁已建立；私有仓库 PR 凭证策略、多个独立发布变更的重复绿灯及 R0 整体仍不得提前标记完成。

## PR 门禁信任边界

根仓库公开、API 与 Desktop 仓库私有，不能把现有发布 job 原样挂到 `pull_request` 或 `pull_request_target`：

- fork PR 的普通 `pull_request` 拿不到读取私有子仓库所需的 Secret；
- `pull_request_target` 虽能取得 Secret，但如果检出并运行 PR 代码，会形成 GitHub 官方明确警告的 `pwn request`；
- 在公开根仓库中直接上传私有源码 artifact，即使测试 job 没有 Secret，也会把两个私有仓库完整泄露给 artifact 下载者；
- 在同一个 runner job 中先使用私钥、再清空环境变量并运行候选代码，不构成物理隔离，候选进程仍可能读取 runner 内存或残留状态。

`pr-release-readiness-gate` 因此采用以下固定边界：

1. `Base-controlled trust policy` 使用 `pull_request_target` 的 base commit workflow 与 base commit 脚本，只读取 PR Git 对象，不运行 PR 文件，也不引用任何发布 Secret。
2. 普通文档 PR 只通过上述无密钥策略检查，不读取私有仓库、不消耗完整双平台门禁。
3. 发布清单、两个 gitlink 或经管理员显式标记的 CI 变更进入 `release-pr-trust` Environment，必须先由 required reviewer 批准。
4. `Approved private source broker` 是唯一能看到 App 私钥和短期安装令牌的 job；它只检出、校验和归档代码，不运行候选仓库脚本。
5. API 与 Desktop 候选 SHA 必须从当前根基线向前推进，并且已经位于各自私有仓库的 `main` 历史中；PR 不能选择回退版本、私有临时分支或任意孤立提交。
6. broker 只把候选 `release-manifest.json` 当数据读取，私有源码使用一次性随机密钥进行 AES-256-GCM 加密后才上传；公开 artifact 中没有明文私有源码。
7. broker job 完成及 App token post-action 撤销后，Linux 与 Windows job 才接收一次性解密密钥。密钥只存在于解密 step，后续候选代码进程没有 App 私钥、安装令牌或仓库写权限。
8. `.github/**`、`scripts/**` 和 `.gitmodules` 默认属于特权路径。普通 PR 修改会直接失败；确需修改 CI 时，仓库管理员必须添加 `trusted-ci-change` 标签并再次通过 Environment 人工审批。`.gitmodules` 的两个仓库和路径仍不允许通过标签改写。
9. 所有根仓库 GitHub-owned Action 均固定到完整 40 位 commit SHA；启用仓库级 SHA pin 约束后，tag 漂移不能改变已审查 workflow 的实际 Action 代码。

### 外部配置顺序

代码推送后必须按以下顺序配置，顺序不能颠倒：

1. 创建 Environment `release-pr-trust`。
2. 将当前仓库管理员设为 required reviewer；当前只有一个维护者，因此 `prevent_self_review` 必须保持关闭，否则所有发布 PR 会永久锁死。
3. Environment 只允许受保护分支部署；审批发生在 broker job 启动和 Secret 可用之前。
4. 创建标签 `trusted-ci-change`，只用于少量 CI / 门禁脚本变更，不用于普通产品代码或文档。
5. 将 Actions 允许范围收紧为 GitHub-owned Action，并要求完整 SHA pin。
6. 最后才创建仓库变量 `PR_RELEASE_ENVIRONMENT_CONFIGURED=true`。如果该变量缺失，即使有人提前打开发布 PR，broker 也不会启动，最终 required check 会明确失败。
7. 用真实文档 PR 验证无密钥快速路径，再用真实发布清单 PR 验证 Environment 审批、加密 broker、Linux/PostgreSQL、Windows 安装器与最终 `Required PR gate`。
8. 只有上述两类 PR 都成功后，才能为 `main` 启用“必须通过 PR”“必须为最新 base”“Required PR gate”“禁止 force push / delete”并对管理员生效。

本节描述的是实现和配置契约，不是远端成功证据。Environment、真实 PR、required check 和主分支保护任一项尚未验证时，R0 的 PR 信任边界都不得关闭。

## 门禁证明范围

统一 workflow 使用同一个根提交及其固定 gitlink，验证：

- 发布清单与 Root、API、Desktop commit 一致；
- API 全量回归；
- PostgreSQL 从零迁移；
- PostgreSQL-backed API 部署 smoke；
- 第一阶段 Desktop UI 对 PostgreSQL API 的主链 smoke；
- 当前 Windows 解包应用构建、启动和业务恢复；
- 固定旧版与当前版 NSIS 安装器构建；
- 安装后业务恢复；
- `2.0.0 -> 2.0.1` 覆盖升级；
- 客户数据升级后保留；
- 升级后故障注入、失败检测和原位回滚；
- 回滚应用启动、回滚后客户数据保留和卸载；
- 安装器和诊断构件上传。

它仍然不能证明：

- 真实供应商模型链路；
- 正式企业代码签名；
- API 进程退出后的任务续跑；
- 独立 worker、持久任务队列、多实例协调或高可用；
- 已安装的正式 EXE 直接驱动 PostgreSQL 与真实供应商模型的完整隔离全链。

以上项目必须在对应的 R1-R3 工作项中继续保留，不得因本 workflow 通过而提前关闭。
