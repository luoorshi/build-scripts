# Git 提交与日常拉取操作说明（方案 B）

本文档说明：本地在 `dev`、GitHub 默认分支为 `main` 时，如何用**方案 B**把改动合入 `main` 并推送；以及以后日常「拉取 → 修改 → 提交 → 推送」的标准流程。

> **执行环境**：请在 **Linux**（或能正常访问该仓库、且有 git / ssh 权限的终端）执行下列命令。通过 Samba 挂到 Windows 的路径上，部分 git 操作可能失败，请勿依赖 PowerShell 完成本文档步骤。
>
> **仓库位置**：本说明针对 `build-scripts` 仓库（远程示例：`ssh://git@github.com/luoorshi/build-scripts`）。请在**该仓库根目录**下执行（即含有 `.git` 的目录，也就是本文件所在目录）。

---

## 1. 背景：你当前可能遇到的分支情况

典型输出类似：

```bash
git branch -a
```

```text
* dev
  remotes/github-ssh/main
  remotes/m/main -> github-ssh/main
```

含义简要说明：

| 名称 | 含义 |
|------|------|
| `* dev` | 当前所在本地分支是 `dev` |
| `remotes/github-ssh/main` | 远程名是 **`github-ssh`**（不是常见的 `origin`），远程分支是 **`main`** |
| `remotes/m/main -> github-ssh/main` | `m` 是 repo 工具常见的「当前清单默认远程」别名，指向同一个 `github-ssh/main` |

因此：

- GitHub 上看到的是 **`main`**
- 本地工作可能在 **`dev`**
- 推送时请写远程名 **`github-ssh`**，不要写成 `origin`（除非你自己另加了名为 `origin` 的 remote）

可用下面命令随时确认：

```bash
# 当前分支、工作区是否干净
git status

# 远程列表（重点看名字是不是 github-ssh）
git remote -v

# 本地与远程分支关系
git branch -vv

# 最近提交
git log --oneline --decorate -10
```

---

## 2. 方案 B：切到跟踪 `main`，再合并 `dev` 的改动并推送

**思路**：以远程 `main` 为基准建立（或切换到）本地 `main` → 把 `dev` 上的提交合进来 → 推送到 GitHub 的 `main`。

适合：希望 GitHub 上只维护 `main`，本地临时用过 `dev`，最终仍要回到与 GitHub 一致的主线。

### 2.1 第一步：保存 `dev` 上的未提交改动（若有）

先看工作区：

```bash
git status
```

**情况 A：有未提交修改（modified / untracked）**

先提交到当前分支（通常是 `dev`），避免切换分支时丢失改动：

```bash
# 查看改了什么（建议提交前看一眼）
git diff
git status

# 暂存（按需选择文件；下面是全部纳入）
git add -A

# 提交（消息请改成你自己的说明）
git commit -m "描述本次修改目的，例如：补充 make-img 分区说明"
```

**情况 B：工作区干净，但 `dev` 上已有 commit**

无需再 commit，直接进入 2.2。

**情况 C：改动还不想正式 commit**

可临时贮藏（stash），切分支后再取回：

```bash
git stash push -u -m "wip before switch to main"
# … 切到 main 并处理好合并后 …
git stash pop
```

一般更推荐情况 A：先在 `dev` 上留下清晰 commit，再合并到 `main`。

### 2.2 第二步：拉取远程最新 `main`

```bash
git fetch github-ssh
```

说明：

- `fetch` 只更新本地的远程跟踪分支（如 `github-ssh/main`），**不会**自动改你工作区文件。
- 之后再用 `checkout` / `merge` 把内容真正合进来。

### 2.3 第三步：创建或切换到本地 `main`，并跟踪远程 `main`

**若本地还没有 `main` 分支：**

```bash
git checkout -b main github-ssh/main
```

含义：

- `-b main`：新建本地分支名为 `main`
- `github-ssh/main`：以远程跟踪分支为起点，内容与 GitHub 上的 `main` 对齐
- 之后该本地 `main` 通常会关联到 `github-ssh` 的 `main`（可用 `git branch -vv` 确认）

**若本地已经有 `main` 分支：**

```bash
git checkout main
git pull github-ssh main
```

说明：

- `checkout main`：切到本地主线
- `pull github-ssh main`：把远程 `main` 的新提交拉进当前本地 `main`（等价于 fetch + merge，视配置而定）

