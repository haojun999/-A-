# -*- coding: utf-8 -*-
"""AI 分析：抓取盘面技术面 + 行业消息面，调用 DeepSeek 输出买卖意见。"""

import json
import os
import datetime
import urllib.request
import urllib.parse

from config import SCRIPT_DIR, DEEPSEEK_URL, DEEPSEEK_MODEL
from utils import market_of


def _import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        return None


def fetch_fundamental_text(code):
    """抓取基本面关键财务指标，返回文本。"""
    ak = _import_akshare()
    if ak is None:
        return "（未安装 akshare，无法获取财务数据）"
    try:
        df = ak.stock_financial_abstract(symbol=code)
    except Exception as e:
        return f"（财务数据获取失败：{e}）"

    # 精确匹配关键指标，避免多口径重复
    exact = ["归母净利润", "营业总收入", "基本每股收益", "每股净资产",
             "毛利率", "资产负债率"]
    recent_cols = [c for c in df.columns if isinstance(c, str) and c.isdigit()][:3]
    lines = []
    seen = set()
    for _, row in df.iterrows():
        ind = str(row.get("指标", "")).strip()
        if ind not in exact or ind in seen:
            continue
        seen.add(ind)
        parts = []
        for col in recent_cols:
            v = row.get(col)
            if v is not None and str(v) != "nan":
                parts.append(f"{col}={v}")
        if parts:
            lines.append(f"{ind}: " + ", ".join(parts))
    return "\n".join(lines) if lines else "（未获取到财务指标）"


def fetch_technical_text(code):
    """从 akshare（腾讯日 K）获取技术面数据：走势、均线、量能、支撑压力。"""
    ak = _import_akshare()
    if ak is None:
        return "（未安装 akshare，无法获取技术面数据）"

    symbol = ("sh" if market_of(code) == 1 else "sz") + code
    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=200)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust="qfq")
    except Exception as e:
        return f"（技术面数据获取失败：{e}）"
    if df is None or len(df) == 0:
        return "（未获取到技术面数据）"

    closes = [float(x) for x in df["close"].tolist()]
    vols = [float(x) for x in df["amount"].tolist()]
    dates = [str(x) for x in df["date"].tolist()]
    last = closes[-1]
    last_date = dates[-1]

    lines = []
    # 最新价与当日涨跌幅
    if len(closes) >= 2:
        pct = (closes[-1] - closes[-2]) / closes[-2] * 100
        lines.append(f"最新收盘 {last:.2f} 元（{last_date}），较前一日 {pct:+.2f}%")
    else:
        lines.append(f"最新收盘 {last:.2f} 元（{last_date}）")

    # 区间涨幅
    def pct_range(n):
        if len(closes) >= n + 1:
            return (closes[-1] - closes[-1 - n]) / closes[-1 - n] * 100
        return None

    r5, r20 = pct_range(5), pct_range(20)
    range_parts = []
    if r5 is not None:
        range_parts.append(f"近5日 {r5:+.2f}%")
    if r20 is not None:
        range_parts.append(f"近20日 {r20:+.2f}%")
    if range_parts:
        lines.append("区间涨幅：" + "，".join(range_parts))

    # 均线
    def ma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    ma5, ma10, ma20, ma60 = ma(5), ma(10), ma(20), ma(60)
    ma_parts = []
    for name, v in [("MA5", ma5), ("MA10", ma10), ("MA20", ma20), ("MA60", ma60)]:
        if v is not None:
            ma_parts.append(f"{name} {v:.2f}")
    if ma_parts:
        lines.append("均线：" + " / ".join(ma_parts))

    # 当前价相对均线位置
    dev_parts = []
    for name, v in [("MA5", ma5), ("MA20", ma20), ("MA60", ma60)]:
        if v is not None and v > 0:
            dev = (last - v) / v * 100
            dev_parts.append(f"{name} {'上方' if dev >= 0 else '下方'} {abs(dev):.1f}%")
    if dev_parts:
        lines.append("均线位置：" + "，".join(dev_parts))

    # 近20日支撑/压力
    if len(closes) >= 20:
        recent_high = max(closes[-20:])
        recent_low = min(closes[-20:])
        lines.append(f"近20日高点 {recent_high:.2f} / 低点 {recent_low:.2f}（支撑/压力参考）")

    # 量能变化
    if len(vols) >= 10:
        v5 = sum(vols[-5:]) / 5
        v5_prev = sum(vols[-10:-5]) / 5
        if v5_prev > 0:
            ratio = v5 / v5_prev
            lines.append(f"量能：近5日均量较前5日 {'放量' if ratio > 1 else '缩量'} {abs(ratio - 1) * 100:.0f}%")

    return "\n".join(lines)


