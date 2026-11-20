# C 题第三问正式回放入口核查

**首个阻塞点是缺少队员正式回放的启动入口和实际接收决策请求的策略函数。** 核查基线为 `850380e`。当时本地只有 `main` 工作树，远程也只列出同一提交的 `main`。在工作区可见的源码和项目说明中，`FixedTrial.run()` 按固定动作列表执行，`policy_view()` 构造快照，`replay_pair()` 编排离线合成事件。三处均未调用队员策略。[固定动作执行代码](../experiments/c_q3_fixture/b_fixture.py) · [双作业编排](../experiments/c_q3_fixture/pair_test.py)

| 核查项 | 状态 | 依据 |
|---|---|---|
| 正式回放入口及策略接口 | 在本次核查范围内未找到 | 未定位到能接入队员策略并运行指定案例、种子和映射的命令或函数。 |
| 双作业正式接口防泄漏与结算对账 | 未运行 | 缺少函数入口，无法捕获实际调用次数、顺序、时刻和入参。2.75 与 3.75 秒是此前合成日志中的离线探针时刻，是否触发正式策略仍待实测。 |
| S1 单种子冒烟 | 未运行 | 正式接口防泄漏验收尚未通过。抽样、前缀披露、设备占用、费用及共同指标均无本轮正式日志可复算。 |

**队员需要提供什么。** 请给出回放代码所在分支及提交（或工作区相对路径）、可运行的启动命令、该回放实际调用的策略函数或方法的文件路径与签名，以及装载队员已有策略或固定动作的方式。命令或配置还需说明如何将两条合成输入送入同一接口，以及如何选定 S1、验证种子和名义映射。取得入口后可直接按既有双作业预期对账。[双作业验收基线](C题第三问双作业验收.md)

**复现入口检索**　在仓库根目录运行。最后一条用于检查合成代码的实际调用关系。

```bash
git worktree list
git branch -avv
git ls-remote --heads origin
rg --files --hidden --no-ignore -g '!.git' -g '!data/raw/C/Raw_Data/**' | sort
rg -n 'def (policy_view|replay_pair|run)\(|for action in self\.trial|policy_callback_noninterference' experiments/c_q3_fixture/*.py
```
