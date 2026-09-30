# 你的 agent 需要一份不在场证明。

**alibi** 核对 AI 编程 agent **怎么描述它自己的工作**，与它**实际做了什么**。

```console
$ alibi check .github/PULL_REQUEST.md

  diff: 11 个文件，新增 550 行，删除 72 行

    1. VERIFIED     file_created
       声明     I created `alibi/writes.py` to record what a session wrote.
       diff     the diff adds alibi/writes.py

    2. CONTRADICTED file_deleted
       声明     I removed `alibi/verify.py` to simplify the judging layer.
       diff     the diff touches alibi/verify.py but does not delete it

    3. UNVERIFIED   tests_pass
       声明     Tests pass.
       diff     测试是否通过是 CI 的事实，不是 diff 能证明的

  ✓ 1 verified   ✗ 1 contradicted   ? 1 unverified
```

真实输出，就在你要审的那个 diff 上。不调模型、不需账号、不联网，依赖只有 `git`。

[English](./README.md) 是主文档，本文件是中文版。

---

## 为什么需要它

每个用编程 agent 的团队，都在合并 agent 写的文字。PR 说它加了模块、删了分支、改了超时——有人读，或者没人读。从来没有任何东西把这段文字和 diff 放在一起核对过。

## 安装

```console
curl -fsSL https://raw.githubusercontent.com/kevindurant735rocket-creator/alibi/main/install.sh | sh
```

或者从源码装：

```console
git clone https://github.com/kevindurant735rocket-creator/alibi && cd alibi
python3 -m alibi doctor      # Python 3.9+，无需安装任何依赖
```

接进 CI：

```yaml
- name: 核对 PR 描述与 diff 是否一致
  run: |
    git fetch origin ${{ github.base_ref }}
    alibi check .github/PULL_REQUEST.md --base origin/${{ github.base_ref }}
```

退出码即契约：**0** 无矛盾，**1** 有矛盾，**2** alibi 跑不起来。崩溃一律是 2，绝不会是 1——退出码 1 意味着 alibi 看了东西之后不同意，bug 绝不能穿上这件衣服。

## 三种判定

| 判定 | 含义 |
|---|---|
| `VERIFIED` | diff 说的和描述说的一致 |
| `CONTRADICTED` | 不一致 |
| `UNVERIFIED` | alibi 判不了 |

`UNVERIFIED` **绝不**被悄悄算作通过。

## 它不是什么

- **不评判工作质量。** diff 内部自洽也可能是错的。alibi 只核对句子与产物。
- **不知道测试有没有过。** 那是 CI 的事实，"Tests pass" 故意判 `UNVERIFIED`。
- **不判断意图。** "我重构了以提升可读性"没有可核对的东西，报 `UNVERIFIED`。
- **不是 reviewer / linter / formatter。** 只读文本和 diff，不写任何文件。

## 两种模式

| 命令 | 核对什么 | 拿什么核对 |
|---|---|---|
| `alibi check <文件>` | PR 描述、commit message、任何书面总结 | diff |
| `alibi scan` | agent 的 session 记录 | 该会话实际做过什么 |

`check` 是今天就能抓到东西的那个。`scan` 是更深的审计，在真实 session 上**绝大多数是 `UNVERIFIED`**——原因见下表，这是诚实的数字。

## 736 个真实 session 上的实测

| | 数量 |
|---|---|
| 审计的会话 | 736 |
| 无法读取的 transcript | 122 |
| 找到的完成声明 | 288 |
| **已验证** | 15 |
| **矛盾** | **0** |
| **无法核实** | 273 |

**在真实 agent session 上，alibi 一条矛盾都没抓到。** 这是诚实的数字，而原因比数字本身更有用：

| 其余 273 条为何无法核实 | 数量 |
|---|---|
| 声明没有点名任何路径、字面量或命令 | 104 |
| 会话里的命令没有可读的退出码 | 81 |
| diff 里没有那一行 | 48 |
| 命令是管道/串联，退出码属于 shell 不属于运行器 | 13 |
| 多条声明与多条命令，无法区分对应关系 | 10 |
| 路径指向会话目录之外的另一棵树 | 2 |

能让 alibi 指控的 transcript，需要 agent 跑过**没有串联的命令**、**记录了状态**、且**在 git 仓库里**。这比"自信的总结的数量"所暗示的要罕见得多。

**这正是 `check` 是主命令的原因。** diff 永远在那里、永远完整、而且正是被审的那份东西。

### 改过的错，留着不删

这个工具最早的版本声称自己抓到过谎言。**没有。** 有一版在 734 个会话上产出 **17 条 `CONTRADICTED`，一条都不成立。** 每一处修正都有对应的回归测试。

| 原来的做法 | 现在的做法 |
|---|---|
| 「`utils.py` 存在吗」→ `VERIFIED` | 文件声明要求**本会话写过**它 |
| 任意一次测试通过就判定声明成立 | 以**离该句最近的**那次运行为准 |
| `pytest \|\| true` 算测试通过 | 串联命令的退出码属于 shell，不属于运行器 |
| 从输出正文里捞**第一个** `exit N` | 必须独占一行结尾、取**最后一个**，优先用 agent 的 `is_error` 字段 |
| 猜某个文件声明说的是哪个文件 | 猜测只决定去哪看；由 `git diff -U0` 定论 |
| `verify.json` 被匹配成 `verify.js` | 扩展名按最长优先 + 词边界 |
| `docker cp ws_test.py` 算跑了测试 | 只有能执行测试的东西才算测试证据 |
| 容器里的文件在宿主上「不存在」 | 容器感知的会话给 `UNVERIFIED` |

对一个核验工具来说，**红行才是产品本身**。**alibi 藏起它们的那天，就是你该不再相信绿行的那天。**

## 加一个新 agent

一个文件，别的都不用改：

```bash
cp alibi/agents/claude_code.py alibi/agents/<你的agent>.py
```

定义 `NAME`、`SUMMARY`、`locate(explicit)`、`parse(path)`，然后 `python3 -m alibi doctor` 就能看到它——包括导入失败���适配器，它会明确报出来而不是藏起来。详见 [docs/agents/](docs/agents/)。

`alibi check` 不需要任何适配器，它对任意文本都工作。

## 你的数据

alibi 只读已存在的文件，不写任何东西。无账号、无 API key、无遥测、无网络请求——有一条测试在强制这件事。

## 许可

MIT，见 [LICENSE](LICENSE)。
