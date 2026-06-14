# AI模型 9.2 市场认知量化说明

AI模型 9.2 建立市场认知量化标准，为后续样本工厂、经验库和经验委员提供统一数据地基。本版不接入正式经验库，不训练模型，不扩大实盘权限。

## 六维状态语言

状态码格式为 `T-C-S-B-R-D`。

- `T` Trend：趋势状态，同时保存 `trend_direction` 和 `trend_strength`。
- `C` Capital：资金状态。
- `S` Structure：结构状态。
- `B` Behavior：盘口、大单和主动行为状态。
- `R` Risk：风险状态，`risk_score` 越高越危险。
- `D` Demand：需求状态，是 9.2 的核心维度。

示例：`T1-C2-S2-B1-R2-D1`。

## 三套权重

`market_cognition_score` 使用认知综合权重：

`Demand 25% + Trend 18% + Capital 16% + Behavior 15% + Structure 14% + Risk安全 12%`

其中 Risk 安全分为 `100 - risk_score`。

经验库方向相似度和止盈止损相似度写入 `config/cognition_similarity_weights_v1.json`，后续 9.4/9.5 使用，当前版本不混用。

## 路径概率

9.2 的 30/60 分钟路径概率是规则概率，字段固定为：

`probability_type = rule_based_v1`

它不是历史统计概率，也不是经验库概率。数据完整度低、风险高或买卖需求接近时，会提高震荡和不确定概率。

## 快照保存

快照写入：

`data/market_cognition_snapshots/market_cognition_YYYYMMDD.jsonl`

保护规则：

- 默认由页面和委员会调用当前交易对象。
- 同一 symbol 默认 300 秒最多写一次。
- 单日文件超过 100MB 停止写入。
- 默认保留最近 7 天。
- 不保存完整原始盘口，不保存 API 密钥、token、密码。
- 写入失败不会影响主程序。

## 与交易委员会关系

9.2 只提供量化认知输入：

- 市场委员读取状态码、趋势、资金、结构、需求和主要矛盾。
- 经验委员仍然弃权，只显示当前 `state_code`，等待 9.4 经验库。
- 风险裁判读取 `risk_score`、`trap_risk_score` 和 `data_integrity_score`。
- 仓位委员会仍使用安全默认逻辑。
- 执行委员会不因 9.2 直接放开实盘。
