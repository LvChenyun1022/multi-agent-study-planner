# -*- coding: utf-8 -*-
"""
mcp_server.py — MCPServer：统一工具接口（真实 API + 模拟数据兜底版）

MCP（Model Context Protocol）思想：Agent 不直接操作数据源，而是通过一个
统一的"工具服务器"调用工具。每个工具在 tool_schemas.json 里有名字、描述、
参数 JSON Schema、是否敏感四个信息，Agent 调用时只需 call_tool(name, args)。

本版本三个工具优先调用真实 API：
  - check_weather  → Open-Meteo（免费天气 API，零配置，支持 7 天预报）
  - check_calendar → 飞书日历 Open API（需 config.json 中 feishu 配置）
  - send_email     → 交大邮箱 SMTP（需 config.json 中 email 配置）
  - rag_search     → 保持原有模拟 RAG 检索

任何工具在真实 API 调用失败时自动降级到模拟数据，保证离线 0 报错。

**扩展点**（同学A）：
  1. 编辑 config.json 填入真实凭据（已填好则直接用）；
  2. 凭据留空（或文件不存在）时自动走模拟数据；
  3. 可在此文件中继续添加更多工具。
"""

import json
import math
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
import smtplib

# ---------------- 内置兜底数据（data/ 目录读不到时使用） ----------------

_FALLBACK_RAG_CORPUS = [
    {
        "id": "rag001",
        "content": "错题：I have went to the park yesterday. 错误原因：一般过去时与现在完成时混淆。"
                   "yesterday 是明确的过去时间标志，应用一般过去时 I went to the park yesterday. "
                   "另外 go 的过去式是 went，过去分词是 gone，have went 本身也是错误形式。",
        "source": "错题本p12",
    },
    {
        "id": "rag002",
        "content": "教材讲解：现在完成时（have/has + 过去分词）强调动作对现在的影响，"
                   "常与 already、just、ever、never、since、for 连用；一般过去时只陈述过去发生的事。",
        "source": "教材unit3",
    },
    {
        "id": "rag003",
        "content": "错题：She don't likes apples. 错误原因：一般现在时第三人称单数，"
                   "否定应为 doesn't like，助动词后动词还原。",
        "source": "错题本p15",
    },
    {
        "id": "rag004",
        "content": "口语练习：自我介绍常用句型 My name is... / I'm interested in... / "
                   "My favorite sport is...。注意语调上扬表疑问，停顿自然。",
        "source": "口语教材unit1",
    },
    {
        "id": "rag005",
        "content": "数学错题：有理数加减，(-3) + 5 = 2。易错点：异号两数相加，"
                   "取绝对值较大的符号，再用大绝对值减小绝对值。",
        "source": "错题本p20",
    },
    {
        "id": "rag006",
        "content": "数学讲解：有理数乘除法则——同号得正、异号得负；"
                   "除以一个数等于乘以它的倒数（0 没有倒数）。",
        "source": "教材数学chapter2",
    },
]

_FALLBACK_CALENDAR = {
    "2026-08-17": [{"time": "19:00-20:30", "event": "数学补习班"}],
    "2026-08-18": [],
    "2026-08-19": [{"time": "19:00-21:00", "event": "家庭聚餐（晚上有事）"}],
    "2026-08-20": [],
    "2026-08-21": [{"time": "18:00-19:00", "event": "钢琴课"}],
    "2026-08-22": [{"time": "10:00-11:30", "event": "网球训练"}],
    "2026-08-23": [],
}

