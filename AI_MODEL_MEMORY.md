# AI_MODEL_MEMORY.md

更新时间：2026-06-13

本文档用于交给网页版 Codex 继续开发 AI量化交易系统。请优先阅读本文，再阅读代码。

## 1. 当前运行目录与版本

当前本地开发工作区：

```text
C:\Users\联砥\Documents\Codex\2026-06-03\python-binance-ai-assistant-1-streamlit\AI模型_7.1.1
```

当前同步运行目录：

```text
C:\Users\联砥\Desktop\AI_MODEL\current
```

当前版本显示：

```text
AI模型 9.0
AI模型 9.0 交易版
```

注意：目录名仍然叫 `AI模型_7.1.1`，这是历史目录名。不要为了版本号强行重命名 Python 文件或目录，否则会破坏现有 import。版本号以 `app.py` 中 `APP_TITLE` / `VERSION` 为准。

启动命令：

```powershell
cd C:\Users\联砥\Desktop\AI_MODEL\current
py -3.12 -m streamlit run app.py --server.port 8531 --server.address 127.0.0.1
```

本地访问：

```text
http://127.0.0.1:8531
```

## 2. 用户长期目标

用户要做的是一个长期运行的 AI量化交易系统，覆盖：

1. Binance 行情、K线、盘口、大单、资金、OI/Funding、多空比。
2. 实时交易机会榜。
3. AI交易委员会。
4. DeepSeek / Gemini 外部 AI 接入。
5. 自动模拟交易。
6. 小资金实盘交易。
7. 小资金 U本位永续合约交易。
8. 自动实盘交易。
9. 交易记录、复盘、数据看板。
10. 服务器长期运行和运维中心。

用户希望系统最终可以自动运行，但每一步都要求：

1. 机会来自交易机会榜。
2. 风险评分和委员会判断必须可解释。
3. 自动交易不能绕过安全锁、熔断、API权限、交易所规则、Test Order。
4. 页面要适合手机端。
5. Binance / DeepSeek / Gemini API 要能在页面安全填写和保存。
6. 任何密钥不能写入日志，页面只能显示脱敏状态。

## 3. 从 7.0 到 9.0 的开发脉络

### 7.0 初始阶段

系统从 Streamlit 单体应用起步，核心是 Binance 行情终端：

1. 当前交易对象。
2. 实时价格。
3. 24h涨跌幅。
4. K线。
5. 盘口订单簿。
6. 大单监控。
7. 简单市场状态。
8. 本地策略初步评分。

这一阶段形成了 `app.py` 主页面结构和 `services/` 服务层。

### 7.1 - 7.5 行情与策略增强

逐步加入：

1. K线系统。
2. 盘口订单簿。
3. 大单监控。
4. 市场结构判断。
5. 趋势评分。
6. 风险评分。
7. OI / Funding / 多空比。
8. 资金结构评分。
9. 清算热力区。
10. 爆仓风险分析。
11. 市场风险雷达。
12. 机会榜。
13. 观察池。
14. 本地策略引擎。

关键文件：

```text
services/binance_public.py
services/kline_service.py
services/orderbook_service.py
services/orderbook_analyzer.py
services/whale_monitor.py
services/market_oi.py
services/market_risk_radar.py
services/local_strategy_engine.py
services/opportunity_score_engine.py
services/market_scanner.py
services/market_cache.py
```

### 7.6 AI交易委员会与小资金实盘安全

建立 AI交易委员会：

1. 本地策略委员。
2. 趋势委员。
3. 资金委员。
4. 盘口委员。
5. 清算委员。
6. 大单/庄家委员。
7. 风险委员。
8. 实盘安全委员。
9. DeepSeek 委员。
10. Gemini 委员。

后续调整后：

1. 观察池委员转为影子委员。
2. 策略验证委员转为影子委员。
3. DeepSeek / Gemini 升级为正式委员。
4. 风险委员和实盘安全委员保留硬否决权。

关键文件：

```text
services/ai_committee_engine.py
services/external_ai_center.py
services/external_ai_client.py
services/secure_api_vault.py
```

7.6 阶段还建立了小资金手动实盘安全中心：

1. Order Plan。
2. Order Preview。
3. 交易所规则校验。
4. Spot Test Order。
5. 人工确认。
6. 审计日志。
7. 手动撤单。
8. 持仓识别。
9. 人工确认平仓。

关键文件：

```text
services/live_trading_center.py
services/manual_position_override.py
```

### 7.7 半自动审批流阶段

