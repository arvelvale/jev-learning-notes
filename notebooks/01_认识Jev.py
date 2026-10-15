import importlib.util
if importlib.util.find_spec("typesafe_sdk") is None:
    raise SystemExit("缺少 typesafe_sdk：请先 pip install typesafe-sdk==0.7.0")

#安装包的名字是 typesafe-sdk，Python 导入名是 typesafe_sdk。。


#导入与配置
import os
import json
import time
import math
from datetime import datetime, timezone
from importlib.metadata import version
from typesafe_sdk import (
    Choice, Score, Noul, NoulCriteria, TypeSafeClient,
    TypeSafeAuthenticationError, RetryPolicy,
)

MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-1.13.0")
RUN_MODE = os.environ.get("JEV_RUN_MODE", "auto")
API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
if RUN_MODE not in {"live", "offline", "auto"}:
    raise ValueError("JEV_RUN_MODE 只能是 live、offline 或 auto")
if RUN_MODE == "live" and not API_KEY:
    raise RuntimeError("JEV_RUN_MODE=live 需要真实密钥：请配置 TYPESAFE_API_KEY；仅学习可改用默认 auto（无密钥自动离线）")
client = None if (RUN_MODE == "offline" or not API_KEY) else TypeSafeClient(
    api_key=API_KEY, model=MODEL, timeout=30, retry=RetryPolicy(max_retries=0))
mode_note = ("在线优先：本次会话调用真实模型" if client is not None else
             ("强制离线（JEV_RUN_MODE=offline）" if RUN_MODE == "offline" else
              "未检测到 TYPESAFE_API_KEY → 离线替身；配置密钥后重跑本格即切在线实测"))
print("模式：", RUN_MODE, "｜", mode_note, "｜SDK：", version("typesafe-sdk"), "｜模型配置：", MODEL)

#连通性测试
# 用一条 Noul 检查真实响应能否返回。网络、限流与输入错误直接抛出，不伪装成不确定判断。

PING = {"source": "offline", "reason": "未发起连通性请求（无 client：未配置密钥或强制离线）"}
if client is not None:
    try:
        ping = client.system_one("你好", {"greeting": Noul(
            instructions="这段文字是否在打招呼？")})
        PING = {"source": "live", "model": ping.model,
                "input_tokens": ping.usage.input_tokens,
                "output_tokens": ping.usage.output_tokens}
    except TypeSafeAuthenticationError:
        if RUN_MODE == "live":
            raise
        client.close()
        client = None
        PING["reason"] = "401 鉴权失败，仅教学模式允许回退"
print(json.dumps(PING, ensure_ascii=False))

# source=live 表示这一次连通性请求成功；仍要查看后续实验记录，不能用它代替整章验收。

# 沿用参考模板的 `_FakeAnswer` 与 `_FakeResponse` 访问方式。

class _FakeAnswer:
    def __init__(self, type_, **values):
        self.type = type_
        for name, value in values.items():
            setattr(self, name, value)


class _FakeResponse:
    def __init__(self, answers):
        self.answers = answers
        self.choices = {k: v for k, v in answers.items() if v.type == "choice"}
        self.scores = {k: v for k, v in answers.items() if v.type == "score"}
        self.nouls = {k: v for k, v in answers.items() if v.type == "noul"}
        self.model = "人工示例，非模型预测"
        self.usage = _FakeAnswer("usage", input_tokens=0, output_tokens=0)
# 定义两种示例答案构造器

def fake_choice(probabilities, confidence):
    return _FakeAnswer("choice", choice=max(probabilities, key=probabilities.get),
                       probabilities=probabilities, confidence=confidence)

def fake_score(probabilities, legend, confidence):
    return _FakeAnswer("score", score=sum(k * p for k, p in probabilities.items()),
                       probabilities=probabilities, legend=dict(enumerate(legend)),
                       confidence=confidence)


#统一调用入口

# 每次调用记录来源、模型与 token 用量。

CALL_LOG = []
class TS:
    def call(self, state, questions, offline_answers, label):
        start = time.perf_counter()
        source = "live"
        if client is None:
            response, source = _FakeResponse(offline_answers), "offline"
        else:
            try:
                response = client.system_one(state, questions)
            except TypeSafeAuthenticationError:
                if RUN_MODE != "auto":
                    raise
                response, source = _FakeResponse(offline_answers), "offline"
        validate_response(response, questions)
        CALL_LOG.append({"case": label, "source": source, "model": response.model,
                         "seconds": time.perf_counter() - start if source == "live" else None,
                         "input_tokens": response.usage.input_tokens,
                         "output_tokens": response.usage.output_tokens})
        if source == "offline":
            print("离线替身（未调用真实模型）：", label, "；人工答案，仅演示代码路径")
        return response


ts = TS()

