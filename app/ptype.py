"""帖子类型分类:把采集到的笔记分成 商业硬文 / 产品软文 / 教程 / 测评 / 其他,并判断它是否和食品饮品产品相关。
用途:复刻时按类型挑参考笔记(一张拆解卡只用同一类),避免把教程、软文、测评、硬文混在一起学。

和 notes.ai_type(9 类内容类型,早期的分类)并存,互不影响;新分类用 notes.kind / kind_sub / kind_rel。
规则特征(价格词、步骤用量词、对比词、第一人称体验词)只作为线索交给模型,最终类型由模型判断并给出依据和把握。"""
import re

from . import llm

KINDS = ["商业硬文", "产品软文", "教程", "测评", "其他"]
SUBS = {"产品软文": ["体验分享", "经验科普"], "测评": ["单款", "多款对比"], "教程": ["配方", "玩法"], "商业硬文": ["商家自述", "品牌产品介绍"]}

THIN = 40   # 正文少于这么多字算「内容不足」:相关性按标题判断,复刻时不当参考
_PRICE = re.compile(r"(¥|￥|\d+(?:\.\d+)?\s*元|到手价|价格|售价|下单|拍下|链接|优惠|领券|折扣|包邮|旗舰店|净含量|规格|配料表|保质期|品牌|厂家|产地|招商|代理|批发|定制|我们家|本品)")
_STEPS = re.compile(r"(步骤|做法|食材|配方|用料|第[一二三四五六七八九十1-9]步|[①②③④⑤]|(?:^|\n)\s*\d+[\.、]|\d+\s*(?:g|克|ml|毫升|勺|匙|分钟|度))")
_COMPARE = re.compile(r"(测评|评测|横评|对比|vs|VS|红黑榜|盲测|PK|哪个好|哪款|区别|排行|榜单|打分|口感|优点|缺点)")
_EXP = re.compile(r"(我|自己|买了|回购|喝了|吃了|用了|入手|试了|亲测|真的|感觉|体验|坚持)")

KIND_SYSTEM = """你是小红书食品饮品类笔记的分类员。逐篇判断「帖子类型」,每篇只选一个:
- 商业硬文:品牌 / 商家 / 厂家视角的产品介绍或推销。常见特征:价格、规格、净含量、配料成分罗列、购买方式、招商 / 定制 / 批发、「我们家」「本品」。商家自述卖点也算,哪怕没写价格。子类:商家自述 / 品牌产品介绍。
- 产品软文:顾客或普通用户视角的体验分享、卖点、观点,借某个产品或某类产品讲感受。子类:体验分享(亲测、感受、推荐)/ 经验科普(吃喝某产品的经验加注意事项、科普提醒)。
- 教程:以配方、用量、步骤、流程为主,教人做出某道菜或饮品。笔记里夹带具体品牌产品也算教程,只要主体是步骤和配方;如果主体是在讲「用某产品有多好」,算产品软文。子类:配方(有明确用量和步骤)/ 玩法(简单的喝法、吃法、搭配思路)。优先级规则:只要正文里有明确的食材用量和做法步骤(配方),即使标题叫「攻略」「分享」、结尾带产品选购或注意事项,也算教程·配方;产品软文·经验科普只给「没有完整配方步骤、主要讲选购 / 食用经验和注意事项」的笔记。补充:主体是「怎么做、怎么吃、怎么搭」的做法清单、吃法合集、搭配思路,即使没有完整用量,也算教程·玩法,不算经验科普。
- 测评:有评价维度的评测(口感、优缺点、打分、排名、对比)。子类:单款(只评一款产品)/ 多款对比(一次评多款产品,哪怕是同一个品牌的多款;排名、红黑榜、对比、横评、热量对比表都算)。只讲感受和卖点、没有评价维度的单款体验,算产品软文,不算测评。
- 其他:和食品饮品产品无关的,或实在看不出类型的。以下一律归「其他」且 related 填 false:咖啡店 / 餐厅的兼职、招聘、入职、带新人,开店 / 创业 / 经营记录,探店打卡,旅行游记,职场,行业从业技巧(如咖啡师萃取参数)。判断标准:这篇是不是在讲某个产品、某道菜或某种饮品「怎么吃、怎么做、怎么选、好不好」;不是就归其他。
另外判断 related:这篇内容是否围绕食品、饮品、调味、烹饪、冲泡这类产品或做法(true / false)。探店、旅行、职场、招聘、创业经营这类 false。
商业硬文补充:商家 / 品牌账号推广自家产品、带购买引导的,即使文风像用户体验,也算商业硬文。
正文很短或为空的笔记会标注「正文不足」:related 只按标题判断(标题明显围绕食品饮品做法 / 产品就填 true),conf 不超过 0.5,类型按标题尽量判断。
每篇会给出规则统计出来的线索(价格购买词、步骤用量词、对比词、第一人称体验词的个数),仅供参考,以内容为准。
严格输出 JSON:{"items": [{"id": 笔记编号, "kind": "上面五类之一", "sub": "子类,没有就空字符串", "related": true或false, "conf": 0到1的把握, "why": "一句话依据,<=25字"}]}"""


