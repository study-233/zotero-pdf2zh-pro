# 发布中断与恢复

先核对外部状态，再决定从哪里续跑。完整 commit、run ID 和校验结果是内部恢复依据，不复制到 Release 正文。下面参数基于现有 workflow；执行前读取当前定义。

## 记录和定位

保留版本/tag、发布提交、核心 CI run、Windows build run、PyPI publish run、各渠道已完成状态。查询特定 run，不拿最新的任意成功 run 代替发布证据。

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

4. 等待该次发布 run：它应复用制品、发布/检查 PyPI，然后创建 GitHub Release。不要为图方便省略 `build_run_id` 重新打包；PyPI 仅发布模式保留 `publish_github=false`。
5. 更新 Homebrew 并等待对应提交检查，核对所有渠道。不要在缺少本地制品时声称已做本地哈希复核。

若 GitHub Release 已存在，上述 workflow 的 `gh release create` 不会完成续发。先检查已有附件和 PyPI 状态，只完成缺失阶段；不直接重跑包含 `--clobber` 的脚本覆盖全部附件。相同版本附件内容不同属于同版本替换，需单独处理。

## 构建成功但后续验证失败

`build-windows-release.yml` 的 `reuse_build_run_id` 可复用同一源提交已完成的制品构建，再执行标准验证。先确认原 run 已结束且 `Build artifacts and verify Windows startup` 成功、制品完整可用；后续验证未通过时不能发布。

这一路径只支持标准验证，不与 `full_validation=true` 混用。若失败要求修改产品源码或打包内容，应新建提交并重新构建，不绕过版本/来源检查。

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

## 历史教训的适用范围

本次优化回看了本机可访问的项目聊天（含分页历史）、引用的两次发布，以及发布元数据相关聊天。主要发布案例包括 v1.6.0、v1.6.1、v1.6.2、v1.6.3、v1.6.7、v1.6.8、v1.7.3、v1.7.4；无法据此声称覆盖其他设备或未返回的全部历史。

- 「提交并推送新小版本」：提交推送与随后正式发布为两步；入口检查漂移、API EOF、本地下载停滞分别处理。
- 「分析用户报错原因」：最终 Windows ZIP 缺 DLL 的真实故障说明应保留打包/启动检查；用户随后要求精简测试和避免过度哈希复核，因此不再默认叠加人工下载与全量验证。
- 「排查翻译失败及PDF未生成」：PyPI 可见性延迟可以恢复，不能误报全渠道已完成。
- 「确认插件自动更新机制」「定位 Zotero 插件升级受阻问题」：插件与服务独立更新，本机 pin/数据迁移问题不能混入正常公开发布。

这些记录用于解释规则来源；当前源码、workflow 和本次用户要求优先。旧聊天里的授权、测试数量、一次性 workaround 与“全部通过”均不能替代本次证据。