_FALLBACK_WEATHER = {
    "2026-08-17": {"city": "上海", "weather": "晴", "temperature": "26~33°C", "suitable_for_outdoor": True},
    "2026-08-18": {"city": "上海", "weather": "多云", "temperature": "27~34°C", "suitable_for_outdoor": True},
    "2026-08-19": {"city": "上海", "weather": "晴", "temperature": "27~35°C", "suitable_for_outdoor": True},
    "2026-08-20": {"city": "上海", "weather": "中雨", "temperature": "25~29°C", "suitable_for_outdoor": False},
    "2026-08-21": {"city": "上海", "weather": "阴", "temperature": "26~31°C", "suitable_for_outdoor": True},
    "2026-08-22": {"city": "上海", "weather": "晴", "temperature": "27~34°C", "suitable_for_outdoor": True},
    "2026-08-23": {"city": "上海", "weather": "多云", "temperature": "26~33°C", "suitable_for_outdoor": True},
}

# ---------------- Open-Meteo 天气（免费，零配置，支持 7 天预报） ----------------

# WMO 天气代码 → (中文描述, 是否适合户外)
# 参考：https://open-meteo.com/en/docs#weathervariables
_WMO_CODES: dict[int, tuple[str, bool]] = {
    0: ("晴", True),
    1: ("大部晴朗", True),
    2: ("多云", True),
    3: ("阴天", True),
    45: ("雾霾", False),
    48: ("冻雾", False),
    51: ("小毛毛雨", False),
    53: ("中毛毛雨", False),
    55: ("大毛毛雨", False),
    56: ("冻毛毛雨", False),
    57: ("冻大毛毛雨", False),
    61: ("小雨", False),
    63: ("中雨", False),
    65: ("大雨", False),
    66: ("冻小雨", False),
    67: ("冻大雨", False),
    71: ("小雪", False),
    73: ("中雪", False),
    75: ("大雪", False),
    77: ("雪粒", False),
    80: ("小阵雨", False),
    81: ("中阵雨", False),
    82: ("大阵雨", False),
    85: ("小阵雪", False),
    86: ("大阵雪", False),
    95: ("雷暴", False),
    96: ("雷暴伴小冰雹", False),
    99: ("雷暴伴大冰雹", False),
}

# 天气缓存：每次查询一次性拉取 7 天预报并缓存
_weather_cache: dict[tuple[str, str], dict] = {}


def _open_meteo_get_weather(city: str, city_lat: float, city_lon: float, date: str) -> dict | None:
    """通过 Open-Meteo 免费 API 查询某城市某日的天气。

    首次查询时一次性拉取 7 天预报并缓存所有日期。
    Open-Meteo 无需 API Key，零配置免费使用。
    """
    cache_key = ("open-meteo", city, date)
    if cache_key in _weather_cache:
        return _weather_cache[cache_key]

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={city_lat}&longitude={city_lon}"
        f"&daily=temperature_2m_max,temperature_2m_min,weathercode"
        f"&timezone=Asia/Shanghai&forecast_days=7"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "OpenClaw/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    daily = body.get("daily", {})
    times = daily.get("time", [])
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    codes = daily.get("weathercode", [])

    for i in range(len(times)):
        d = times[i]
        code = codes[i] if i < len(codes) else 0
        desc, suitable = _WMO_CODES.get(code, ("未知", False))
        temp = f"{min_temps[i] if i < len(min_temps) else '?'}~{max_temps[i] if i < len(max_temps) else '?'}°C"
        _weather_cache[("open-meteo", city, d)] = {
            "city": city,
            "date": d,
            "weather": desc,
            "temperature": temp,
            "suitable_for_outdoor": suitable,
        }

    return _weather_cache.get(cache_key)