# 与参考模板相比，这里增加了严格 live 模式和逐次记录。保留 401 教学回退，但超时、429 等错误继续失败，防止验收被回退掩盖。
#
# 校验结构与数值契约；只断言接口应满足的性质，不断言真实模型必须预测某个标签。

def validate_response(response, questions):
    if set(response.answers) != set(questions):
        raise ValueError("答案 ID 与问题 ID 不一致")
    for key, question in questions.items():
        answer = response.answers[key]
        if isinstance(question, Noul):
            if not 0 <= answer.noul <= 1:
                raise ValueError("Noul 超出概率范围")
            continue
        probabilities = answer.probabilities
        if not all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities.values()):
            raise ValueError("概率值无效")
        if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.02):
            raise ValueError("概率之和偏离 1")
        if not 0 <= answer.confidence <= 1:
            raise ValueError("confidence 超出范围")
        if isinstance(question, Choice):
            if set(probabilities) != set(question.criteria):
                raise ValueError("Choice 选项集合不一致")
            if answer.choice not in probabilities:
                raise ValueError("Choice 标签不在选项中")
        else:
            expected = sum(int(k) * p for k, p in probabilities.items())
            if not math.isclose(answer.score, expected, abs_tol=0.03):
                raise ValueError("Score 与概率加权期望不一致")

# **观察与理解：** 容差用于服务端数值舍入。结构检查通过只说明响应可读取，不证明语义判断正确。
#
# 显示结果时统一列出类型、概率和置信度；Noul 不额外制造 confidence 字段。

def show(response):
    rows = {}
    for key, answer in response.answers.items():
        rows[key] = {name: getattr(answer, name) for name in
                     ("type", "choice", "score", "noul", "confidence", "probabilities", "legend")
                     if hasattr(answer, name)}
    print(json.dumps(rows, ensure_ascii=False, indent=2))

#实验 A：一次调用，三种原语

EXP_STATE = ("用户：我连续三天没能把 Stripe 账户连上，集成一直失败，"
             "每耽误一天都在丢订单，请尽快处理！")

EXP_QUESTIONS = {
    "urgency": Noul(instructions="这条消息是否表达了紧迫性？"),
    "intent": Choice(instructions="用户的主要诉求是什么？", criteria={
        "troubleshoot": "希望排查或修复连接问题",
        "refund": "要求退款或赔偿",
        "inquiry": "只是咨询，不要求立即处理"}),
    "frustration": Score(instructions="用户的受挫程度如何？", criteria=[
        "平静，仅陈述事实", "有些不满", "强烈不满，有流失风险"]),
}
EXP_OFFLINE = {
    "urgency": _FakeAnswer("noul", noul=0.94),
    "intent": fake_choice({"troubleshoot": 0.82, "refund": 0.03, "inquiry": 0.15}, 0.8),
    "frustration": fake_score({0: 0.05, 1: 0.35, 2: 0.6},
                              ["平静", "有些不满", "强烈不满"], 0.87),
}

resp_a = ts.call(EXP_STATE, EXP_QUESTIONS, EXP_OFFLINE, "实验A·三原语一次调用")
show(resp_a)



groups = {0.2: [1] * 2 + [0] * 8, 0.8: [1] * 8 + [0] * 2}
for p, labels in groups.items():
    print(f"预测概率 {p}：{len(labels)} 条，实际发生 {sum(labels) / len(labels):.0%}，"
          f"组内仍有 {labels.count(0)} 次没发生")

#定义状态与问题

INCIDENTS = [
    {"id": "runner_injury", "state": {
        "event": "海湾夜跑 10 公里", "time": "19:12",
        "message": "2.4 公里蓝旗处有跑者脚踝扭伤，无法继续前进，请派人协助。"}},
    {"id": "water_station", "state": {
        "event": "海湾夜跑 10 公里", "time": "19:13",
        "message": "7 号补水站的纸杯用完了，但仓库还有整箱，麻烦补送两箱。"}},
    {"id": "shirt_pickup", "state": {
        "event": "海湾夜跑 10 公里", "time": "19:15",
        "message": "完赛T恤的领取处是先到先得吗？明天早上还能领吗？"}},
]

INCIDENT_QUESTIONS = {
    "incident_type": Choice(instructions="这条赛事消息主要属于哪类事件？", criteria={
        "medical_help": "跑者受伤或需要现场医疗",
        "supplies": "物资补给问题",
        "event_info": "赛事信息咨询"}),
    "priority": Score(instructions="需要多快处理？", criteria=[
        "可赛后处理", "30 分钟内", "10 分钟内", "必须立即"]),
    "requires_human": Noul(instructions="是否需要人工到场介入？"),
}

#逐条调用并由代码分流

