# Conclave

**并行隔离多智能体执行引擎** —— Agent 彼此隔离、并行独立执行，产出聚合后才揭晓。运行时零第三方依赖（纯 stdlib）。

> 命名典故：教皇选举（conclave）——枢机们彼此隔离、独立投票，选票聚合后才公布结果；黑烟 = 零有效票（门 1），白烟 = 聚合完成。

## 为什么需要它

把一个复杂判断交给多个 LLM 时，最常见也最隐蔽的失败模式不是"模型答错"，而是**它们一起错**：共享上下文的 Agent 会互相趋同（Groupthink）、陷入同一个思维死角（认知隧道）。Conclave 用机制而非 prompt 来保证独立性——**每个 Agent 只拿到任务与自己的角色设定，看不到任何同伴**。

## 30 秒上手

```python
from conclave import Engine

class MyAgent:                       # 鸭子类型：role / goal / execute
    def __init__(self, role, goal):
        self.role, self.goal = role, goal
    def execute(self, prompt):
        return call_your_llm(prompt)  # 任意厂商 / 本地模型

engine = Engine(max_workers=8, per_call_timeout=30.0)
result = engine.run(
    [MyAgent("Researcher", "find facts"),
     MyAgent("Critic", "find risks"),
     MyAgent("Writer", "draft the report")],
    "评审这份系统设计文档",
    require_all=False,
)

print(result.content)          # 聚合产物
for t in result.traces:        # 全量审计轨迹：成败 / 耗时 / 错误
    print(t.role, t.status, t.duration_ms)
```

单个 Agent 崩溃（API 超时 / 限流 / 解析失败）不会炸掉整批：它被收割为 `error` Trace，幸存者照常聚合。零成功才抛 `NoValidReportsError`；开启 `require_all=True` 则批次不完整即抛 `IncompleteBatchError`（对账 / 合规场景）。

## 架构

```
                      ┌──────────────────────────────┐
   task ──────────────►│          Engine.run           │
                      └───────┬──────────────────────┘
            D1 零互知          │ 并行分发（ThreadPool）
        ┌──────────┬──────────┼──────────┬──────────┐
        ▼          ▼          ▼          ▼          
   ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
   │Agent A │ │Agent B │ │Agent C │ │Agent D │   各自只见任务+角色设定
   └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘
       │          │          │          │   D2 异常即数据
       ▼          ▼          ▼          ▼   （崩溃也产出 error Trace）
   ┌────────────────────────────────────────┐
   │            Trace（审计轨迹）            │  role/status/output/error/耗时
   └───────────────┬────────────────────────┘
                   ▼
        gate 1: 零成功? ──► NoValidReportsError（黑烟）
        gate 2: require_all 且不完整? ──► IncompleteBatchError
                   ▼
        Aggregator (prompt, reports) -> str   ──►  SynthesisResult
```

## 设计决策

| #  | 决策 | 理由 |
|----|------|------|
| D0 | 运行时零依赖 | 编排层不绑定任何厂商 SDK；LLM 适配留给调用方 |
| D1 | 零互知 | 独立性靠机制保证：Agent 看不到同伴，杜绝趋同与认知隧道 |
| D2 | 异常即数据 | 子任务异常被收割为 error Trace；审计完整性优先于成功率 |
| D3 | 两道可配置的门 | 零成功→快速失败；`require_all`→完整性场景才启用 |
| D4 | 手动管理 executor | `with` 语句会阻塞至全部 future 完成，超时语义形同虚设 |
| D5 | `time.monotonic()` | 墙上时钟会被 NTP 回拨，测耗时必须单调时钟 |
| D6 | Aggregator 是 callable | 聚合策略不设基类，`(prompt, reports) -> str` 即可接入 |
| D7 | dataclass 而非 pydantic | 核心数据只是审计记录；强校验是边界层职责 |
| D8 | 断路器可注入时钟 | 测试零等待推进冷却期，确定性测试不靠睡眠 |
| D9 | Agent 是 Protocol | 鸭子类型契约，测试替身与真实适配器同构 |

配套的 `breaker.CircuitBreaker` 提供**跨批次**失败记忆：`CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN`，上游 API 持续故障时快速失败、冷却后小流量探测，避免每批都付出完整超时成本。

## 测试即证据

测试套件本身就是容错行为的可执行文档（全部离线，零网络）：

| 测试 | 验证的失败路径 |
|------|----------------|
| `test_single_failure_degrades_to_partial` | 1 个 Agent 崩溃 → 部分降级，幸存者照常聚合 |
| `test_all_fail_raises_no_valid_reports` | 全体崩溃 → 门 1 快速失败，异常自带审计现场 |
| `test_aggregator_never_sees_empty_batch` | 零成功时聚合器绝不被调用（防幻觉聚合） |
| `test_incomplete_batch_raises_with_roster` | 门 2：异常消息同时列出成功/失败名单 |
| `test_prompt_contains_no_peer_knowledge` | D1：断言同伴名字从未出现在 prompt 中 |
| `test_slow_agent_harvested_as_timeout` | D4：慢 Agent 被收割为 timeout error |
| `test_open_blocks_until_cooldown` 等 | 断路器四条状态转移，假时钟零等待 |

```bash
pip install -e ".[dev]" && pytest -v
```

## Roadmap

- [x] 核心引擎：并行隔离 + 三道门 + 批次超时（v0.1.0）
- [x] 断路器：跨批次失败记忆
- [ ] DAG 任务编排：拓扑分波、波间交付物传递（v0.2）
- [ ] RAG 混合检索：多通道召回 + RRF 融合（v0.3）
- [ ] 示例应用：AI 简历评审面板（多专家并行评审 → 聚合报告）

## License

MIT