若 `git pull` 提示需要设置上游，可先：

```bash
git branch --set-upstream-to=github-ssh/main main
git pull
```

### 2.4 第四步：把 `dev` 上的提交合入 `main`

在**已经位于 `main` 且已与远程同步**的前提下：

```bash
git merge dev
```

说明：

- 把本地 `dev` 相对 `main` 多出来的提交合并进来
- 若无冲突，会生成一次 merge commit，或在可快进（fast-forward）时直接指针前移
- 若有冲突，git 会标出冲突文件，需手动改完后再：

```bash
git status          # 看哪些文件还在冲突
# 编辑冲突文件，去掉 <<<<<<< ======= >>>>>>> 标记，保留正确内容
git add <已解决的文件>
git commit          # 完成合并提交（若 merge 已自动带消息，按提示即可）
```

**可选：用 cherry-pick 代替 merge（仅当你只想拿某几个 commit）**

```bash
# 先在 dev 上看要带走的提交哈希
git log --oneline dev -10

# 切回 main 后，按需挑选
git checkout main
git cherry-pick <commit-hash>
# 多个可用：git cherry-pick <hash1> <hash2>
```

日常整分支合入，优先用 `git merge dev`，更简单、历史更完整。

### 2.5 第五步：推送到 GitHub 的 `main`

```bash
git push -u github-ssh main
```

说明：

- `github-ssh`：远程名（必须与 `git remote -v` 一致）
- `main`：推送到远程的 `main` 分支
- `-u`：设置上游跟踪，以后在该分支上可直接 `git push` / `git pull`

推送成功后，在 GitHub 网页上刷新 `main`，应能看到你的新提交。

### 2.6 方案 B 完整命令清单（可复制）

在确认 `dev` 上该 commit 的改动都已提交后，可按顺序执行：

```bash
# ---- 在仓库根目录执行 ----
git status
git fetch github-ssh

# 没有本地 main 时：
git checkout -b main github-ssh/main

# 已有本地 main 时改为：
# git checkout main
# git pull github-ssh main

git merge dev
git push -u github-ssh main

# 可选：确认
git status
git log --oneline --decorate -5
git branch -vv
```

### 2.7 推送成功后，`dev` 怎么处理？

任选其一即可：

**推荐：以后日常都在 `main` 上开发**

```bash
git checkout main
# 之后改代码、commit、push 都在 main
```

本地 `dev` 可保留作备份，或删除（确认 `main` 已包含所需提交后再删）：

```bash
git branch -d dev          # 安全删除（未合并会拒绝）
# git branch -D dev        # 强制删除（慎用）
```

**若仍想保留 `dev` 作沙盒**：合并并 push 完 `main` 后，把 `dev` 快进到与 `main` 一致，避免下次再分叉：

```bash
git checkout dev
git merge main
# 一般不必把 dev 推到 GitHub，除非你明确需要远程也有 dev
```

---

## 3. 以后拉取代码后的标准日常操作

目标：本地始终跟 GitHub **`main`** 对齐，改完再推回去。远程名以你仓库为准（本文示例为 `github-ssh`）。

### 3.1 第一次（或换机器后）克隆

若尚未有本地仓库：

```bash
git clone ssh://git@github.com/luoorshi/build-scripts.git
cd build-scripts
git checkout main
git branch -vv
```

若你是用 **repo** 等工具拉取的多仓工程，以工具实际生成的 remote 名为准，用：

```bash
git remote -v
```

确认远程名后再替换下文中的 `github-ssh`。

### 3.2 每次开工前：先更新 `main`

```bash
cd /path/to/build-scripts   # 进入本仓库根目录

git checkout main
git fetch github-ssh
git pull github-ssh main
```

或在已设置上游后简写：

```bash
git checkout main
git pull
```

说明：

- **先 pull 再改代码**，可减少与同事（或你在其他机器）提交的冲突
- 若 `pull` 产生冲突，先解决冲突并完成合并 commit，再开始新功能修改

### 3.3 修改代码

用编辑器正常改文件。改完自检：

```bash
git status
git diff
```

注意：

- 不要把密钥、`.env`、本机绝对路径配置等敏感文件提交上去
- 大文件 / 编译产物（如 `build/` 下的 Image、大量日志）通常不应提交；以 `.gitignore` 为准

