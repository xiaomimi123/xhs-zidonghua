import json
import re
import time
import httpx
from .db import get_settings


CHAT_WAITS = (0, 2, 6)  # 最多试 3 次,间隔 2 秒、6 秒


def chat(system: str, user: str, temperature: float = 1.0) -> dict:
    """调用 DeepSeek(OpenAI 兼容),要求返回 JSON 对象。
    网络抖动、限流(429)、服务暂不可用(5xx)、返回的不是合法 JSON,这几种偶发情况自动重试;
    Key 错误、余额不足这类重试也没用的,直接报错。"""
    s = get_settings()
    if not s["deepseek_api_key"]:
        raise RuntimeError("请先在「设置」里填写 DeepSeek API Key")
    last: Exception = RuntimeError("DeepSeek 调用失败")
    for wait in CHAT_WAITS:
        if wait:
            time.sleep(wait)
        try:
            resp = httpx.post(
                s["deepseek_base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {s['deepseek_api_key']}"},
                json={
                    "model": s["deepseek_model"],
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "response_format": {"type": "json_object"},
                    "temperature": temperature,
                },
                timeout=180,
            )
        except httpx.HTTPError as e:
            last = RuntimeError(f"连不上 DeepSeek:{e.__class__.__name__}")
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            last = RuntimeError(f"DeepSeek 返回 {resp.status_code}:{resp.text[:120]}")
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"DeepSeek 返回 {resp.status_code}:{resp.text[:200]}")
        try:
            return json.loads(resp.json()["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError):
            last = RuntimeError("DeepSeek 返回的内容不是合法的 JSON")
    raise RuntimeError(f"{last}(已自动重试 {len(CHAT_WAITS)} 次)")


def ping(s: dict) -> dict:
    """测试连通:发一条极短的消息,几乎不花钱。s 是要测试的配置。"""
    base, key, model = (s.get("deepseek_base_url") or "").rstrip("/"), s.get("deepseek_api_key") or "", s.get("deepseek_model") or "deepseek-chat"
    if not (base and key):
        return {"ok": False, "msg": "请先填写接口地址和 API Key"}
    t0 = time.time()
    try:
        r = httpx.post(base + "/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=30,
                       json={"model": model, "messages": [{"role": "user", "content": "只回复 OK"}], "max_tokens": 8})
    except httpx.HTTPError as e:
        return {"ok": False, "msg": f"连不上 {base}:{e.__class__.__name__}。检查地址是否正确、服务器能否访问外网"}
    ms = int((time.time() - t0) * 1000)
    if r.status_code == 200:
        return {"ok": True, "msg": f"连接正常,模型 {model} 响应 {ms} 毫秒"}
    hint = {401: "API Key 不正确", 402: "账户余额不足", 404: "接口地址或模型名不对", 429: "请求太频繁"}.get(r.status_code, "")
    return {"ok": False, "msg": f"返回 {r.status_code}" + (f":{hint}" if hint else "") + f"({r.text[:120]})"}


def _notes_text(notes: list[dict], body_len: int = 900) -> str:
    return "\n\n".join(
        f"【笔记{i+1}】赞{n.get('likes',0)} 藏{n.get('collects',0)} 评{n.get('comments',0)}\n"
        f"标题:{n['title']}\n正文:{(n.get('content') or '')[:body_len]}\n"
        f"话题:{' '.join('#'+t for t in (n.get('tags') or []))}"
        for i, n in enumerate(notes)
    )


def _project_text(p: dict) -> str:
    kws = p.get("keywords") or []
    kw = "、".join(kws) + ("(这些是彼此独立的搜索词,不是要放进同一篇笔记里的组合)" if len(kws) > 1 else "")
    return f"赛道:{p.get('track','')}\n关键词:{kw}\n想做的内容方向:{p.get('direction') or '未指定'}"


def _account_text(p: dict) -> str:
    """任务指定了发布账号时,把「按这个账号数据挑出的选题角度」和「账号里表现好的标题」交给规划和写作。"""
    out = []
    if (p.get("angle_pref") or "").strip():
        out.append(f"优先的选题角度(按这个账号的数据表现挑的):{p['angle_pref'].replace(',', '、')}")
    if (p.get("account_style") or "").strip():
        out.append(f"这个账号里表现较好的笔记标题(只学选题方向和语气,不要照抄):\n{p['account_style']}")
    return "\n" + "\n".join(out) if out else ""


def _kb_text(kb: str) -> str:
    return f"\n\n赛道知识库(平台规则与品类经验,优先遵守):\n{kb}" if kb.strip() else ""


# ---------- 拆解 ----------
ANALYZE_SYSTEM = """你是小红书爆款内容分析师。阅读给定赛道下的多篇高互动笔记和「样本统计」,
提炼可直接指导仿写的「拆解卡」。只总结方法,不要照抄原句。

证据要求:
- 能用样本统计说明的,必须引用统计里的具体数字(如「标题 15-18 字,参考笔记均值 16.4」),不要编造统计里没有的数字。
- 统计注明样本不足时,不要下「爆款比普通更…」的对比结论。
- 字数、段落、emoji 这类写法基准,以统计表里「爆款/热门」组为准;如果「本次参考」和爆款组差得很远(统计下方会有「注意」),说明所选参考不典型,benchmarks 写爆款组的数字,并在 summary 里提一句所选参考偏短或偏长。
- 在 evidence 里为每个字段标注依据:「数据」=主要由样本统计支撑;「归纳」=由阅读笔记内容归纳;「知识库」=来自赛道知识库。

主题要求:
- 先判断这批笔记实际涉及哪些不同的主题(不同的食材、产品、场景),在 topics 里列出,一个主题一个短语(如「姜黄粉吃法」「黄金奶」「速溶咖啡」),只列笔记里真实出现的。
- 用户填的关键词只是搜索词,可能互不相关,不是要组合在一起的主题。不要把它们当成一个整体,不要把不同主题糅成一个套路;标题公式、结构、语气这些写法可以共用,内容主题要分开。
- 拆解卡的名称和总结里不要出现笔记里没有的搭配。

严格输出 JSON:
{"name": "拆解卡短名称,<=12字",
 "topics": ["这批笔记实际涉及的主题,每个一个短语"],
 "summary": "一句话说明这批笔记为什么能爆",
 "benchmarks": ["量化规律,每条引用统计数字,如:标题 15-18 字(参考均值 16.4)", "...共3-6条"],
 "title_formulas": ["标题公式,如:数字+人群+结果+情绪词,附一个示例", "...共3-5条"],
 "hooks": ["开头钩子写法,附示例", "...共2-4条"],
 "structure": "正文结构骨架,按段落列出每段写什么",
 "tone": "语气与人设,如:闺蜜分享、过来人避坑",
 "format": "字数、分段、emoji 与符号的使用习惯(尽量给出数字区间)",
 "tag_strategy": "话题标签的选取规律,并给出5-8个推荐话题",
 "cover_style": "封面图:构图、配色、文字排版、大字标题写法",
 "image_style": "内页配图:张数、每张拍什么、整体视觉风格",
 "dos": ["必须做到的要点", "..."],
 "donts": ["要避免的点,含平台违规风险", "..."],
 "evidence": {"benchmarks": "数据", "title_formulas": "数据/归纳/知识库", "...": "每个字段一项"}}"""


def analyze(project: dict, notes: list[dict], stats_text: str = "", kb: str = "", feedback: str = "") -> dict:
    user = (f"{_project_text(project)}\n\n样本统计:\n{stats_text or '(无)'}{_kb_text(kb)}\n\n"
            f"以下是选中的爆款笔记:\n\n{_notes_text(notes)}" + (f"\n\n修改要求:{feedback}" if feedback else ""))
    return chat(ANALYZE_SYSTEM, user, temperature=0.5)


# ---------- 复刻 ----------
ANGLES_SYSTEM = """你是小红书选题策划。根据赛道、内容方向和拆解卡,为同一批笔记规划互不重复的切入角度,
让每篇针对不同的人群/场景/痛点/形式,避免内容雷同。
选题约束:
- 每个角度只围绕**一个**主题,主题从「可写主题」里选;关键词是并列的搜索词,不是要在同一篇里组合的东西。
- 不要把互不相关的食材、产品、场景强行搭配在一起,不要设计现实中没人会这样吃、这样用、这样做的组合(例如把咖啡和腐乳放在一起吃)。除非参考笔记里本来就有这种搭配。
- 每条格式:主题:xxx｜角度:一句话说明这篇写什么、写给谁。
如果给了「优先的选题角度」,大约七成的角度要属于这些类型,其余自由补充,保证互不重复。各类型的写法:吃法搭配=讲怎么吃、搭配什么;产品种草=讲这类产品好在哪、适合谁、怎么选;菜谱做法=一道菜的做法步骤;知识科普=原理和常识;测评对比=选购对比(只能基于通用知识和参考笔记,不编亲身测评)。
角度必须仅凭通用知识和参考笔记就能写成(科普、配方、避坑、原理、对比解释、清单)。
不要设计需要作者亲身测评、盲测、探店、打卡、挑战才写得出来的角度(如「我测了三种…」「盲测记录」「连喝一周」),除非额外要求里明确说明作者有这些真实素材。
严格输出 JSON:{"angles": ["角度1:一句话说明这篇写什么、写给谁", "..."]}"""


def plan_angles(project: dict, playbook: dict, n: int, extra: str = "") -> list[str]:
    topics = "、".join(str(t) for t in (playbook.get("topics") or [])) or "(拆解卡没有列出,请从参考笔记实际涉及的主题里选)"
    user = (f"{_project_text(project)}{_account_text(project)}\n可写主题:{topics}\n拆解卡摘要:{playbook.get('summary','')}\n"
            f"额外要求:{extra or '无'}\n请给出 {n} 个角度。")
    angles = chat(ANGLES_SYSTEM, user).get("angles") or []
    return [str(a) for a in angles][:n] + [""] * max(0, n - len(angles))


NO_FABRICATION = """真实性与功效(最重要):
- 身份与口吻(小红书规则,违反会被判违规、限流):账号是商家 / 品牌的,不能装成普通买家。禁止用第一人称买家口吻写消费体验:不写「我买了 / 入手了 / 回购 / 囤了 / 收到 / 亲测 / 实测 / 盲测 / 自用 / 试吃试喝 / 用了 N 天 / 坚持了 N 周 / 我的反应 / 我的感受」这类像买家的话;不虚构就医、美容、住宿、家政等服务经历;不写「用前用后对比」的故事;不写「求推荐 / 求助 / 有没有人知道哪里买」这类假装消费者的提问;不说「闺蜜 / 同事推荐我」。
  可以用的口吻只有三种:① 商家 / 做产品的人:「我们家」「我们」「做了 X 年的…」「店里」,需要时自然亮明身份;② 中立的科普 / 教程 / 清单:无主语,或「可以这样吃」「建议」「一般这样搭配」;③ 直接对读者说:「你可以…」。吃法、做法、搭配、选购要点、成分常识照常写,但写成建议和方法,不写成「我吃了之后」。写得具体、自然、合理,不夸张、不写成「实验室式」的结论。
- 价格、购买渠道可以写,但渠道只能是本平台内(小红书店铺、商品链接、本平台购买),不能把人引导到其他平台(淘宝、京东、拼多多、抖音、微信等);检测或实验数据、他人评价、品牌方说法,作者没提供就不要写成事实,需要时用占位符:【补充:要补什么】。
- 可以写食材/产品常见的功效和带来的变化,这是爆款笔记吸引人的关键(如「有助于促进消化」「喝完胃里暖暖的」「抗炎饮食」「对我来说调理肠胃挺有帮助」)。保持在合理范围:多用「有助于/可能/对我来说/因人而异」这类措辞;不写治疗、治愈、根治、替代药物、保证见效、100% 有效,不把食品说成药,不夸大。
- 方法、步骤、通用知识可以正常写。"""

WRITE_SYSTEM = """你是资深小红书博主。严格按照给定「拆解卡」的量化规律、标题公式、开头钩子、正文结构、语气和排版习惯,
围绕指定角度写一篇【原创】笔记。可以借鉴参考笔记的风格,但不得照抄原句和具体数据。

篇幅与排版:正文字数、段落数、emoji 密度、分点比例按拆解卡「量化规律」给出的数值或区间来写,偏差不超过 30%。
写不下时优先精简、只保留最有用的点,不要靠加内容把篇幅撑大。

选题与常识:本篇只写「本篇角度」里点明的那一个主题,关键词里的其他东西不要硬塞进来;不要写现实中没人会这样搭配、这样吃、这样用的组合。

""" + NO_FABRICATION + """

合规:标题不超过 20 字;封面文字(cover 里的所有字段)同样适用;不使用下列高风险词(会限流或违反广告法):
{banned}

严格输出 JSON:
{{"title": "主标题,<=20字",
 "alt_titles": ["备选标题1","备选标题2"],
 "content": "正文,不含话题标签",
 "tags": ["话题1","话题2","...共5-8个,不带#"],
 "cover": {{"title": "封面主标题,<=14字,抓人,可以和标题不同", "sub": "副标题,<=18字,没有就空字符串", "points": ["清单、教程类给 3-4 个要点,每条<=12字;不是清单教程就给空数组"], "num": "标题里的核心数字,如3,没有就空字符串", "unit": "数字后的量词短语,如「种吃法」,<=6字,没有就空字符串", "tag": "角标,<=4字,如「亲测」「避坑」,没有就空字符串", "scene": "封面实拍背景的画面描述,<=60字:点名主体、场景、光线;场景要像普通人在家随手拍的(家里餐桌/厨房台面/书桌,自然光),不要棚拍、纯色背景、专业布光;画面里不能有文字"}},
 "image_prompts": ["内页配图提示词,条数以用户消息里【配图数量】为准(0 条就给空数组);每条是可直接交给生图模型的中文画面描述,必须点名画面主体(如「一杯姜黄咖啡」),并且画的是正文里某一步/某个要点真实会看到的东西(如正文讲「奶太烫→香料结团」就画一杯结团的奶茶);各条换不同的场景和拍摄角度;像手机随手拍的生活照(家里的自然光、随意构图、背景整洁自然、最多一两件日常小物,不要棚拍广告感;不要纸巾团、水渍、杂物堆;除热饮外不要写热气、冒烟、热腾腾、刚出锅),不要标题字、图标、对错符号、箭头、礼盒等和正文无关的道具;画面里不要出现任何文字、字母、标签,不要英文"]}}"""


def _card(playbook: dict) -> str:
    return json.dumps({k: v for k, v in playbook.items() if k != "evidence"}, ensure_ascii=False, indent=1)


def write_note(project: dict, playbook: dict, refs: list[dict], angle: str, extra: str = "",
               kb: str = "", banned: str = "") -> dict:
    user = (f"{_project_text(project)}{_account_text(project)}\n本篇角度:{angle or '自由发挥'}\n额外要求:{extra or '无'}{_kb_text(kb)}\n\n"
            f"拆解卡:\n{_card(playbook)}\n\n参考笔记(仅学风格):\n{_notes_text(refs[:3], 500) or '无'}")
    return chat(WRITE_SYSTEM.format(banned=banned or "(无)"), user)


INNER_SYSTEM = """你是小红书笔记的配图策划。根据成稿正文,为内页配图写生图提示词。
要求:
- 每条画的是正文里某一步、某个要点或某个结果真实会看到的东西,条与条对应正文不同位置,按正文顺序。
- 必须点名画面主体(食材、成品、器具、动作),写清场景和拍摄角度。
- 画面风格是普通人用手机随手拍的生活照:家里的自然光、构图随意、背景整洁自然,最多一两件日常小物,偶尔带一点随手拍的痕迹(如一个用过的杯子);不要棚拍、纯色背景、专业布光、精致摆盘这类广告大片感,也不要纸巾团、水渍、杂物堆这类凌乱的东西。
- 除热饮外,不要写热气、冒烟、热腾腾、刚出锅、蒸汽这类会让画面冒烟的词;冷的、常温的食物不要有热气。
- 几张图的搭配、场景、拍摄角度各不相同。产品资料里有拍摄规格的,按里面的搭配和频率来:标「少用」的整篇最多一张,常见搭配也不要每张都一样(例如不要每张都是白粥配馒头)。
- 不要画标题字、图标、对错符号、箭头、序号、标注卡片,不要礼盒、节日装饰等和正文无关的道具。
- 场景要符合产品真实的使用方式:冲泡饮品(咖啡、茶、奶类)放在餐桌、咖啡角、办公桌、茶几、窗边,不要放在灶台、炒锅、油烟重的厨房;零食点心放在桌面、沙发;只有烹饪调味类才出现厨房灶台。
- 画面里不要出现任何文字、字母、标签。
严格输出 JSON:{{"image_prompts": ["提示词1","提示词2",...共 {k} 条]}}"""


def inner_image_prompts(title: str, content: str, k: int, product: str = "", block: str = "") -> list[str]:
    """按成稿正文重新策划内页配图提示词(不含封面),返回 k 条。block = 产品资料(含拍摄规格:搭配和频率、不要出现的东西)。"""
    user = f"标题:{title}\n产品:{product or '未指定'}\n\n正文:\n{content[:2500]}{block}"
    out = chat(INNER_SYSTEM.format(k=k), user, temperature=0.7)
    return [str(x).strip() for x in (out.get("image_prompts") or []) if str(x).strip()][:k]


REVISE_SYSTEM = """你是小红书笔记的合规编辑。根据「扫描结果」修改笔记,只改命中的问题,其余内容、结构、语气尽量保持不变。
- 极限词、导流词、承诺类词:换成相对、个人化的表达或删掉,参考给出的建议。
- AI 腔词:换成口语或删掉。
- 标题超长:压到 20 字以内,保留核心关键词。
- 买家口吻(「我买了 / 亲测 / 用了 N 天 / 我的感受 / 求推荐」这类以买家身份写的消费体验):整句改成商家口吻(「我们家…」「我们…」)或无主语的做法 / 建议(「可以这样吃」),删掉买家的购买和使用经历,不要改成别的虚构经历。
- 已有的【补充:…】占位符原样保留,不要替作者填写。

""" + NO_FABRICATION + """

严格输出 JSON:{"title": "...", "alt_titles": ["..."], "content": "...", "tags": ["..."]}"""
FIELD_NAMES = {"title": "标题", "content": "正文", "tags": "话题", "image_text": "配图文字"}


def revise(draft: dict, hits: list[dict]) -> dict:
    found = "\n".join(f"- [{h['category']}] 「{h['word']}」在{FIELD_NAMES.get(h['field'], h['field'])},"
                      f"建议:{h['replacement'] or '换成更稳妥的说法'}" for h in hits)
    note = json.dumps({k: draft[k] for k in ("title", "alt_titles", "content", "tags")}, ensure_ascii=False, indent=1)
    return chat(REVISE_SYSTEM, f"扫描结果:\n{found}\n\n笔记:\n{note}", temperature=0.3)


# ---------- 审稿 ----------
REVIEW_DIMS = ["标题", "开头钩子", "结构", "干货密度", "语气人设", "互动引导", "合理性"]
REVIEW_SYSTEM = """你是小红书内容主编,负责发布前审稿。对照「拆解卡」「量化对比」「合规扫描」「赛道知识库」审一篇稿子。

评分(每个维度 0-100):""" + "、".join(REVIEW_DIMS) + """。
- 90 以上:可以直接发,具备拆解卡里的爆款特征;75-89:合格,有明确可改进点;60-74:能看但平淡;60 以下:有明显问题。
- 打分要拉开差距,不要全给 80 左右。

问题清单(3-6 条,按严重程度排序):
- problem 要具体:引用「量化对比」里的数字或稿子原文,如「正文 120 字,参考中位数 260 字」「开头第一句『今天分享…』没有钩子」。
- fix 必须能直接照做,写成「原文『…』→ 改为『…』」;结构类问题写清楚加在哪、删哪段。
- why 说明依据(拆解卡哪条、知识库哪条、量化对比哪项)。不要预测点击率、涨粉数等效果数字。
- 必查:有没有编造检测数据、他人评价、品牌方说法这类硬信息,有就作为 high 问题,fix 写成改成【补充:…】占位符;作者的使用感受、吃法、价格和本平台内的购买渠道可以写,不算问题;但引导到其他平台购买是 high 问题。
- 必查:买家口吻。商家账号用第一人称买家口吻写消费体验(「我买了 / 亲测 / 回购 / 用了 N 天 / 我的反应 / 求推荐」等)会被平台判违规,有就作为 high 问题,fix 改成商家口吻(「我们家…」)或无主语的做法 / 建议。
- 必查:合理性。有没有把不相关的食材、产品、场景强行组合在一起,现实中没人会这样吃、这样用、这样做(如咖啡配腐乳)。有就作为 high 问题,「合理性」维度打低分,fix 写成删掉其中一个主题、让整篇只讲一个。

修改版 revised:按问题清单把整篇改好,给出完整的标题、正文、话题。只改有问题的地方,保留原稿的角度和好的部分。
""" + NO_FABRICATION + """
- 原稿里的【补充:…】占位符必须原样保留,不能删掉或替作者填写。
- 不使用以下高风险词:
{banned}
{comments_rule}
严格输出 JSON:
{{"scores": {{"标题": 0, "开头钩子": 0, "结构": 0, "干货密度": 0, "语气人设": 0, "互动引导": 0, "合理性": 0}},
 "summary": "一句话总评",
 "issues": [{{"dimension": "维度名", "severity": "high/medium/low", "problem": "...", "fix": "原文『…』→ 改为『…』", "why": "..."}}],
 "revised": {{"title": "...", "content": "...", "tags": ["..."]}}{comments_field}}}"""
COMMENTS_RULE = """
模拟评论区:写 6 条这篇发出后可能出现的评论,用来提前发现读者会问什么、会质疑什么。
- 类型混合:认同、提问(要具体,如问肤质/价格/步骤细节)、质疑(如觉得是广告、方法不适用)、经验补充。至少 1 条质疑、2 条提问。
- 口语化、像真实小红书评论,长短不一;昵称用虚构的。这只是模拟,不代表真实用户。"""
COMMENTS_FIELD = """,
 "comments": [{{"nickname": "...", "text": "...", "type": "认同/提问/质疑/补充"}}]"""


def review(project: dict, playbook: dict, draft: dict, quant_text: str, checks_text: str,
           kb: str = "", banned: str = "", with_comments: bool = False) -> dict:
    system = REVIEW_SYSTEM.format(banned=banned or "(无)", comments_rule=COMMENTS_RULE if with_comments else "",
                                  comments_field=COMMENTS_FIELD if with_comments else "")
    note = json.dumps({k: draft.get(k) for k in ("title", "content", "tags")}, ensure_ascii=False, indent=1)
    user = (f"{_project_text(project)}\n本篇角度:{draft.get('angle') or '未指定'}{_kb_text(kb)}\n\n"
            f"拆解卡:\n{_card(playbook)}\n\n量化对比(代码计算,可信):\n{quant_text}\n\n合规扫描:\n{checks_text}\n\n稿子:\n{note}")
    return chat(system, user, temperature=0.4)


# ---------- 帖子自动打标签 ----------
CONTENT_TYPES = ["教程攻略", "测评对比", "好物种草", "避坑踩雷", "合集清单", "经验分享", "日常记录", "观点讨论", "其他"]
TAG_SYSTEM = f"""你是小红书内容分类员。逐篇判断给定笔记,输出 JSON:
{{"items": [{{"id": 笔记编号, "type": "从 {'/'.join(CONTENT_TYPES)} 中选一个",
  "topic": "主题:这篇笔记主要讲的食材/产品/场景,一个短语,<=8字,如「姜黄粉」「生姜粉」「速溶咖啡」「腐乳」",
  "audience": "目标人群,<=12字,如:油皮学生党",
  "highlight": "这篇的爆点/吸引人的地方,<=30字",
  "tags": ["3-5 个内容关键词"]}}]}}
每篇都要输出,id 与输入一致。

主题的写法:
- 按笔记的实际内容判断,不要照抄搜索词;笔记讲的是什么就写什么,一篇只写最主要的那一个。
- 近义写法统一成一个(如姜黄粉/黄姜粉统一写「姜黄粉」)。注意:生姜和姜黄是两种不同的东西,不要混为一谈。
- 如果输入里给了「已有主题」,是同一件东西就沿用那里的写法;不是同一件东西就写新的。
- 粒度要粗,方便按主题筛选:不要带地名、品牌名、店名、人名(不写「珠海咖啡店」「永璞咖啡」,写「咖啡店探店」「咖啡品牌」;不写「厦门咖啡厅」「陆家嘴咖啡节」,写「咖啡店探店」「咖啡活动」)。
- 如果输入里给了「搜索词」,是这批笔记采集时用的词:笔记讲的正好是其中某个,主题就写那个词(或它的通用说法);笔记其实和搜索词无关(比如搜「腐乳」却采到了旅游美食、服饰),就写它真实的主题(如「旅游美食」「服饰」),不要硬套搜索词。"""


def tag_notes(notes: list[dict], known_topics: list[str] | None = None, search_terms: list[str] | None = None) -> dict[int, dict]:
    user = "\n\n".join(f"【{n['id']}】标题:{n['title']}\n正文:{(n.get('content') or '')[:400]}" for n in notes)
    if known_topics:
        user = f"已有主题:{'、'.join(known_topics)}\n\n" + user
    if search_terms:
        user = f"搜索词:{'、'.join(search_terms)}\n\n" + user
    items = chat(TAG_SYSTEM, user, temperature=0.3).get("items") or []
    out = {}
    for it in items:
        try:
            t = it.get("type") if it.get("type") in CONTENT_TYPES else "其他"
            out[int(it["id"])] = {"type": t, "topic": str(it.get("topic") or "").strip()[:12] or "未识别",
                                  "audience": str(it.get("audience") or "")[:30],
                                  "highlight": str(it.get("highlight") or "")[:80],
                                  "tags": [str(x) for x in (it.get("tags") or [])][:6]}
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ---------- 选题角度:固定的一套分类,方便跨笔记、跨账号比较哪类选题表现好 ----------
ANGLES = ["菜谱做法", "吃法搭配", "产品种草", "测评对比", "促销活动", "日常心情", "知识科普", "探店打卡", "其他"]
ANGLE_SYSTEM = f"""你是小红书食品电商的内容分析员。逐篇判断笔记的「选题角度」(只看标题),必须从下面选一个,不能自创:
- 菜谱做法:教怎么做一道菜、一个步骤流程
- 吃法搭配:讲产品/食材怎么吃、搭配什么吃(拌饭、拌面、佐餐、蘸料、早餐搭配)
- 产品种草:直接夸某个产品好吃好用、推荐、开箱、回购、安利
- 测评对比:多款对比、盲测、怎么选、踩雷避坑
- 促销活动:优惠、福利、试吃招募、秒杀、新品上市、粉丝福利
- 日常心情:生活感悟、情绪、日记、吃瓜,基本和产品无关
- 知识科普:营养功效、工艺原理、常识
- 探店打卡:去某家店、某个地方吃
- 其他:以上都不是
严格输出 JSON:{{"items": [{{"id": 笔记编号, "angle": "{'/'.join(ANGLES)} 之一"}}]}},每篇都要输出,id 与输入一致。"""


def angle_notes(notes: list[dict]) -> dict[int, str]:
    """notes: [{id, title}] → {id: 选题角度};模型给了列表以外的写法一律当「其他」。"""
    user = "\n".join(f"【{n['id']}】{n['title']}" for n in notes)
    out = {}
    for it in chat(ANGLE_SYSTEM, user, temperature=0.1).get("items") or []:
        try:
            a = str(it.get("angle") or "").strip()
            out[int(it["id"])] = a if a in ANGLES else "其他"
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


# ---------- 主题整理:建议合并 ----------
MERGE_SYSTEM = """你是内容分类整理员。下面是一批笔记的「主题」列表(带篇数和示例标题)。请找出意思相同或属于同一类、应该合并的主题,给出合并方案。
规则:
- 只合并确实是同一件东西或同一类内容的。例如「黄金奶」和「姜黄奶」是同一种饮品;「Manner兼职」「咖啡店兼职」都是咖啡兼职;「东山岛小吃」「潮汕菜」都是旅游美食。
- 不同的东西不要合并:生姜粉和姜黄粉是两种东西;咖啡冲泡教程和咖啡店探店也是两类内容。
- 合并后的名称要通用,不带地名、品牌名、店名,不超过 12 个字;能沿用列表里已有的、最常用的名称就沿用。
- 只输出需要合并的组,不需要合并的主题不要出现;一个主题只能出现在一个组里。
严格输出 JSON:{"groups": [{"to": "合并后的名称", "from": ["主题A", "主题B"]}]}"""


def suggest_topic_merges(items: list[dict]) -> list[dict]:
    """items: [{k: 主题, n: 篇数, samples: [示例标题]}]。返回经过校验的合并方案:[{to, from: [要并入的主题...]}]。"""
    user = "\n".join(f"- {i['k']}({i['n']}篇) 例:{' / '.join(i.get('samples') or [])}" for i in items)
    names, used, out = {i["k"] for i in items}, set(), []
    for g in chat(MERGE_SYSTEM, user, temperature=0.2).get("groups") or []:
        to = str(g.get("to") or "").strip()[:12]
        frm = [x for x in dict.fromkeys(str(x).strip() for x in (g.get("from") or [])) if x in names and x not in used]
        sources = set(frm) | ({to} if to in names and to not in used else set())
        if to and len(sources) >= 2:  # 至少两个已有的主题才算「合并」
            used |= sources
            out.append({"to": to, "from": sorted(sources - {to})})
    return out


# ---------- 关键词推荐:把宽泛的词换成带意图的具体词 ----------
SUGGEST_SYSTEM = """你是小红书选题搜索词专家。用户填了一个搜索词,请判断它是不是太宽泛(含义多、容易搜到不相关的内容,比如只有一个食材/品类/产品名),并给出更具体、带明确意图的搜索词。
- 带意图:做法、教程、怎么吃、怎么选、避坑、测评、清单、对比、新手入门……
- 必须是真实用户会在小红书搜的短语,2-8 个字,不要带地名、品牌名。
- 不要只是「原词+做法/测评」这种机械拼接:要把宽泛的词落到具体的品类/场景/方式上,再带上意图。例如「咖啡」→ 手冲咖啡教程、在家做咖啡、速溶咖啡冲泡、咖啡豆怎么选;「腐乳」→ 腐乳做法、腐乳怎么吃、自制腐乳、腐乳拌面;「面膜」→ 敏感肌面膜推荐、自制面膜怎么做。
- 覆盖不同的意图和场景,给 4-6 个;每个用不超过 12 个字说明会搜到什么内容。
- 如果用户的词已经足够具体(有明确意图),broad 写 false,suggestions 给空数组。
严格输出 JSON:{"broad": true, "suggestions": [{"word": "腐乳做法", "hint": "家常菜里怎么用腐乳"}]}"""
KW_PATTERNS = {"美食": ["{w}做法", "{w}怎么吃", "自制{w}", "{w}推荐"], "美妆护肤": ["{w}教程", "{w}测评", "{w}避坑", "{w}推荐"],
               "穿搭": ["{w}搭配", "{w}教程", "{w}推荐", "{w}避坑"]}


def suggest_keywords(track: str, word: str) -> dict:
    out = chat(SUGGEST_SYSTEM, f"赛道:{track or '未指定'}\n搜索词:{word}", temperature=0.4)
    items = []
    for it in out.get("suggestions") or []:
        w = str((it or {}).get("word") or "").strip()
        if 2 <= len(w) <= 12 and w != word and w not in [i["word"] for i in items]:
            items.append({"word": w, "hint": str((it or {}).get("hint") or "")[:24]})
    return {"broad": bool(out.get("broad")) and bool(items), "suggestions": items[:6]}


def fallback_keywords(track: str, word: str) -> dict:
    """AI 暂时不可用时的通用说法。"""
    pats = KW_PATTERNS.get(track, ["{w}教程", "{w}攻略", "{w}推荐", "{w}避坑"])
    return {"broad": True, "suggestions": [{"word": p.format(w=word), "hint": "通用说法"} for p in pats]}


# ---------- 正文压缩 ----------
COMPRESS_SYSTEM = """你是小红书编辑。把下面这篇笔记的正文压缩到约 {target} 字(上下浮动 20% 以内)。
- 保留:开头钩子、核心结论、最有用的 2-3 个干货点、结尾的互动提问。
- 删掉:铺垫、重复的话、次要例子、过渡句。
- 原文里所有【补充:…】占位符必须原样保留,不能删,也不能替作者填。
- 不增加原文没有的新事实、数据、功效、价格、渠道;语气和人称保持原样。
- 段落保持短,一般 4-5 段;原有的编号/分点可以保留,但每条精简成一句。
严格输出 JSON:{{"content": "压缩后的正文"}}"""


def compress(draft: dict, target: int) -> str:
    out = chat(COMPRESS_SYSTEM.format(target=target), f"标题:{draft['title']}\n当前正文({len(draft['content'])}字):\n{draft['content']}", temperature=0.3)
    return str(out.get("content") or "").strip()


# ---------- 看产品图 ----------
def parse_chat_text(raw: str) -> str:
    """兼容普通 JSON 和流式(data: {...} 逐行,中间夹 ": PING" 保活行)两种返回,取出文本。"""
    t = (raw or "").strip()
    if not t:
        return ""
    if "data:" in t:
        out = ""
        for line in t.splitlines():
            line = line.strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            try:
                j = json.loads(line[5:])
            except ValueError:
                continue
            for c in j.get("choices") or []:
                out += (c.get("delta") or c.get("message") or {}).get("content") or ""
        return out
    try:
        j = json.loads(t)
        return j["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


VISION_WAITS = (0, 3, 6, 10)  # 到服务商的连接偶尔会被掐断,多试几次


def vision_chat(system: str, text: str, images: list, model: str | None = None, s: dict | None = None) -> str:
    """带图片的对话(OpenAI 兼容的 /chat/completions)。地址和 Key 优先用「看图接口」的配置,没填就沿用生图接口。images 是本地图片路径。"""
    import base64
    from . import mirror
    s = s or get_settings()
    base = (s.get("vision_base_url") or s.get("image_base_url") or "").rstrip("/")
    key = s.get("vision_api_key") or s.get("image_api_key") or ""
    if not (base and key):
        raise RuntimeError("请先在管理端「接口配置 → 产品图」里填写看图接口的地址和 Key(留空则用生图接口的)")
    parts = [{"type": "text", "text": text}]
    for f in images:
        b = open(f, "rb").read()
        ext = mirror.sniff(b) or "jpg"
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/{'jpeg' if ext == 'jpg' else ext};base64,{base64.b64encode(b).decode()}"}})
    payload = {"model": model or s.get("vision_model") or "qwen-vl-max", "max_tokens": 3000,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": parts}]}
    last = RuntimeError("看图失败")
    for wait in VISION_WAITS:
        if wait:
            time.sleep(wait)
        try:
            r = httpx.post(base + "/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=180)
        except httpx.HTTPError as e:
            last = RuntimeError(f"连不上看图接口:{e.__class__.__name__}")
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = RuntimeError(f"看图接口返回 {r.status_code}")
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"看图接口返回 {r.status_code}:{r.text[:160]}")
        out = parse_chat_text(r.text)
        if out.strip():
            return out
        last = RuntimeError("看图接口返回了空内容")
    raise last


DESCRIBE_SYSTEM = """你是小红书选品和内容策划。看用户上传的产品图,识别产品并输出 JSON。
- category:产品品类,2-8 个字,如「腐乳」「手冲咖啡壶」「防晒霜」。
- track:从给定的赛道名里选最合适的一个;都不合适就写空字符串。
- appearance:产品和包装的外观,一句话(颜色、形状、材质、包装样式),不超过 80 字。
- package_text:包装上能看清的文字(品牌名、产品名、规格),看不清就写空字符串。
- selling_points:图上能看出来的特点,最多 4 个,每个不超过 15 字;不要编造图上看不出的功效、成分。
- scenes:这个产品适合在哪些生活场景里自然出现,最多 4 个,每个不超过 12 字。
- keywords:品类核心词的搜索词,2-3 个,每个 2-8 个字,必须包含产品的品类词(如品类是腐乳,写「腐乳做法」「腐乳怎么选」),再加明确意图(做法、怎么选、测评等);不同词的意图要不一样;不要带品牌名。
- broader:上位或相邻的品类词,1-2 个,2-8 个字(如品类是金汤酱料,写「复合调味料」「火锅底料」)。搜这些词能采到更多可参考的笔记。
- uses:用这个产品能做的菜或常见用法/场景,2-4 个,2-6 个字(如「酸菜鱼」「肥牛」;非食品写使用场景,如防晒霜写「军训」「海边」)。
严格输出 JSON:{"category": "", "track": "", "appearance": "", "package_text": "", "selling_points": [""], "scenes": [""], "keywords": [""], "broader": [""], "uses": [""]}"""


def _json_in(text: str) -> dict:
    """从模型回复里取出 JSON:去掉 ```json 围栏,容忍多余的逗号;实在解析不了(比如输出被截断、引号没转义),
    就逐个字段把 category / track / keywords 这些关键内容抠出来,只要有品类或关键词就算识别成功。"""
    import re
    i, j = text.find("{"), text.rfind("}")
    if i < 0:
        raise RuntimeError("看图结果不是 JSON:" + text[:80])
    body = text[i:j + 1] if j > i else text[i:]
    for cand in (body, re.sub(r",\s*([}\]])", r"\1", body)):
        try:
            return json.loads(cand)
        except ValueError:
            pass
    out = {}
    for k in ("category", "track", "appearance", "package_text"):
        m = re.search(rf'"{k}"\s*:\s*"([^"]*)"', body)
        if m:
            out[k] = m.group(1)
    for k in ("selling_points", "scenes", "keywords"):
        m = re.search(rf'"{k}"\s*:\s*\[(.*?)(?:\]|$)', body, re.S)
        if m:
            out[k] = re.findall(r'"([^"]+)"', m.group(1))
    if not (out.get("category") or out.get("keywords")):
        raise RuntimeError("看图结果不是合法 JSON:" + text[:80])
    return out


def describe_product(name: str, images: list, track_names: list[str]) -> dict:
    ask = lambda: vision_chat(DESCRIBE_SYSTEM, f"产品名称:{name}\n可选赛道:{'、'.join(track_names) or '无'}\n请识别这个产品。", images)
    try:
        out = _json_in(ask())
    except RuntimeError as e:
        if "看图结果" not in str(e):
            raise
        out = _json_in(ask())  # 模型这次输出的格式不对:再问一次(偶发)
    lst = lambda k, n, m: [str(x).strip()[:m] for x in (out.get(k) or []) if str(x).strip()][:n]
    track = str(out.get("track") or "").strip()
    return {"category": str(out.get("category") or "").strip()[:12], "track": track if track in track_names else "",
            "appearance": str(out.get("appearance") or "").strip()[:120], "package_text": str(out.get("package_text") or "").strip()[:60],
            "selling_points": lst("selling_points", 4, 20), "scenes": lst("scenes", 4, 16),
            "keywords": [k for k in lst("keywords", 6, 10) if len(k) >= 2],
            "broader": [k for k in lst("broader", 2, 8) if len(k) >= 2], "uses": [k for k in lst("uses", 4, 8) if len(k) >= 2]}


# ---------- 产品拍摄规格:按产品自动生成一次,所有出图环节(封面、内页配图、Live 场景图)都读它 ----------
SHOOT_SYSTEM = """你是小红书食品 / 生活用品的图片策划。根据产品资料,给这个产品定一份「拍摄规格」,让以后给它配图时画面符合真实使用方式,不重样,不出怪画面。输出 JSON:
- kind:产品类型,只能是以下之一:
  drink(冲泡饮品:咖啡、茶、奶茶粉、麦片、可可粉、固体饮料)/ snack(零食点心:饼干、坚果、肉干、肉脯、糖果)/ cook(烹饪调味:酱油、香料、食用油、复合调味料)/
  mixed(既能冲饮又能入菜)/ side(下饭佐餐的小菜:腐乳、酱菜、榨菜、辣酱)/ food(其他一般食品)。以产品真实的主要吃法为准,别被包装文案里的个别词带偏
  (比如「姜黄粉固体饮料」是冲饮,不是厨房香料;「下午茶零食」是零食,不是饮品)。
- serve:吃的时候是 hot(热的,会冒热气)/ cold(冷的)/ room(常温)。只有 hot 的画面才允许有热气。
- pairings:这个产品真实常见的搭配或吃法(食品类 5-9 个,非食品给空数组),每个 {"name": "搭配名,2-8 个字", "freq": "high / mid / low"}。
  freq 决定出现频率,按这个分配:high 最多 2 个(最典型、最不容易出错的吃法);mid 2-3 个;其余都写 low。
  特别注意:「谁都会第一个想到、已经被写烂的刻板搭配」必须写 low,哪怕它是真实吃法
  (例如腐乳夹馒头、咖啡配面包、牛奶配饼干:真实,但不能每张图都是它)。要覆盖不同的吃法(主食、配菜、做法、直接吃、冲饮…),别全是同一类。
- avoid:这个产品的画面里绝对不要出现的东西,3-6 条,每条 4-14 个字。要想到「容易被模型错误联想」的东西
  (例如冲饮粉不要出现姜、蒜、洋葱等生食材;腌制小菜不要出现烟雾蒸汽;别的品牌或别的产品)。
- shoot_note:一句话拍摄提示,不超过 40 字(例如「瓶子放在饭桌上,配饭配粥,不要冒热气」)。
- scenes:这个产品最适合出现的 6-8 个具体场景,每个 ≤ 40 字,写清地点和 2-3 件合理的小道具(要符合产品真实的吃法 / 用法);
  不同场景要各不相同(家里、办公、出行、户外…);不要写纸巾、水渍、杂物堆、热气、冒烟、刚出锅这类词。
- size:这个产品的实际大小,≤ 60 字。写清每一件包装的大致尺寸和它们的相对大小(谁大谁小),以及拿在手里是什么感觉。
  根据品类和包装上的规格(如「50g 罐」「248g 袋」)按常见大小估计,例如「玻璃罐高约 9cm、直径约 6cm,单手可握;外盒约 8×8×9cm,比罐子大一圈」。
只输出 JSON:{"kind": "", "serve": "", "pairings": [{"name": "", "freq": ""}], "avoid": [""], "shoot_note": "", "scenes": [""], "size": ""}"""

SHOOT_KINDS = ("drink", "snack", "cook", "mixed", "side", "food")


def shoot_spec(name: str, category: str = "", selling_points: str = "", audience: str = "", scenes: list | None = None,
               uses: list | None = None, appearance: str = "") -> dict:
    """给一个产品生成拍摄规格(整理成固定格式,模型给的内容不合规就丢掉那一项,不会带进画面提示词)。"""
    user = (f"产品名称:{name}\n品类:{category or '未知'}\n外观:{appearance or '未知'}\n卖点:{(selling_points or '无')[:300]}\n"
            f"适合人群:{(audience or '无')[:80]}\n适合的场景:{'、'.join(scenes or []) or '无'}\n常见用法:{'、'.join(uses or []) or '无'}")
    out = chat(SHOOT_SYSTEM, user, temperature=0.3)
    kind = str(out.get("kind") or "").strip()
    serve = str(out.get("serve") or "").strip()
    pairings = []
    for x in out.get("pairings") or []:
        n = str((x or {}).get("name") if isinstance(x, dict) else x or "").strip()[:10]
        f = str((x or {}).get("freq") if isinstance(x, dict) else "mid").strip().lower()
        if n and f in ("high", "mid", "low"):
            pairings.append({"name": n, "freq": f})
        elif n:
            pairings.append({"name": n, "freq": "mid"})
    bad = ["纸巾", "餐巾", "抽纸", "水渍", "杂乱", "杂物堆", "热气", "冒烟", "蒸汽", "刚出锅", "热腾腾", "没收拾"]   # 模型自己写的专属场景里带这些词的丢掉
    if kind != "cook":
        bad += ["灶台", "炒锅", "油烟"]   # 只有烹饪调味类的产品才会出现灶台
    # 标成「少用」的搭配(如腐乳夹馒头)不能又写进专属场景里,不然频率控制就白做了:专属场景里出现它的关键词就丢掉
    low_cores = {re.sub(r"^(搭配|夹|配|拌|抹|蘸|炒|加|蒸|煮|泡)+", "", x["name"]) for x in pairings if x["freq"] == "low"}
    bad += [c for c in low_cores if len(c) >= 2]
    scenes = []
    for x in out.get("scenes") or []:
        t = str(x or "").strip()[:60]
        if len(t) >= 6 and not any(w in t for w in bad) and t not in scenes:
            scenes.append(t)
    return {"kind": kind if kind in SHOOT_KINDS else "", "serve": serve if serve in ("hot", "cold", "room") else "",
            "pairings": pairings[:10], "avoid": [str(a).strip()[:16] for a in (out.get("avoid") or []) if str(a).strip()][:6],
            "shoot_note": str(out.get("shoot_note") or "").strip()[:60], "scenes": scenes[:8], "size": str(out.get("size") or "").strip()[:80]}


# ---------- 补全赛道:笔记库沉淀的笔记没有项目,没人告诉它属于哪个赛道 ----------
TRACK_SYSTEM = """你是内容分类员。给每篇笔记选一个最合适的赛道。
- 赛道只能从「可选赛道」里选一个,写原样的名称;
- 都不合适就写「未分类」,不要自己编新赛道;
- 看笔记实际讲的是什么,不要只看搜索词(比如搜「咖啡」却讲咖啡店兼职的,看内容归类)。
严格输出 JSON:{"items": [{"id": 笔记编号, "track": "赛道名或未分类"}]}"""


def classify_tracks(notes: list[dict], names: list[str]) -> dict[int, str]:
    """返回 {笔记 id: 赛道名};模型给了不在列表里的名字,一律当「未分类」。"""
    user = f"可选赛道:{'、'.join(names)}\n\n" + "\n\n".join(
        f"【{n['id']}】标题:{n['title']}\n主题:{n.get('topic') or ''}\n正文:{(n.get('content') or '')[:200]}" for n in notes)
    out = {}
    for it in chat(TRACK_SYSTEM, user, temperature=0.1).get("items") or []:
        try:
            nid, t = int(it.get("id")), str(it.get("track") or "").strip()
        except (TypeError, ValueError, AttributeError):
            continue
        out[nid] = t if t in names else "未分类"
    return out


# ---------- 关键词扩展:给一个产品/品类,生成一批小红书搜索词 ----------
EXPAND_SYSTEM = """你是小红书选题搜索词专家。用户给一个产品或品类,请生成一批用户真的会在小红书搜的词,用来采集同品类的笔记。
- 每个词必须包含这个产品/品类的核心词(如品类是腐乳,就写「腐乳做法」「腐乳怎么选」),不带品牌名、地名;
- 2-10 个字,带明确意图;覆盖尽量不同的角度:做法 / 吃法(用法)/ 怎么选 / 测评对比 / 避坑 / 搭配 / 清单推荐 / 新手入门 / 不同人群(上班族、学生党、减脂、宝妈…)/ 不同场景(早餐、宵夜、聚餐、送礼…)/ 不同季节价位;
- 不要互相只差一两个字的重复词;
- 已有的词不要重复。
严格输出 JSON:{"keywords": ["腐乳做法", "..."]}"""


def expand_keywords(topic: str, track: str = "", n: int = 30, existing: list[str] | None = None) -> list[str]:
    n = max(5, min(n, 60))
    ex = "、".join((existing or [])[:80])
    out = chat(EXPAND_SYSTEM, f"产品/品类:{topic}\n赛道:{track or '未指定'}\n需要 {n} 个关键词。" + (f"\n已有的词:{ex}" if ex else ""), temperature=0.7).get("keywords") or []
    have, res = set(existing or []), []
    core = topic.strip()
    for k in out:
        k = str(k).strip()
        if not (2 <= len(k) <= 12) or k in have or k in res:
            continue
        # 必须带核心词(至少共享两个连续的字);短词(核心词不足两个字)不检查
        if len(core) >= 2 and not any(core[i:i + 2] in k for i in range(len(core) - 1)):
            continue
        res.append(k)
    return res[:n]


# ---------- 看图失败时:只凭产品名判断品类和搜索词 ----------
NAME_SYSTEM = """你是小红书选品专家。用户给一个产品名称(可能带品牌名),请判断它是什么品类,并给出在小红书上找同类笔记要搜的词。
- category:产品品类,2-8 个字(如「金汤酱料」「红腐乳」「防晒霜」),不要带品牌名;
- track:从给定的赛道名里选最合适的一个,都不合适就写空字符串;
- keywords:2-3 个搜索词,每个 2-10 个字,必须包含品类词,意图各不相同(做法、怎么选、测评、避坑等),不要带品牌名;
- broader:上位或相邻的品类词 1-2 个(如金汤酱料 → 「复合调味料」「火锅底料」);
- uses:用这个产品能做的菜或常见用法/场景 2-4 个(如「酸菜鱼」「肥牛」)。
严格输出 JSON:{"category": "", "track": "", "keywords": [""], "broader": [""], "uses": [""]}"""


def describe_by_name(name: str, track_names: list[str]) -> dict:
    out = chat(NAME_SYSTEM, f"产品名称:{name}\n可选赛道:{'、'.join(track_names) or '无'}", temperature=0.2)
    track = str(out.get("track") or "").strip()
    kws = [str(k).strip() for k in (out.get("keywords") or []) if 2 <= len(str(k).strip()) <= 12][:6]
    cat = str(out.get("category") or "").strip()[:12]
    if not (cat and kws):
        raise RuntimeError("没能从产品名判断出品类")
    lst = lambda k, n: [str(x).strip() for x in (out.get(k) or []) if 2 <= len(str(x).strip()) <= 8][:n]
    return {"category": cat, "track": track if track in track_names else "", "keywords": kws, "broader": lst("broader", 2), "uses": lst("uses", 4), "by_name": True}


# ---------- 相邻关键词:品类本身的内容很少时,往上位品类、常用菜、同类替代品扩 ----------
ADJ_SYSTEM = """你是小红书选题搜索词专家。用户给一个产品或品类,而它自己的笔记很少,请给出「相邻」的搜索词,用来采集可以借鉴写法的笔记。
- 包括三类:① 上位品类(如金汤酱料 → 复合调味料、火锅底料、懒人调料);② 用这个产品能做的菜或常见用法(酸菜鱼、肥牛、一人食);③ 同类替代品或相近的东西(酸汤底料、番茄锅底);
- 每个词 2-10 个字,带明确意图(做法、怎么选、推荐、测评、教程…),是用户真的会搜的词;不带品牌名、地名;
- 词必须和这个产品的用途直接相关,不要扯远;不要互相只差一两个字的重复词;已有的词不要重复。
严格输出 JSON:{"keywords": ["复合调味料推荐", "..."]}"""


def expand_adjacent(topic: str, track: str = "", n: int = 10, existing: list[str] | None = None) -> list[str]:
    n = max(3, min(n, 40))
    ex = "、".join((existing or [])[:60])
    out = chat(ADJ_SYSTEM, f"产品/品类:{topic}\n赛道:{track or '未指定'}\n需要 {n} 个相邻关键词。" + (f"\n已有的词:{ex}" if ex else ""), temperature=0.7).get("keywords") or []
    have, res = set(existing or []), []
    for k in out:
        k = str(k).strip()
        if 2 <= len(k) <= 12 and k not in have and k not in res:
            res.append(k)
    return res[:n]



# ---------- 按账号推荐搜索词 ----------
ACCKW_SYSTEM = """你是小红书选题搜索词专家。下面是一个账号的内容画像(表现较好的笔记标题、主要主题、近期的热搜词)。请给出 6 个适合用来采集「同类参考笔记」的搜索词:
- 贴合这个账号已有的内容方向和产品,是用户真的会在小红书搜的短语,2-10 个字,带明确意图(做法、吃法、搭配、怎么选、避坑、清单……);
- 热搜词里和这个账号的产品相关的优先采用(原样使用),不相关的忽略,不要硬凑;
- 不带品牌名、地名;互相不要只差一两个字,尽量覆盖不同的意图。
严格输出 JSON:{"keywords": ["...", "..."]}"""


def account_keywords(profile: str, n: int = 6) -> list[str]:
    ks = chat(ACCKW_SYSTEM, profile, temperature=0.6).get("keywords") or []
    return list(dict.fromkeys(str(k).strip() for k in ks if str(k).strip()))[:n]