曾经实现过半自动审批中心：

1. 审批单。
2. 审批队列。
3. 用户批准/拒绝/修改。
4. 审批执行前检查。
5. 审批日志。

关键文件：

```text
services/approval_center.py
```

重要变化：到 9.0 时，用户明确要求取消审批制度。现在底部导航的“审批”已经改成“自动”，旧 `?page=approval` 路由只作为兼容入口，实际显示自动交易控制台。机会榜快速引擎也不再创建审批单，改为生成自动候选。

### 8.0 - 8.2 服务器运行、远程控制、通知

逐步加入：

1. 服务器运行中枢。
2. 自动模拟常驻运行。
3. 远程控制。
4. 简单认证。
5. 设备识别。
6. 通知中心。
7. 远程操作审计。

关键文件：

```text
services/server_runtime.py
services/remote_control_center.py
services/notification_center.py
services/user_account.py
services/cloud_sync_adapter.py
```

### 8.3 数据看板和经营分析

加入数据看板：

1. 平台总览。
2. 模拟交易看板。
3. 实盘交易看板。
4. 自动实盘看板。
5. 委员表现。
6. DeepSeek / Gemini 表现。
7. 策略表现。
8. 风控表现。
9. 服务器运行看板。
10. 通知与远程操作看板。
11. 日报/周报/月报导出。

关键文件：

```text
services/dashboard_center.py
```

### 8.3.1 快速机会捕捉与机会榜重构

加入 TOP1/TOP10 快速捕捉：

1. TOP10 快速刷新。
2. TOP1 3秒快速捕捉。
3. 80分触发候选。
4. TOP10 委员会快速预判。
5. 多机会并行评审。
6. 冷却时间。
7. 审查次数。
8. 剔除机制。
9. 自动补位。

关键文件：

```text
services/fast_opportunity_engine.py
```

后来修复：

1. risk_score 普遍过高的问题。
2. raw_opportunity_score / final_opportunity_score 分离。
3. risk_breakdown / opportunity_breakdown 显示。
4. 机会榜按最终机会分排序。
5. None 分数安全兜底。
6. current_symbol 全局同步。
7. 机会榜点击后联动顶部状态栏、K线、盘口、信号、委员会。
8. 所有排行榜和订单记录的币种都可以点击跳转到 K线区域。

### 8.5 模拟交易持久化与运维中心

加入 SQLite 数据库：

```text
database/trading.db
```

核心表：

```text
sim_trades
review_records
```

功能：

1. 模拟开仓写库。
2. 模拟平仓更新。
3. 交易记录页面。
4. 模拟交易统计中心。
5. 自动复盘。
6. 系统运维中心。
7. Binance / DeepSeek / Gemini 状态显示。
8. systemd / Ubuntu 24.04 / Vultr 东京部署准备。

关键文件：

```text
services/trading_database.py
services/system_operations.py
services/system_diagnostics.py
```

### 8.5.1 机会榜动态轮换和 Gemini 修复

加入机会榜动态淘汰：

1. `review_count`
2. `reject_count`
3. `cooldown_until`
4. `round_index`
5. `status`
6. 被否决 2 次移除。
7. 审查 3 次未通过移除。
8. 自动补位。
9. 30分钟重新全市场排榜。
10. 观察池联动移除。

关键文件：

```text
services/fast_opportunity_engine.py
services/watchlist_manager.py
```

Gemini 连接修复主要集中在：

```text
services/external_ai_client.py
services/external_ai_center.py
services/secure_api_vault.py
```

SSL 证书要求：

1. 不允许 `verify=False`。
2. 使用 `certifi.where()`。
3. SSL错误返回中文提示，不崩溃。
4. DeepSeek/Gemini 失败不影响 Binance、本地策略、委员会本地成员。

### 9.0 交易版

9.0 重点变化：

1. 版本显示改为 `AI模型 9.0 交易版`。
2. 所有榜单和订单记录中的币种支持点击跳转 K线区域。
3. 剔除旧的“待接入”无数据占位块，改为更准确的“初始化中 / 获取中 / 数据不足 / 获取失败”。
4. 审批栏改为自动交易栏。
5. 取消审批制度作为交易主流程。
6. 机会榜不再生成审批单，改为生成自动候选。
7. 自动交易支持 Spot 现货和 U本位永续合约。
8. 自动交易栏支持 Binance API 填写和保存。
9. Binance API 保存机制和 DeepSeek/Gemini 一样，走 `secure_api_vault.py` 写入本地 `.env`。
10. 无 Binance API 时，自动交易栏显示“待接入”。
11. 用户可设置：
    - 自动交易本金。
    - 开仓比例，最高 40%。
    - 现货/永续是否启用。
    - 默认市场。
    - 最大允许杠杆。
    - 指定执行杠杆。
    - 止盈阈值。
    - 止损阈值。
    - 是否允许自动真实下单。
    - 是否允许自动止盈止损。
