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

## 首次激活

当前 workflow 仅允许手工触发，避免凭证尚未配置时，每次提交都制造无意义红灯。

配置完成后：

1. 打开根仓库 **Actions**。
2. 选择 `release-readiness-gate`。
3. 选择 `Run workflow`，分支使用 `main`。
4. 等待两个 job 完成：
   - `PostgreSQL and phase-one UI gate`
   - `Windows packaged application and installer gate`

首次远端执行通过后，才允许把 workflow 扩展为 `push` / `pull_request` 持续门禁。在首次绿灯前，不得把 R0 标记完成。

## 门禁证明范围

统一 workflow 使用同一个根提交及其固定 gitlink，验证：

- 发布清单与 Root、API、Desktop commit 一致；
- API 全量回归；
- PostgreSQL 从零迁移；
- PostgreSQL-backed API 部署 smoke；
- 第一阶段 Desktop UI 对 PostgreSQL API 的主链 smoke；
- Windows 解包应用构建和启动；
- NSIS 安装器构建；
- 安装、启动、卸载生命周期；
- 安装器和诊断构件上传。

它仍然不能证明：

- 真实供应商模型链路；
- 代码签名；
- 老版本覆盖升级；
- 升级失败回滚；
- 客户数据保留；
- 已安装的正式 EXE 直接驱动 PostgreSQL/真实模型全链。

以上项目必须在 R1/R2 中继续保留，不得因本 workflow 通过而提前关闭。
