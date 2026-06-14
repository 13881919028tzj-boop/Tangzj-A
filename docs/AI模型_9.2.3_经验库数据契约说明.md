# AI模型 9.2.3 经验库数据契约说明

## 1. 默认经验库路径

AI_MODEL 默认从以下目录读取训练工厂生成的经验库：

```text
/data/ai-training-data/experience_library/current/
```

该目录不存在时，AI_MODEL 不应崩溃。经验委员保持弃权，并显示“经验库未找到，等待 AI-Training-Factory 生成”。

## 2. 训练工厂应输出的文件

推荐文件：

```text
experience_manifest.json
experience_version.json
symbol_level_experience.parquet
group_level_experience.parquet
global_level_experience.parquet
```

优先推荐 Parquet。AI_MODEL 也预留 JSONL、JSON、CSV 的兼容检测，但当前不会一次性加载大文件。

## 3. 三层经验库结构

- `symbol_level_experience`：同一个币种自己的历史相似状态经验。
- `group_level_experience`：同类币种历史相似状态经验，单币种样本不足时补充。
- `global_level_experience`：全市场通用规律和兜底参考。

未来经验委员查询顺序：

```text
当前 state_code + state_vector
-> 同币种经验
-> 同类币种经验
-> 全市场经验
-> 按样本数量和置信度动态加权
```

## 4. 每条经验记录字段

基础字段：

```text
experience_id
experience_version
generated_at
scope_type
symbol
symbol_group
state_code
state_vector_center
sample_count
avg_similarity
confidence
data_quality
```

未来表现字段：

```text
future_30m_up_probability
future_30m_down_probability
future_30m_sideways_probability
future_60m_up_probability
future_60m_down_probability
future_60m_sideways_probability
```

收益字段：

```text
avg_return_30m
avg_return_60m
median_return_30m
median_return_60m
```

MFE / MAE 字段：

```text
mfe_p50
mfe_p75
mfe_p90
mae_p50
mae_p75
mae_p90
```

止盈止损建议：

```text
suggested_stop_loss
suggested_take_profit_1
suggested_take_profit_2
suggested_trailing_stop
suggested_max_holding_minutes
```

风险字段：

```text
historical_max_drawdown
historical_loss_probability
trap_risk_avg
risk_score_avg
```

适用性字段：

```text
min_similarity_required
min_sample_count_required
applicable_market_regime
notes
```

版本字段：

```text
schema_version
state_language_version
cognition_model_version
weight_config_version
similarity_config_version
```

完整机器可读契约见：

```text
config/experience_library_contract_v1.json
```

## 5. 当前快照如何作为查询输入

AI_MODEL 用当前市场认知快照构造经验库查询：

```text
symbol
symbol_group
state_code
state_vector
```

`state_vector` 必须包含趋势、资金、结构、行为、风险、需求、净需求、置信度和数据完整度。未来经验库不能只按 `state_code` 匹配，必须同时使用 `state_code + state_vector`。

## 6. 经验委员降级逻辑

当经验库目录不存在、清单缺失、版本缺失、三层经验文件不完整，或 parquet 读取依赖不可用时：

```text
vote = ABSTAIN
confidence = 0
data_integrity_score = 0
```

经验委员会显示默认路径、当前 `state_code`、`state_vector` 摘要和弃权原因。

## 7. 大数据不能进入 GitHub

经验库、历史行情、训练样本、回测大文件不得提交到 GitHub。推荐把大数据放在：

```text
/data/ai-training-data
```

GitHub 只保存代码、配置契约、文档和小型示例。

## 8. 目录分离

AI_MODEL 和 AI-Training-Factory 可以部署在同一台 Vultr 服务器，但必须目录分离：

```text
/opt/AI_MODEL
/opt/AI-Training-Factory
/data/ai-training-data
```

AI_MODEL 是实时系统，AI-Training-Factory 是离线训练系统。训练工厂不能直接修改 AI_MODEL 的实盘权限、安全锁或交易执行逻辑。