12. 默认杠杆为 5x。
13. 默认止盈/止损为模拟交易阈值的约 2/3：
    - 止盈 `2.13%`
    - 止损 `-1.07%`
14. 自动交易后台循环已经接入 `background_refresher.py`。
15. 自动交易循环会从机会榜 TOP10 快速预判中寻找候选。
16. 真实下单前仍然必须通过：
    - API 权限。
    - LIVE_TRADING_ENABLED。
    - 安全锁。
    - 熔断。
    - 白名单。
    - 单笔额度。
    - 单日额度。
    - 交易所规则校验。
    - Spot/Futures Test Order。
    - 实盘执行前检查。

关键修改文件：

```text
app.py
services/live_auto_pilot.py
services/fast_opportunity_engine.py
services/background_refresher.py
services/ai_committee_engine.py
```

## 4. 当前导航结构

底部导航现在包括：

```text
总览
行情
信号
交易
自动
持仓
记录
复盘
数据
我的
```

注意：

1. “审批”已经改为“自动”。
2. `?page=approval` 保留兼容，但实际显示自动交易控制台。
3. 不要恢复旧审批中心作为主流程。

## 5. 当前自动交易逻辑

自动交易栏函数名仍叫：

```python
render_approval_center_page()
```

这是为了兼容旧 route，不代表还使用审批制度。不要只看函数名误判。

页面标题和实际内容已经是自动交易控制台。

自动交易配置文件：

```text
data/live_auto_config.json
```

默认配置在：

```text
services/live_auto_pilot.py
DEFAULT_CONFIG
```

关键字段：

```python
principal_usdt
position_pct
max_order_usdt
daily_limit_usdt
allow_spot
allow_futures
default_market_type
default_leverage
max_leverage
take_profit_pct
stop_loss_pct
live_auto_pilot_enabled
live_auto_order_enabled
live_auto_exit_enabled
paused
circuit_breaker_enabled
allowed_symbols
```

开仓金额逻辑：

```text
保证金 = principal_usdt * position_pct / 100
永续名义金额 = 保证金 * leverage
现货名义金额 = 保证金
position_pct 最高 40%
```

自动交易后台循环：

```python
services/live_auto_pilot.py
run_live_auto_trading_cycle(rankings)
```

调用位置：

```python
services/background_refresher.py
_refresh_rankings()
```

自动交易候选来源：

```text
交易机会榜 TOP10
run_committee_top10_precheck()
```

不要绕过机会榜直接生成自动交易候选。

## 6. 当前模拟交易逻辑

自动模拟交易后台循环：

```python
services/auto_simulation_runner.py
run_auto_simulation_cycle(rankings)
```

调用位置：

```python
services/background_refresher.py
_refresh_rankings()
```

模拟交易长期运行要求：

1. 模拟账户状态为 running。
2. 模拟设置 mode 为 auto。
3. 机会榜 TOP10 有符合条件的候选。
4. 自动模拟不调用真实交易接口。
5. 模拟交易数据写入 JSON / CSV / SQLite。

关键文件：

```text
services/sim_trade_engine.py
services/auto_simulation_runner.py
services/trading_database.py
```

## 7. API 与密钥保存

统一安全保存文件：

```text
services/secure_api_vault.py
```

本地密钥文件：

```text
.env
```

支持字段：

```text
BINANCE_API_KEY
BINANCE_API_SECRET
BINANCE_TESTNET_API_KEY
BINANCE_TESTNET_API_SECRET
DEEPSEEK_API_KEY
GEMINI_API_KEY
```

页面只能显示脱敏状态，不要显示完整 Key / Secret。

不要把以下内容写入日志：

```text
API Secret
完整 API Key
密码
真实账户敏感明细
```

## 8. Binance 实盘与合约能力

实盘中心文件：

```text
services/live_trading_center.py
```

已支持：

1. Spot 现货订单计划。
2. Spot Test Order。
3. Spot 真实下单。
4. U本位合约订单计划。
5. Futures Test Order。
6. 设置合约杠杆。
7. U本位合约真实下单。
8. 真实订单记录。
9. 实盘审计日志。