def fetch_news_text(code, limit=10):
    """抓取近期新闻/公告标题，返回文本。"""
    ak = _import_akshare()
    if ak is None:
        return "（未安装 akshare，无法获取新闻）"
    try:
        news = ak.stock_news_em(symbol=code)
    except Exception as e:
        return f"（新闻获取失败：{e}）"

    lines = []
    for _, row in news.head(limit).iterrows():
        title = str(row.get("新闻标题", "")).strip()
        t = str(row.get("发布时间", "")).strip()
        src = str(row.get("文章来源", "")).strip()
        if title and title != "nan":
            lines.append(f"[{t[:10]}] {title}（{src}）")
    return "\n".join(lines) if lines else "（无近期新闻）"


def load_api_key():
    """读取 DeepSeek API key：优先环境变量，其次脚本同目录 config.json。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            key = str(cfg.get("deepseek_api_key", "") or "").strip()
            if key:
                return key
        except Exception:
            pass
    return ""


def analyze_stock_text(code, name, industry=""):
    """抓取盘面技术面 + 行业消息面，调用 DeepSeek，返回分析文本（失败返回错误提示）。"""
    api_key = load_api_key()
    if not api_key:
        return ("[!] 未设置 DeepSeek API Key。\n"
                "    两种方式任选其一：\n"
                "    1) 环境变量 DEEPSEEK_API_KEY\n"
                "    2) 脚本同目录 config.json 里的 deepseek_api_key 字段")

    technical = fetch_technical_text(code)
    news = fetch_news_text(code)

    industry_line = f"（所属行业：{industry}）" if industry else ""
    prompt = (
        f"请分析以下 A 股股票，结合其所属行业的消息面与盘面的技术面，给出明确的买入或卖出意见。\n\n"
        f"股票：{name}（{code}）{industry_line}\n\n"
        f"【盘面技术面】\n{technical}\n\n"
        f"【近期消息面（含所属行业动向）】\n{news}\n\n"
        f"请从以下角度给出结论：\n"
        f"1. 技术面：价格走势、量能、换手率、量比、振幅、估值等，判断当前多空强弱与关键支撑/压力位\n"
        f"2. 行业消息面：所属行业的政策、景气度、资金动向等利好/利空因素\n"
        f"3. 明确意见：给出「买入 / 卖出 / 观望」的结论，并说明理由与风险提示\n"
        f"（请明确说明分析仅供参考，不构成投资建议）"
    )
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system",
             "content": "你是一名专业的 A 股股票分析师，擅长结合盘面技术面和行业消息面做客观分析，回答用中文、条理清晰。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "stream": False,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return f"[!] DeepSeek 调用失败：{e}"

    content = (result.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not content:
        return f"[!] DeepSeek 返回为空：{result.get('error', '')}"

    return content


def analyze_stock(code, name, industry=""):
    """终端版：调用 analyze_stock_text 并打印。"""
    print(f"\n正在分析 {name}({code}) ...")
    content = analyze_stock_text(code, name, industry)
    print("\n" + "=" * 72)
    print(f"DeepSeek 分析结论：{name}({code})")
    print("=" * 72)
    print(content)
    print("=" * 72)


def prompt_analyze(stock_list):
    """结果输出后，让用户选择一只股票做 DeepSeek 分析。"""
    if not stock_list:
        return
    try:
        raw = input("\n要用 DeepSeek 分析其中某只股票吗？输入序号，回车跳过：").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if raw == "":
        return
    if raw.isdigit() and 1 <= int(raw) <= len(stock_list):
        s = stock_list[int(raw) - 1]
        analyze_stock(s.get("code", ""), s.get("name", ""), s.get("industry", ""))
    else:
        print("序号无效，已跳过分析。")