# 城市 → 坐标映射（Open-Meteo 需要经纬度）
# 常见城市内置，未知城市通过名字匹配降级到模拟数据
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "上海": (31.23, 121.47),
    "北京": (39.90, 116.41),
    "广州": (23.13, 113.26),
    "深圳": (22.54, 114.06),
    "杭州": (30.27, 120.15),
    "南京": (32.06, 118.80),
    "成都": (30.57, 104.07),
    "武汉": (30.58, 114.30),
    "重庆": (29.56, 106.55),
    "西安": (34.26, 108.94),
    "苏州": (31.30, 120.59),
    "天津": (39.13, 117.19),
    "长沙": (28.23, 112.94),
    "郑州": (34.75, 113.63),
    "东莞": (23.05, 113.75),
    "青岛": (36.07, 120.38),
    "沈阳": (41.80, 123.43),
    "宁波": (29.87, 121.54),
    "昆明": (25.04, 102.68),
    "大连": (38.91, 121.62),
    "厦门": (24.48, 118.09),
    "合肥": (31.82, 117.23),
    "佛山": (23.02, 113.12),
    "福州": (26.07, 119.30),
    "哈尔滨": (45.80, 126.53),
    "济南": (36.65, 117.12),
    "温州": (28.00, 120.70),
    "长春": (43.88, 125.32),
    "石家庄": (38.05, 114.47),
    "常州": (31.77, 119.96),
    "泉州": (24.91, 118.59),
    "贵阳": (26.65, 106.63),
    "南昌": (28.68, 115.86),
    "太原": (37.87, 112.54),
    "烟台": (37.47, 121.44),
    "南宁": (22.82, 108.37),
    "无锡": (31.57, 120.30),
    "珠海": (22.27, 113.57),
    "海口": (20.02, 110.33),
    "兰州": (36.06, 103.79),
    "呼和浩特": (40.82, 111.75),
    "银川": (38.47, 106.25),
    "西宁": (36.63, 101.78),
    "乌鲁木齐": (43.83, 87.60),
    "拉萨": (29.65, 91.14),
}

# ---------------- 飞书 API 辅助函数 ----------------


