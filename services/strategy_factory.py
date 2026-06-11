"""策略工厂：策略模板、配置、候选库与复盘建议。

本模块只做策略研究与历史回测，不执行真实订单，不修改生产策略。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from services.replay_learning_engine import analyze_replay_learning


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REGISTRY_PATH = DATA_DIR / "strategy_registry.json"
CONFIG_PATH = DATA_DIR / "strategy_configs.json"
CANDIDATE_PATH = DATA_DIR / "strategy_candidates.json"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path, default: Any) -> Any:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            _write_json(path, default)
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[策略工厂] 数据文件读取失败 {path.name} error={exc!r}")
        try:
            if path.exists():
                path.rename(path.with_suffix(path.suffix + f".broken_{int(time.time())}"))
        except Exception:
            pass
        _write_json(path, default)
        return default


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


DEFAULT_STRATEGIES: list[dict[str, Any]] = [
    {
        "strategy_id": "trend_follow_v1",
        "strategy_name": "趋势跟随策略",
        "strategy_type": "trend",
        "description": "顺着 MA20/MA60 趋势方向交易，并用 RSI 与风险收益比做过滤。",
        "risk_profile": "中",
        "supported_timeframes": ["5m", "15m", "1h", "4h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"ma_short": 20, "ma_long": 60, "rsi_min_long": 45, "rsi_max_long": 75, "rsi_min_short": 25, "rsi_max_short": 55, "atr_mult": 1.5, "rr_min": 1.2},
    },
    {
        "strategy_id": "pullback_confirm_v1",
        "strategy_name": "回踩确认策略",
        "strategy_type": "pullback",
        "description": "趋势中等待价格回踩 MA20 附近后再入场，避免高位追涨或低位追空。",
        "risk_profile": "中",
        "supported_timeframes": ["5m", "15m", "1h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"ma_period": 20, "pullback_pct": 1.0, "rsi_low": 40, "rsi_high": 65, "atr_mult": 1.4, "rr_min": 1.3},
    },
    {
        "strategy_id": "false_break_reversal_v1",
        "strategy_name": "假突破反打策略",
        "strategy_type": "reversal",
        "description": "识别突破后快速失败或跌破后快速收回的反向交易机会。",
        "risk_profile": "高",
        "supported_timeframes": ["5m", "15m", "1h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"lookback": 30, "failure_pct": 0.4, "atr_mult": 1.2, "rr_min": 1.5},
    },
    {
        "strategy_id": "liquidation_hunt_v1",
        "strategy_name": "清算猎杀策略",
        "strategy_type": "liquidation",
        "description": "用波动率和极端突破模拟清算密集区附近的谨慎反打或顺势观察。",
        "risk_profile": "高",
        "supported_timeframes": ["5m", "15m"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"lookback": 48, "volatility_pct": 2.0, "atr_mult": 1.6, "rr_min": 1.5},
    },
    {
        "strategy_id": "whale_follow_v1",
        "strategy_name": "大单跟随策略",
        "strategy_type": "whale",
        "description": "用放量突破近似模拟大资金方向，要求价格和成交量同步确认。",
        "risk_profile": "中",
        "supported_timeframes": ["5m", "15m", "1h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"volume_mult": 1.8, "breakout_lookback": 24, "atr_mult": 1.3, "rr_min": 1.3},
    },
    {
        "strategy_id": "range_trade_v1",
        "strategy_name": "震荡区间策略",
        "strategy_type": "range",
        "description": "横盘区间内靠近支撑观察做多，靠近压力观察做空。",
        "risk_profile": "中",
        "supported_timeframes": ["5m", "15m", "1h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"lookback": 60, "edge_pct": 18, "rsi_low": 35, "rsi_high": 65, "atr_mult": 1.1, "rr_min": 1.1},
    },
    {
        "strategy_id": "committee_resonance_v1",
        "strategy_name": "委员会共振策略",
        "strategy_type": "committee",
        "description": "用更严格的趋势和动量过滤，模拟本地策略与委员会方向共振。",
        "risk_profile": "低",
        "supported_timeframes": ["15m", "1h", "4h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"ma_short": 20, "ma_long": 60, "rsi_min_long": 50, "rsi_max_short": 50, "min_trend_bars": 3, "atr_mult": 1.5, "rr_min": 1.4},
    },
    {
        "strategy_id": "risk_filter_v1",
        "strategy_name": "风险过滤策略",
        "strategy_type": "filter",
        "description": "作为其他策略过滤器，当前用波动率和连续亏损风险做保守过滤，不单独鼓励开仓。",
        "risk_profile": "低",
        "supported_timeframes": ["5m", "15m", "1h", "4h"],
        "supported_markets": ["USDT_PERP"],
        "enabled": True,
        "parameters": {"max_volatility_pct": 4.0, "atr_mult": 1.0, "rr_min": 1.2},
    },
]


def load_strategy_registry() -> list[dict[str, Any]]:
    registry = _read_json(REGISTRY_PATH, DEFAULT_STRATEGIES)
    if not isinstance(registry, list) or len(registry) < 8:
        registry = DEFAULT_STRATEGIES
        _write_json(REGISTRY_PATH, registry)
    return registry


def get_available_strategies() -> list[dict[str, Any]]:
    return [s for s in load_strategy_registry() if s.get("enabled", True)]


def register_strategy(strategy: dict[str, Any]) -> None:
    registry = load_strategy_registry()
    registry = [s for s in registry if s.get("strategy_id") != strategy.get("strategy_id")]
    registry.append(strategy)
    _write_json(REGISTRY_PATH, registry)


def _default_config(strategy_id: str) -> dict[str, Any]:
    strategy = next((s for s in load_strategy_registry() if s.get("strategy_id") == strategy_id), None)
    return dict((strategy or {}).get("parameters") or {})


def load_strategy_configs() -> dict[str, Any]:
    configs = _read_json(CONFIG_PATH, {})
    return configs if isinstance(configs, dict) else {}


def get_strategy_config(strategy_id: str) -> dict[str, Any]:
    configs = load_strategy_configs()
    config = _default_config(strategy_id)
    if isinstance(configs.get(strategy_id), dict):
        config.update(configs[strategy_id])
    return config


def save_strategy_config(strategy_id: str, config: dict[str, Any]) -> None:
    configs = load_strategy_configs()
    configs[strategy_id] = dict(config or {})
    _write_json(CONFIG_PATH, configs)


def reset_strategy_config(strategy_id: str) -> dict[str, Any]:
    configs = load_strategy_configs()
    configs[strategy_id] = _default_config(strategy_id)
    _write_json(CONFIG_PATH, configs)
    return configs[strategy_id]


def load_strategy_candidates() -> list[dict[str, Any]]:
    data = _read_json(CANDIDATE_PATH, [])
    return data if isinstance(data, list) else []


def create_strategy_candidate(result: dict[str, Any], notes: str = "") -> dict[str, Any]:
    metrics = result.get("metrics") or {}
    candidate = {
        "candidate_id": f"candidate_{int(time.time() * 1000)}",
        "strategy_id": result.get("strategy_id"),
        "strategy_name": result.get("strategy_name"),
        "config": result.get("config") or {},
        "symbols": [result.get("symbol")] if result.get("symbol") else result.get("symbols", []),
        "timeframes": [result.get("timeframe")] if result.get("timeframe") else result.get("timeframes", []),
        "grade": result.get("grade", "E"),
        "total_return": metrics.get("return_pct", 0),
        "max_drawdown": metrics.get("max_drawdown_pct", 0),
        "win_rate": metrics.get("win_rate", 0),
        "profit_factor": metrics.get("profit_factor", 0),
        "avg_r": metrics.get("avg_r", 0),
        "sample_size": metrics.get("total_trades", 0),
        "overfit_risk": (result.get("overfit_risk") or {}).get("level", "高"),
        "created_time": _now(),
        "notes": notes or "候选策略仅允许进入模拟验证，不允许直接实盘。",
        "status": "待模拟验证",
    }
    candidates = load_strategy_candidates()
    candidates.insert(0, candidate)
    _write_json(CANDIDATE_PATH, candidates[:200])
    return candidate


def get_strategy_candidates() -> list[dict[str, Any]]:
    return load_strategy_candidates()


def get_strategy_candidates_for_simulation() -> list[dict[str, Any]]:
    return [
        c
        for c in load_strategy_candidates()
        if c.get("status") == "待模拟验证" and str(c.get("grade")) in {"A", "B"} and str(c.get("overfit_risk")) in {"低", "中", "low", "medium"}
    ]


def get_replay_optimization_hints() -> dict[str, Any]:
    replay = analyze_replay_learning()
    return {
        "summary": replay.get("summary", {}),
        "weaknesses": replay.get("weaknesses", []),
        "suggestions": replay.get("strategy_factory_suggestions", []),
    }