ROUTES = {"medical_help": "现场负责人 / 医疗人员",
           "supplies": "补给组（优先处理）",
           "event_info": "自动回复赛事 FAQ（模拟）"}
LEVELS = ["可赛后", "30分钟", "10分钟", "立即"]
OFFLINE = [
    {"incident_type": fake_choice({"medical_help": 0.9, "supplies": 0.06, "event_info": 0.04}, 0.9),
     "priority": fake_score({0: 0, 1: 0, 2: 0.15, 3: 0.85}, LEVELS, 0.9),
     "requires_human": _FakeAnswer("noul", noul=0.93)},
    {"incident_type": fake_choice({"supplies": 0.88, "medical_help": 0.06, "event_info": 0.06}, 0.85),
     "priority": fake_score({0: 0.1, 1: 0.7, 2: 0.2, 3: 0}, LEVELS, 0.8),
     "requires_human": _FakeAnswer("noul", noul=0.22)},
    {"incident_type": fake_choice({"event_info": 0.9, "supplies": 0.05, "medical_help": 0.05}, 0.9),
     "priority": fake_score({0: 0.8, 1: 0.2, 2: 0, 3: 0}, LEVELS, 0.85),
     "requires_human": _FakeAnswer("noul", noul=0.04)},
]

for inc, off in zip(INCIDENTS, OFFLINE):
    r = ts.call(inc["state"], INCIDENT_QUESTIONS, off, f"指挥台·{inc['id']}")
    t = r.choices["incident_type"].choice
    human = r.nouls["requires_human"].noul >= 0.7   # 阈值是应用政策
    print(f"{inc['id']:14s} → {ROUTES[t]}{'（转人工）' if human else ''}")

EX_QUESTIONS = {
    "severity": Score(instructions="这张工单的严重程度如何？", criteria=[
        "轻微，咨询或小问题", "一般，影响使用但有替代办法", "严重，核心功能不可用或在丢钱",
        "涉及人身安全或重大资损"]),
    "wants_human": Noul(instructions="用户是否明确要求转人工或真人客服？"),
    "auto_can_solve": Noul(instructions="用常见自助 FAQ 或自动流程能否直接解决这个问题？"),
}
EX_TICKETS = [
    {"id": "charger_smoke", "state": "充电器冒烟，刚买两天，插上就发烫，有焦味。"},
    {"id": "how_to_invoice", "state": "请问发票怎么开？是在订单页面点击申请吗？"},
    {"id": "angry_human", "state": "你们这个机器人答非所问，我已经问了三遍了，给我转真人！"},
    {"id": "payment_lost", "state": "付款成功但订单没生成，钱被扣了三天了，客服一直没回。"},
]
EX_OFFLINE = {
    "severity": fake_score({0: 0.05, 1: 0.15, 2: 0.3, 3: 0.5}, ["轻微", "一般", "严重", "安全/资损"], 0.8),
    "wants_human": _FakeAnswer("noul", noul=0.1),
    "auto_can_solve": _FakeAnswer("noul", noul=0.1),
}

def should_escalate(r):
    sev = r.scores["severity"].score
    wants = r.nouls["wants_human"].noul
    auto = r.nouls["auto_can_solve"].noul
    if sev >= 2.5:                      # 安全/资损：不论其他因素一律升级（确定性规则）
        return True, "高严重度，强制升级"
    if wants >= 0.7:
        return True, "用户明确要求真人"
    if sev >= 1.5 and auto < 0.5:
        return True, "较严重且自动流程难以解决"
    return False, "自动流程处理"

for t in EX_TICKETS:
    r = ts.call(t["state"], EX_QUESTIONS, EX_OFFLINE, f"练习·{t['id']}")
    go, why = should_escalate(r)
    print(f"{t['id']:15s} severity={r.scores['severity'].score:.2f} "
          f"wants_human={r.nouls['wants_human'].noul:.2f} "
          f"auto_can_solve={r.nouls['auto_can_solve'].noul:.2f} → "
          f"{'升级人工' if go else '自动处理'}（{why}）")



if client is not None:
    client.close()
AUDIT = {
    "kind": "jev_execution_audit",
    "executed_at_utc": datetime.now(timezone.utc).isoformat(),
    "sdk": version("typesafe-sdk"), "requested_model": MODEL,
    "mode": RUN_MODE, "ping": PING,
    "real_calls": sum(x["source"] == "live" for x in CALL_LOG),
    "offline_calls": sum(x["source"] == "offline" for x in CALL_LOG),
    "cases": CALL_LOG,
    "validation_status": "live_executed_requires_review" if (
        PING["source"] == "live" and CALL_LOG
        and all(x["source"] == "live" for x in CALL_LOG)
    ) else "offline_only_not_model_evidence",
}
print(json.dumps(AUDIT, ensure_ascii=False, indent=2))