def features(title: str, content: str) -> dict:
    t = f"{title or ''}\n{content or ''}"
    return {"price": len(_PRICE.findall(t)), "steps": len(_STEPS.findall(t)), "compare": len(_COMPARE.findall(t)), "exp": len(_EXP.findall(t))}


def classify(notes: list[dict]) -> dict[int, dict]:
    """给一批笔记(id、title、content)分类。返回 {id: {kind, sub, related, conf, why}},模型没返回的篇不在结果里。"""
    parts = []
    for n in notes:
        f = features(n.get("title"), n.get("content"))
        body = (n.get("content") or "").strip()
        thin = "(正文不足)" if len(body) < THIN else ""
        parts.append(f"【{n['id']}】标题:{n.get('title') or ''}\n正文{thin}:{body[:700]}\n线索:价格购买词 {f['price']},步骤用量词 {f['steps']},对比词 {f['compare']},第一人称体验词 {f['exp']}")
    items = llm.chat(KIND_SYSTEM, "\n\n".join(parts), temperature=0.2).get("items") or []
    out = {}
    for it in items:
        try:
            kind = it.get("kind") if it.get("kind") in KINDS else "其他"
            sub = str(it.get("sub") or "").strip()
            conf = max(0.0, min(1.0, float(it.get("conf") or 0)))
            out[int(it["id"])] = {"kind": kind, "sub": sub if sub in SUBS.get(kind, []) else "", "related": bool(it.get("related", True)),
                                  "conf": round(conf, 2), "why": str(it.get("why") or "")[:60]}
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------- 复刻时按类型用 ----------
# 每个类型的写作要求:拼进写稿的「额外要求」里,让这一篇写成这个类型,而不是被参考笔记带偏
RULES = {
    "产品软文": "【帖子类型:产品软文】用普通用户的视角,第一人称写真实的使用体验、感受和观点,借产品讲卖点;"
              "不要像商家介绍(不罗列规格、配料、价格、购买链接),也不要写成一步步的配方教程或打分评测。"
              "禁止出现:材料清单、具体用量(克数、毫升、几勺)、编号步骤、水温时间这类做法细节;"
              "吃法只能一笔带过(如「撒在拿铁上」「拌进燕麦」),重点写为什么喜欢、用起来的感受、带来的变化和个人看法。",
    "教程": "【帖子类型:教程】主体是配方、用量和步骤:先列材料和用量,再按顺序写步骤(编号),给出温度、时间等关键细节和成功要点;"
          "产品只作为其中一样材料自然出现,不要大篇幅夸产品,也不要写成测评。",
    "测评": "【帖子类型:测评】要有明确的评价维度(如口感、香气、甜度、便利性、性价比)和结论:写清优点、缺点、适合谁;"
          "多款对比要给出对比结论或排名;语气客观,不要一味夸,不要写成配方教程。",
    "商业硬文": "【帖子类型:商业硬文】品牌 / 商家视角,清楚介绍产品:是什么、成分规格、适用场景、怎么买(只能是平台内);"
              "只写产品资料里有的信息,不编造数据和资质;用词合规,不夸大功效。",
}
DEFAULT_MIX = {"产品软文": 6, "测评": 4}                       # 有产品的任务:软文为主,加测评
DEFAULT_MIX_FREE = {"产品软文": 5, "教程": 3, "测评": 2}       # 没有产品的任务(只按赛道关键词)
MIN_REFS = 2                                                   # 某个类型可用的参考笔记少于这个数,就不做这一类


def parse_mix(mix) -> dict[str, int]:
    """页面传来的类型比例 {类型: 份数},只保留已知类型(不含「其他」)、份数大于 0 的。"""
    out = {}
    for k, v in (mix or {}).items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if k in RULES and n > 0:
            out[k] = n
    return out


def split_count(total: int, weights: dict) -> dict:
    """按权重把篇数分给各类型(最大余数法);篇数够的话每个类型至少 1 篇。"""
    ks = [k for k, w in weights.items() if w > 0]
    if not ks or total <= 0:
        return {}
    sw = sum(weights[k] for k in ks)
    raw = {k: total * weights[k] / sw for k in ks}
    out = {k: int(raw[k]) for k in ks}
    if total >= len(ks):
        for k in ks:
            out[k] = max(out[k], 1)
    while sum(out.values()) > total:
        k = max((k for k in ks if out[k] > 1), key=lambda k: out[k] - raw[k], default=None)
        if k is None:
            break
        out[k] -= 1
    while sum(out.values()) < total:
        k = max(ks, key=lambda k: raw[k] - out[k])
        out[k] += 1
    return {k: v for k, v in out.items() if v}


def usable(n: dict) -> bool:
    """这篇笔记能不能当某一类的参考:已分类、和产品相关、类型不是「其他」、正文够长、把握不太低。"""
    return (n.get("kind_status") == "done" and n.get("kind") in RULES and n.get("kind_rel") != 0
            and len((n.get("content") or "").strip()) >= THIN and (n.get("kind_conf") or 0) >= 0.5)


def check(title: str, content: str) -> str:
    """给写好的稿子判一次类型,返回类型名;判不出返回空串。"""
    got = classify([{"id": 1, "title": title, "content": content}]).get(1)
    return got["kind"] if got else ""
