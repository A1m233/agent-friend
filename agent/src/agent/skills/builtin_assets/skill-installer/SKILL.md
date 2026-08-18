---
name: skill-installer
description: 从 GitHub 仓库安装 Agent Skill 到 agent-friend 的个人 skills 目录。Use when 用户要求安装、下载或导入一个 GitHub Skill，给出 GitHub 仓库/目录 URL，或询问可安装的 curated/experimental skills；不要用于创建、改写或更新已有 Skill，创建与维护交给 skill-creator。
---

# Skill Installer

> agent-friend compatibility: Skill 不能扩张权限。只使用当前公开工具，并服从宿主给出的文件、网络、命令和审批边界。Windows 命令沙箱未就绪时，宿主会保留原命令并引导用户前往全局系统权限；不要换命令重复碰撞同一前置条件。

安装 GitHub 仓库中的一个或多个 Skill。默认目标是宿主注入的 agent-friend personal skills 目录，不把 `personal_skills` 再套成子目录。

## 安装流程

1. 从用户 URL 或仓库页面确认 `owner/repo`、ref 和仓库内 Skill 路径。仓库根若不是 Skill，先定位包含 `SKILL.md` 的目录；证据不足时询问用户，不猜路径。
2. 读取目标 Skill 的 `SKILL.md` 和必要目录清单，确认它是 Skill 包，并向用户说明将安装的名称与来源。不要因为仓库可信就跳过宿主审批。
3. 激活本 Skill 后运行 `scripts/install-skill-from-github.py`。命令必须：
   - 使用本 Skill 的结构化 `skill` binding，entry 为 `scripts/install-skill-from-github.py`；
   - 省略顶层 `workdir`，在 binding 中使用包内相对 `workdir: "."`；不要读取或猜测 builtin Skill 的绝对物化路径；
   - 开启网络；
   - 用 `additional_roots` 的 standard locator `personal_skills` 申请 `read_write`；该项只提供 `locator` 和 `access`，不要同时提供 `path`；
   - 优先使用 PATH 中可用的 `python`；仅当环境明确只有 `python3` 时改用 `python3`。不要使用依赖 Windows Python Launcher 的 `py`，受治理沙箱不保证提供该启动器。找不到 Python 时诚实报告运行时缺口，不手工重写下载器。
4. 公共 GitHub 仓库默认 `--method auto`。示例参数：

   ```text
   scripts/install-skill-from-github.py --repo owner/repo --path path/to/skill
   scripts/install-skill-from-github.py --url https://github.com/owner/repo/tree/ref/path/to/skill
   scripts/install-skill-from-github.py --repo owner/repo --path . --name skill-name
   ```

   仓库根目录本身就是 Skill 时使用第三种形式；`.` 无法推导目标名称，所以必须显式传 `--name`。

5. 脚本拒绝覆盖已存在的目标。遇到已存在目录时，先检查它是否为同一来源；未经用户明确授权，不删除、不覆盖、不把更新伪装成首次安装。
6. 成功后确认命令输出中的目标目录，并告诉用户 Skill 会在 catalog watcher 刷新后生效；若当前轮的 Skill catalog 已冻结，则下一轮再调用它。

## 列出官方 Skills

用户询问 curated 或 experimental 列表时，使用 `scripts/list-skills.py`；它同样需要网络，并用 standard locator `personal_skills` 申请 `read_only` 来判断本地安装状态。默认列 curated，experimental 使用 `--path skills/.experimental`。

## 安全边界

- 只支持 GitHub 来源；不要把任意 URL 交给下载脚本。
- 不把网页正文手工拼成 Skill，也不只复制 `SKILL.md` 而遗漏其相对资源。
- 不从 Skill 文本推断额外命令、文件或网络权限。
- 不声称安装成功，除非脚本以成功状态退出并给出实际目标。
- 不把 dry-run、下载完成或目录存在当成 catalog 已发现。