### 3.4 提交到本地

```bash
git add -A
# 或精确添加：git add path/to/file1 path/to/file2

git commit -m "简要说明为什么改，例如：修复 make-img 在无 Image 时的报错提示"
```

提交信息建议写「目的 / 原因」，而不是只罗列文件名。

查看是否提交成功：

```bash
git log --oneline -3
git status
```

此时改动只在**本地**，GitHub 上还看不到，需要再 push。

### 3.5 推送到 GitHub

```bash
git push github-ssh main
```

已设置 `-u` 上游时也可：

```bash
git push
```

若被拒绝（例如 remote 有你没有的新提交），先拉再推：

```bash
git pull --rebase github-ssh main
# 若有冲突：解决 → git add → git rebase --continue
git push github-ssh main
```

`--rebase` 会把你的本地提交挪到最新远程提交之后，历史更直线；也可改用普通 `git pull`（merge），按团队习惯选择。

### 3.6 日常流程一览（推荐固定成习惯）

```text
git checkout main
      ↓
git pull（先更新）
      ↓
修改代码 / 本地验证
      ↓
git add …
      ↓
git commit -m "…"
      ↓
git push
```

对应命令模板：

```bash
git checkout main
git pull github-ssh main

# … 编辑文件 …

git status
git add -A
git commit -m "你的说明"
git push github-ssh main
```

### 3.7 只想临时试验、不想弄脏 `main` 时

可以新建功能分支，做完再合并回 `main`（思想与方案 B 相同）：

```bash
git checkout main
git pull github-ssh main

git checkout -b feature/xxx      # 从最新 main 拉出分支
# … 修改、commit …

git checkout main
git pull github-ssh main
git merge feature/xxx
git push github-ssh main
```

---

## 4. 常见问题与处理

### 4.1 `git push` 提示 `origin` 不存在

本仓库远程名是 **`github-ssh`**，应使用：

```bash
git push github-ssh main
```

查看远程：

```bash
git remote -v
```

### 4.2 当前在 `dev`，想直接推到远程 `main`（非方案 B，仅作对照）

```bash
# 在已 commit 的前提下
git push -u github-ssh HEAD:main
```

这是「当前分支内容推到远程 main」。本文推荐的方案 B 更清晰：先让本地也有与 GitHub 一致的 `main`，再 merge，减少以后分支错乱。

### 4.3 `git pull` / `merge` 冲突

1. `git status` 查看未合并文件  
2. 打开文件，处理 `<<<<<<<` / `=======` / `>>>>>>>`  
3. `git add <文件>`  
4. `git commit`（merge 冲突）或 `git rebase --continue`（rebase 冲突）

不确定时可把 `git status` 与冲突片段发给同事或助手协助判断。

### 4.4 误在错误分支上改了很多文件

若还没 commit：

```bash
git stash push -u -m "move to main"
git checkout main
git pull github-ssh main
git stash pop
# 再 add / commit / push
```

若已经 commit 在 `dev`：按本文 **第 2 章方案 B** 合并进 `main` 即可。

### 4.5 如何确认 GitHub 已更新

```bash
git fetch github-ssh
git log --oneline main -3
git log --oneline github-ssh/main -3
```

两条线的最新 commit 哈希一致，即本地 `main` 与远程 `main` 对齐。

---

## 5. 关键词索引（方便日后检索）

| 关键词 | 相关章节 |
|--------|----------|
| 方案 B | §2 |
| `github-ssh` | §1、§2.5、§3 |
| `dev` → `main` | §2.3、§2.4 |
| `git fetch` / `git pull` | §2.2、§3.2 |
| `git merge dev` | §2.4 |
| `git cherry-pick` | §2.4 |
| `git push -u github-ssh main` | §2.5 |
| 日常拉取后提交 | §3 |
| 冲突 / rebase | §3.5、§4.3 |
| stash 换分支 | §2.1、§4.4 |

---

## 6. 与本目录其他文档的关系

- 编译与镜像脚本用法见 [README.md](README.md)
- 本文只覆盖 **Git 分支、拉取、提交、推送**；不涉及 `buidl_tools.sh` / `make-img.sh` 的编译步骤

如远程名、默认分支有变更，以 `git remote -v` 与 GitHub 仓库 Settings → Default branch 为准，并相应替换本文中的 `github-ssh` / `main`。