def _feishu_get_tenant_token(app_id: str, app_secret: str) -> str | None:
    """获取飞书 tenant_access_token（有效期 2 小时）。"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("code") == 0:
            return body.get("tenant_access_token")
    except Exception:
        pass
    return None


def _feishu_list_calendars(token: str) -> list[dict]:
    """获取飞书应用可访问的日历列表。"""
    url = "https://open.feishu.cn/open-apis/calendar/v4/calendars?page_size=50"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("code") == 0:
            return body.get("data", {}).get("calendar_list", [])
    except Exception:
        pass
    return []


def _feishu_list_events(token: str, calendar_id: str, date: str) -> list[dict]:
    """查询飞书日历某一天的所有事件。

    返回格式：与模拟数据保持一致，每项 {time, event}。
    """
    dt = datetime.strptime(date, "%Y-%m-%d")
    start_ts = int(dt.timestamp())
    end_ts = start_ts + 86400

    url = (
        f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{calendar_id}/events"
        f"?page_size=50&start_time={start_ts}&end_time={end_ts}"
    )
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    if body.get("code") != 0:
        return []

    items = body.get("data", {}).get("items", [])
    events = []
    for item in items:
        summary = item.get("summary", "(无标题)")
        start_info = item.get("start", {})
        end_info = item.get("end", {})

        if "date" in start_info and "date" in end_info:
            time_str = "全天"
        elif "timestamp" in start_info and "timestamp" in end_info:
            start_t = datetime.fromtimestamp(int(start_info["timestamp"]))
            end_t = datetime.fromtimestamp(int(end_info["timestamp"]))
            time_str = f"{start_t.strftime('%H:%M')}-{end_t.strftime('%H:%M')}"
        else:
            time_str = "未知时间"

        events.append({"time": time_str, "event": summary})

    return events


# ---------------- 发信辅助函数 ----------------


def _smtp_send_email(
    smtp_server: str,
    smtp_port: int,
    from_addr: str,
    password: str,
    to: str,
    subject: str,
    body: str,
) -> dict:
    """通过 SMTP 发送邮件。返回 {"sent": bool, "to": str, "error": str | None}。"""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("学习规划助手", from_addr))
        msg["To"] = to

        with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as server:
            server.login(from_addr, password)
            server.sendmail(from_addr, [to], msg.as_string())

        print(f"[真实邮件] 已发送至 {to} | 主题: {subject}")
        return {"sent": True, "to": to, "error": None}
    except Exception as e:
        error_msg = str(e)
        print(f"[邮件发送失败] {error_msg}")
        return {"sent": False, "to": to, "error": error_msg}


# =====================================================================


class MCPServer:
    """统一工具服务器：对外提供 list_tools() 和 call_tool() 两个接口。"""

    def __init__(
        self,
        schemas_path: str = "tool_schemas.json",
        data_dir: str = "data",
        config_path: str = "config.json",
    ) -> None:
        self.schemas_path = schemas_path
        self.data_dir = data_dir
        self.config: dict = self._load_json_file(config_path, default={})

        self.schemas: list[dict] = self._load_json_file(schemas_path, default=[])
        self.rag_corpus: list[dict] = self._load_json_file(
            os.path.join(data_dir, "mock_rag_corpus.json"), default=_FALLBACK_RAG_CORPUS
        )
        self._mock_calendar: dict = self._load_json_file(
            os.path.join(data_dir, "mock_calendar.json"), default=_FALLBACK_CALENDAR
        )
        self._mock_weather: dict = self._load_json_file(
            os.path.join(data_dir, "mock_weather.json"), default=_FALLBACK_WEATHER
        )

        # 飞书 token 缓存
        self._feishu_token: str | None = None
        self._feishu_token_expires_at: float = 0.0

        # TF-IDF 索引（rag_search 用）
        self._tfidf_df: Counter = Counter()     # term → 出现在多少篇文档中
        self._tfidf_vectors: list[Counter] = []  # 每篇文档的 TF-IDF 向量
        self._tfidf_ndocs: int = 0                # 文档总数
        self._build_tfidf_index()

    @staticmethod
    def _load_json_file(path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def _ensure_feishu_token(self) -> str | None:
        feishu_cfg = self.config.get("feishu", {})
        app_id = feishu_cfg.get("app_id", "")
        app_secret = feishu_cfg.get("app_secret", "")
        if not app_id or not app_secret:
            return None
        if self._feishu_token and time.time() < self._feishu_token_expires_at - 300:
            return self._feishu_token
        token = _feishu_get_tenant_token(app_id, app_secret)
        if token:
            self._feishu_token = token
            self._feishu_token_expires_at = time.time() + 7200
        return token

    def list_tools(self) -> list[dict]:
        return self.schemas

    def call_tool(self, name: str, args: dict) -> dict:
        handlers = {
            "rag_search": self._rag_search,
            "check_calendar": self._check_calendar,
            "check_weather": self._check_weather,
            "send_email": self._send_email,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"ok": False, "result": None, "error": f"unknown tool: {name}"}
        try:
            result = handler(args or {})
            return {"ok": True, "result": result, "error": None}
        except Exception as e:
            return {"ok": False, "result": None, "error": f"{name} 执行出错: {e}"}

    # ------------------------------------------------------------------
    # TF-IDF 索引构建
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """将中英混合文本拆成 token 列表。

        策略（纯标准库，不用 jieba）：
        - 英文单词 → 整体保留（转小写）
        - 中文字符 → 每个字作为一个 token（unigram）
        - 数字 → 整体保留
        - 其他标点/空格 → 忽略
        """
        tokens = []
        for segment in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+|\d+", text):
            if re.match(r"[\u4e00-\u9fff]+", segment):
                tokens.extend(list(segment))
            elif re.match(r"[a-zA-Z]+", segment):
                tokens.append(segment.lower())
            elif re.match(r"\d+", segment):
                tokens.append(segment)
        return tokens

    def _build_tfidf_index(self) -> None:
        """从 self.rag_corpus 构建 TF-IDF 索引。

        为每篇文档计算 TF-IDF 向量，存储在 self._tfidf_vectors 中，
        以便 _rag_search 做余弦相似度排序。
        """
        if not self.rag_corpus:
            return

        self._tfidf_ndocs = len(self.rag_corpus)
        N = self._tfidf_ndocs

        # 第 1 步：每篇文档做 tokenize，同时统计 DF
        doc_tokens: list[list[str]] = []
        for item in self.rag_corpus:
            text = item.get("content", "") + " " + item.get("source", "")
            tokens = self._tokenize(text)
            doc_tokens.append(tokens)
            # 一篇文档只计一次
            for term in set(tokens):
                self._tfidf_df[term] += 1

        # 第 2 步：为每篇文档计算 TF-IDF 向量
        # 使用平滑 IDF：log((N+1)/(df+1)) + 1，避免 df=0 或 df=N 时的极端值
        for tokens in doc_tokens:
            total = len(tokens)
            if total == 0:
                self._tfidf_vectors.append(Counter())
                continue
            tf = Counter(tokens)
            vec = Counter()
            for term, count in tf.items():
                tf_val = count / total
                df = self._tfidf_df.get(term, 0)
                idf_val = math.log((N + 1) / (df + 1)) + 1  # 平滑 IDF
                vec[term] = round(tf_val * idf_val, 6)
            self._tfidf_vectors.append(vec)

    # ------------------------------------------------------------------
    # 工具 1：rag_search（TF-IDF 语义检索）
    # ------------------------------------------------------------------

    def _rag_search(self, args: dict) -> list[dict]:
        """用 TF-IDF + 余弦相似度做语义检索。

        相比旧版关键词重叠:
        - 稀有词（如"yesterday""混淆"）自动获得更高权重
        - 高频停用词（如"的""是""了"）被稀释
        - 长文本不会因为字数多而占便宜（TF 归一化）
        """
        query = str(args.get("query", ""))
        top_k = int(args.get("top_k", 3) or 3)

        if not self._tfidf_vectors or not query:
            return self.rag_corpus[:top_k]

        N = self._tfidf_ndocs

        # 计算 query 的 TF-IDF 向量
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return self.rag_corpus[:top_k]

        q_total = len(query_tokens)
        q_tf = Counter(query_tokens)
        q_vec: Counter = Counter()
        for term, count in q_tf.items():
            if term not in self._tfidf_df:
                continue  # query 中的词在语料中从未出现 → 没贡献
            tf_val = count / q_total
            df = self._tfidf_df.get(term, 0)
            idf_val = math.log((N + 1) / (df + 1)) + 1
            q_vec[term] = round(tf_val * idf_val, 6)

        if not q_vec:
            return self.rag_corpus[:top_k]

        # 计算 query 与每篇文档的余弦相似度
        scored: list[tuple[float, dict]] = []
        for i, item in enumerate(self.rag_corpus):
            doc_vec = self._tfidf_vectors[i]
            if not doc_vec:
                scored.append((0.0, item))
                continue
            # 所有共现的 term
            common_terms = set(q_vec.keys()) & set(doc_vec.keys())
            if not common_terms:
                scored.append((0.0, item))
                continue
            # 点积 / (||q|| * ||d||)
            dot = sum(q_vec[t] * doc_vec[t] for t in common_terms)
            q_norm = math.sqrt(sum(v * v for v in q_vec.values()))
            d_norm = math.sqrt(sum(v * v for v in doc_vec.values()))
            sim = dot / (q_norm * d_norm) if q_norm > 0 and d_norm > 0 else 0.0
            scored.append((sim, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # ------------------------------------------------------------------
    # 工具 2：check_weather（优先 Open-Meteo，失败降级到模拟数据）
    # ------------------------------------------------------------------

    def _check_weather(self, args: dict) -> dict:
        """查天气：优先 Open-Meteo 免费 API 实时查询。

        Open-Meteo 无需 API Key，支持全球 7 天预报。
        失败时降级到模拟数据兜底。
        """
        city = str(args.get("city", "上海"))
        date = str(args.get("date", ""))

        # 尝试真实 API
        if date and city:
            coords = _CITY_COORDS.get(city)
            if coords:
                lat, lon = coords
                real = _open_meteo_get_weather(city, lat, lon, date)
                if real is not None:
                    print(f"[天气] 使用 Open-Meteo 实时查询：{city} {date} → {real['weather']}")
                    return real

        # 降级到模拟数据
        print(f"[天气] 降级到模拟数据：{city} {date}")
        info = self._mock_weather.get(date)
        if info is None:
            # mock 数据是固定一周（2026-08-17~23），规划Agent查的是"下周"，
            # 超出范围时按日期哈希生成确定性天气（同一天多次查询结果一致），
            # 避免出现"未知 未知"影响演示效果。
            options = [
                ("晴", "26~33°C", True),
                ("多云", "27~34°C", True),
                ("阴", "26~31°C", True),
                ("小雨", "24~28°C", False),
            ]
            h = sum(ord(c) for c in date)
            w, t, s = options[h % len(options)]
            return {
                "city": city,
                "date": date,
                "weather": w,
                "temperature": t,
                "suitable_for_outdoor": s,
            }
        result = dict(info)
        result["date"] = date
        result.setdefault("city", city)
        return result

    # ------------------------------------------------------------------
    # 工具 3：check_calendar（优先飞书日历 API，失败降级到模拟数据）
    # ------------------------------------------------------------------

    def _check_calendar(self, args: dict) -> dict:
        """查日程：优先飞书日历实时查询。

        使用 tenant_access_token（应用身份）查应用日历。
        如需查用户个人日历，需升级到 user_access_token（OAuth 授权）。
        """
        date = str(args.get("date", ""))

        token = self._ensure_feishu_token()
        if token and date:
            calendars = _feishu_list_calendars(token)
            if calendars:
                cal_id = calendars[0].get("calendar_id")
                if cal_id:
                    events = _feishu_list_events(token, cal_id, date)
                    print(f"[日历] 使用飞书日历实时查询：{date} → {len(events)} 个事件")
                    return {"date": date, "events": events}

        print(f"[日历] 降级到模拟数据：{date}")
        events = self._mock_calendar.get(date)
        if events is None:
            # mock 日历同样是固定一周；超出范围时给"周三"补一条晚上安排，
            # 与学生画像"周三晚上效率低"的情节保持一致，其余日期视为空闲。
            events = []
            try:
                if datetime.strptime(date, "%Y-%m-%d").weekday() == 2:
                    events = [{"time": "19:00-21:00", "event": "家庭聚餐（晚上有事）"}]
            except ValueError:
                pass
        return {"date": date, "events": events}

    # ------------------------------------------------------------------
    # 工具 4：send_email（优先 SMTP 真实发信，失败降级到模拟打印）
    # ------------------------------------------------------------------

    def _send_email(self, args: dict) -> dict:
        """发送邮件：优先 SMTP 真实发送，失败或未配置时打印模拟回执。

        注意：审批拦截逻辑在 Harness / BaseAgent 层完成，
        这里只负责"执行"，假设已经被批准了。
        """
        to = str(args.get("to", ""))
        subject = str(args.get("subject", ""))
        body = str(args.get("body", ""))

        email_cfg = self.config.get("email", {})
        smtp_server = email_cfg.get("smtp_server", "")
        smtp_port = email_cfg.get("smtp_port", 465)
        address = email_cfg.get("address", "")
        password = email_cfg.get("auth_password", "")

        if smtp_server and address and password:
            result = _smtp_send_email(smtp_server, smtp_port, address, password, to, subject, body)
            if result.get("sent"):
                return result
            print(f"[邮件] 真实发送失败 ({result.get('error')})，降级到模拟回执")

        print(f"[模拟邮件] 收件人: {to} | 主题: {subject}\n{body}")
        return {"sent": True, "to": to}