关键函数：

```python
validate_live_order_plan()
validate_order_against_exchange_rules()
run_spot_test_order()
run_futures_test_order()
submit_live_spot_order()
submit_live_futures_order()
set_futures_leverage()
get_live_safety_status()
load_api_credentials_safely()
```

注意：

1. 自动交易调用 `submit_live_spot_order()` / `submit_live_futures_order()`。
2. 自动交易不是直接调用 Binance API，仍通过 live_trading_center 的安全链路。
3. `LIVE_TRADING_ENABLED` 仍然是关键开关。

## 9. 机会榜逻辑

机会榜来源：

```text
services/market_scanner.py
services/opportunity_score_engine.py
services/fast_opportunity_engine.py
```

核心概念：

```text
raw_opportunity_score
risk_score
risk_penalty
overheat_penalty
data_quality_penalty
final_opportunity_score
opportunity_status
risk_breakdown
opportunity_breakdown
```

机会榜排序使用：

```text
final_opportunity_score
```

不是原始热度分。

TOP10 都要快速预判。机会进入候选必须满足：

```text
final_opportunity_score >= 80
risk_score < 70
data_quality != poor
快速预判通过
未处于重复冷却
未被剔除
```

机会榜剔除规则：

```text
reject_count >= 2 -> removed
review_count >= 3 且未 approved -> removed
委员会阻断/等待/不交易达到2次 -> removed
```

冷却时间：

```text
OPPORTUNITY_DUPLICATE_COOLDOWN_SECONDS = 120
OPPORTUNITY_REJECT_COOLDOWN_SECONDS = 120
```

用户曾要求机会榜冷却后显示审查次数，页面中已经加入审查次数/否决次数/轮次显示。

## 10. 观察池逻辑

观察池文件：

```text
services/watchlist_manager.py
```

观察池用于记录未立即交易但值得跟踪的机会。

规则：

1. 机会榜中的潜在机会可进入观察池。
2. 观察池可作为机会榜候选来源之一。
3. 如果失去进入观察池资格，应自动踢出。
4. 如果连续两次被委员会否决，也应从观察池移除。

观察池不是正式交易许可，只是机会发现来源。

## 11. 当前页面联动

current_symbol 是全局当前交易对象。

涉及：

```python
st.session_state.current_symbol
st.session_state.committee_active_symbol
st.session_state.topbar_symbol
st.session_state.kline_symbol
st.session_state.orderbook_symbol
st.session_state.signal_symbol
```

统一切换函数：

```python
set_current_symbol(symbol, source)
```

要求：

1. 顶部状态栏读取 current_symbol。
2. K线读取 current_symbol。
3. 盘口读取 current_symbol。
4. 信号页读取 current_symbol。
5. 交易页读取 current_symbol。
6. 点击任何排行榜/订单记录中的币种，跳转 K线区域。

K线跳转相关函数在 `app.py`：

```python
normalize_symbol()
kline_href()
kline_symbol_link()
render_kline_jump_links()
```

K线区域有锚点：

```html
#kline-area
```

## 12. 数据初始化与 None 安全

此前修复过首次进入页面显示：

```text
当前价格：正在获取
24小时涨跌幅：正在获取
市场状态：待接入
AI建议：待接入
风险评分：0
机会评分：0
```

当前规则：

1. 初始化必须早于页面渲染。
2. None 表示未计算，不等于 0。
3. 评分未计算显示“计算中”。
4. 不允许 None 和数字直接比较。

安全函数在 `app.py`：

```python
safe_number()
safe_score()
safe_compare_lt()
safe_compare_gte()
format_score()
format_percent()
format_price()
get_risk_class()
get_opportunity_class()
```

如果后续改评分或榜单，请继续使用这些安全函数。

## 13. 后台刷新与启动顺序

后台刷新文件：

```text
services/background_refresher.py
```

线程：

1. 市场榜单刷新。
2. 当前 ticker 刷新。
3. K线刷新。
4. 盘口刷新。
5. 衍生品数据刷新。
6. 大单刷新。
7. 自动模拟循环。
8. 自动交易循环。

重要：9.0 最近修过启动顺序。`app.py` 的 `main()` 里，后台线程应在页面初始化和渲染之后启动，避免首屏被全市场扫描抢资源。

当前主流程大意：

```python
initialize_session_state()
init_state()
ensure_current_device()
enforce_account_login()
bootstrap_initial_data()
refresh_page_data()
render_fixed_market_bar()
render_page()
render_bottom_nav()
start_background_refresher()
start_local_api_server()
```

