# 发布中断与恢复

先核对外部状态，再决定从哪里续跑。完整 commit、run ID 和校验结果是内部恢复依据，不复制到 Release 正文。下面参数基于现有 workflow；执行前读取当前定义。

## 记录和定位

保留版本/tag、发布提交、核心 CI run、Windows build run、PyPI publish run、各渠道已完成状态。查询特定 run，不拿最新的任意成功 run 代替发布证据。

正常发布由 `release.sh` 创建带 `.keep` 的恢复记录，按 [Actions 测试规范](../../../../docs/actions-testing.md#时间复用与恢复)
使用 `--resume <state.json>`。保留原选项和冻结计划；协调器用 dispatch 标识定位准确 run，不按产品 SHA 猜测工作流 run。
旧记录若没有新验证契约，需要复用原制品补验，不能手工伪造通过回执。

- `gh run watch` 报 `unexpected EOF` 或连接中断：用 `gh run view <run> --json status,conclusion,url` 确认远端是否已完成。仍运行则继续观察；已成功则进入下一阶段，不重建。
- 本地 Actions 制品下载长期无进展：确认下载进程与文件增长，有限重试；本地链路仍失败时可转入下述云端发布，让 runner 下载原制品。
- 非阻塞缓存告警不等于构建失败。相同提交的标准检查通过后，不因一次工具观察中断再跑全套测试。
- 代码或打包输入发生变化：新提交需新证据，不能复用旧提交的构建。维护 workflow 可以来自后续提交，但必须通过现有来源/祖先关系检查和制品 manifest 核验。

## 复用成功的 Windows 构建发布

适用于构建成功、本机下载或发布脚本中断，尚未创建正式 GitHub Release：

1. 验证 run 的仓库、workflow、成功结论及实际源提交。工作流 `head_sha` 不一定等于源提交，核对 `display_title` 与制品 `release-<version>-<commit>`，由发布 workflow 再验证 manifest 的 version/commit/size/hash。
2. 核对本地和远端 tag 解引用后的提交。标签不存在时才为已验证提交创建并推送；相同则继续，不同则停止，不能隐式 force。
3. 调用现有 workflow，变量都从已核对状态得到：

   ```bash
   gh workflow run publish-pypi.yml --repo study-233/zotero-pdf2zh-pro --ref main \
     -f tag="$TAG" -f build_run_id="$BUILD_RUN" -f publish_github=true
   ```

4. 等待该次发布 run：它应复用制品、发布/检查 PyPI，然后创建 GitHub Release。`build_run_id` 为必填，PyPI 仅发布模式也必须提供，并保留 `publish_github=false`。
5. 更新 Homebrew 并等待对应提交检查，核对所有渠道。不要在缺少本地制品时声称已做本地哈希复核。

若 GitHub Release 已存在，共享发布守卫会先核验所有同名附件，再补传缺失项；同名同摘要直接复用，冲突时停止。相同版本附件内容不同属于同版本替换，需单独处理。

历史产品提交与默认分支的工作流文件不同时，创建 Release 时额外指定历史 `--target` 会触发工作流写权限检查，`GITHUB_TOKEN` 无法获得该权限。
共享守卫会先核验已有 tag 的真实提交，再使用 `--verify-tag` 创建 Release；仅 tag 不存在时才指定精确 `--target`。
这保留原产品提交和现有令牌权限。若 PyPI 已完成而 GitHub 创建失败，沿用原制品续接该发布阶段。
依据：[GitHub Release API 权限与 target 语义](https://docs.github.com/en/rest/releases/releases#create-a-release)。

## 远端制品已成功，本机扩展验证未完成

Windows 的扩展选测会在远端完成所选生命周期后，下载最终制品并在本机补历史升级。
这一步中断不代表远端构建失败，也未授权跳过扩展验证。用原恢复记录运行 `--resume`；
入口重新核验远端事实与制品摘要，并复用相符的本机回执。需要单独执行时，对
`test_windows_release_extended.ps1` 传入 `-Plan <冻结计划>`、`-SourceCommit <产品提交>`
和 `-Receipt <发布记录旁的 local-validation.json> -SkipLifecycle -SkipRecentUpgrade`。
这两个跳过参数对应远端已通过的场景；单独做完整验收时不使用。日志保存在 `.local-dev/reports/`。
云端发布要求本机扩展时必须传 `local_validation` 回执；仅构建成功不足以跳过该检查。

若只修复了验收脚本，保留原制品与源提交；本机加 `-SourceCommit <原制品提交>`，
记录会分别保存源提交和验收脚本提交。远端复用流程从维护 workflow 取明确列出的
验收脚本及其依赖，其他产品源码保持原提交；不要把验收修复变成安装包重建。

若本机条件不足，使用同一源提交的 `reuse_build_run_id` 补跑远端扩展验证；不能只跑
标准检查便宣称扩展验证完成。若修复涉及产品或打包输入，则新提交需要新制品。

## 构建成功但后续验证失败

`build-windows-release.yml` 的 `reuse_build_run_id` 可复用同一源提交已完成的制品构建，再执行所选的标准或扩展验证。先确认原 run 已结束且 `Build artifacts and verify Windows startup` 成功、制品完整可用；后续验证未通过时不能发布。

复用制品时仍须完成所选验证，不能用旧的成功打包结果替代后续检查。若失败要求修改产品源码或打包内容，应新建提交并重新构建，不绕过版本/来源检查。

若标准验证及 `Record passed standard validation` 均成功，后续历史升级或归档失败，
可在复用原 `reuse_build_run_id` 时另传 `validation_run_id=<已通过标准测试的 run>`。
工作流从该 run 下载标准回执，核对 run/attempt、八项制品哈希、冻结计划、源提交及验收代码；
全部匹配后才跳过标准验证，继续所选历史升级和归档，不适用于同版本替换。
旧构建没有回执，或测试脚本、工作流、策略、基线变化时，省略 `validation_run_id`，
生成新计划并对原制品重跑所选检查；不能将旧结果套到新构建或新计划上。
验收脚本仍须在归档前恢复为源提交版本，保留产品改动与干净源码守卫。

## PyPI 延迟和部分成功

- 精确版本的 wheel 与 sdist 都存在且匹配才算完成；只看到项目页或某个文件不够。
- 公开索引暂未显示完整文件时保留相同验证制品，有限等待后重跑必要发布/核验阶段。v1.7.3 曾发生这类延迟；不要因此重新构建同版本包。
- 远端已有文件内容不匹配时停止，不删除或尝试覆盖 PyPI 版本。
- PyPI 成功、GitHub/Homebrew 未完成时只续做剩余渠道，不能提前报告全渠道发布成功。
- 相同原因有限重试后仍无进展，保存 run 链接、失败原因及可恢复阶段并报告阻塞。不要无期限反复 dispatch；也不要通过关掉发布门槛强行完成。

## 同版本客户端替换

仅在用户明确要求替换已公开版本时读取 `scripts/release_replacement.py` 并采用 `--replace-existing <old-commit>`：

- 使用刚核对的原 tag 完整提交作为并发保护，不能从旧记录猜测。
- 必须满足服务端和许可证输入未变、原 PyPI wheel/sdist 完整且不变；不符合则发布新的版本。
- 执行完整验证，保留脚本生成的恢复备份；失败先查看自动恢复结果和各远端当前状态，不盲目重试。
- Homebrew 推送后的回退需要检查实际 tap 状态，不能对公共历史随意强推。
- 正文写明客户端修订和同版本手动更新路径；仍保留机器来源证据，不展示长 SHA。

当前源码、workflow 和本次用户要求决定恢复行为。旧聊天的授权、测试数量与一次性 workaround 不能替代本次证据；插件与服务独立更新，本机 pin 或数据迁移问题应单独诊断。