不要再把 `start_background_refresher()` 提到首屏渲染之前。

## 14. 当前已知问题与注意事项

### 14.1 启动验证问题

曾经启动 Streamlit 后出现 Python 进程 CPU 偏高，导致健康检查超时。后来排查时发现并没有 Python/Streamlit 残留，当前电脑卡顿主要来自 Codex / Explorer / DWM / Defender。

如果网页版 Codex 接手后发现启动慢，请先：

1. 确认没有旧的 Python/Streamlit 进程。
2. 用 `py_compile` 检查。
3. 用端口 8531 启动。
4. 不要一开始就全市场高频扫描。
5. 检查 `background_refresher.py` 是否在首屏前启动。

### 14.2 Windows Defender

用户觉得 Defender 占资源，希望关闭。建议优先给项目目录加 Defender 排除项，而不是永久关闭 Defender。

推荐排除路径：

```text
C:\Users\联砥\Desktop\AI_MODEL
C:\Users\联砥\Documents\Codex
```

这比完全关闭 Defender 安全，且能减少代码复制、py_compile、Streamlit 启动时的扫描负担。

### 14.3 不要随意删除运行数据

以下目录/文件属于运行数据：

```text
data/
database/
logs/
reports/
runtime/
.env
```

同步或备份时要保护这些文件。不要无脑 `git reset --hard` 或删除。

### 14.4 备份制度

每次大改后，需要备份：

```text
C:\Users\联砥\Desktop\AI_MODEL\backups\current_9.0_YYYYMMDD_HHMMSS
```

再同步到：

```text
C:\Users\联砥\Desktop\AI_MODEL\current
```

最近一次已知备份：

```text
C:\Users\联砥\Desktop\AI_MODEL\backups\current_9.0_20260611_120419
```

## 15. 推荐网页版 Codex 接手后的第一步

请网页版 Codex 按以下顺序继续：

1. 打开运行目录：

```text
C:\Users\联砥\Desktop\AI_MODEL\current
```

2. 先不要重构。

3. 先运行编译检查：

```powershell
py -3.12 -m py_compile (Get-ChildItem "." -Filter *.py | ForEach-Object { $_.FullName }) (Get-ChildItem ".\services" -Filter *.py | ForEach-Object { $_.FullName })
```

4. 启动：

```powershell
py -3.12 -m streamlit run app.py --server.port 8531 --server.address 127.0.0.1
```

5. 检查页面：

```text
http://127.0.0.1:8531/?page=home
http://127.0.0.1:8531/?page=market
http://127.0.0.1:8531/?page=signals
http://127.0.0.1:8531/?page=auto_trade
http://127.0.0.1:8531/?page=positions
http://127.0.0.1:8531/?page=trade_records
```

6. 优先检查：

```text
顶部状态栏
K线
盘口
大单
机会榜
观察池
自动模拟
自动交易栏
Binance API 接入
DeepSeek/Gemini 状态
```

7. 不要恢复审批流程。

8. 不要扩大自动实盘权限，除非用户明确要求。

9. 不要绕过：

```text
风险委员
实盘安全委员
LIVE_TRADING_ENABLED
安全锁
熔断
交易所规则
Test Order
```

## 16. 后续建议任务

如果继续开发，建议优先级：

1. 自动交易页和模拟交易页 UI 完全对齐。
2. 自动交易后台循环增加更清晰的日志和页面状态。
3. 自动交易持仓自动止盈/止损目前偏保守，需要确认是否真实执行自动平仓。
4. Binance API 接入后完整测试 Spot 和 Futures Test Order。
5. Gemini 连接状态继续观察，失败时页面必须显示明确原因。
6. 机会榜动态轮换继续观察，确保审查次数不会再次几十次不剔除。
7. 给 Defender 添加项目路径排除项，减少本地卡顿。
8. 服务器部署时保护 `.env`、`data/`、`database/`，不要被 Git 覆盖。

## 17. 最重要的原则

1. 机会榜是所有交易机会的总入口。
2. 自动模拟可以自己跑。
3. 自动实盘可以自己跑，但必须经过安全链路。
4. 审批制度已经取消，不要再把机会转成审批单。
5. Binance / DeepSeek / Gemini API 统一走安全保存机制。
6. 不记录 API Secret。
7. 不显示完整 API Key。
8. 不把 None 当成 0。
9. 不把过期数据用于交易。
10. 不把模拟和实盘混在一起统计。
11. 每次大改后要备份并同步到 `current`。

