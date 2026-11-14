"""闭环学习(第一阶段):发布时的打分快照、评分 / 做法 vs 真实表现的对比、标了发布没对上时的提示。
稿子、笔记都是直接写库造的;对比用手工构造的 rows,不依赖发布天数和千帆数据的先后。"""
import json


def run(rep, env):
    from app import api_user, db, loop, qianfan

    q = env.q
    print("\n[闭环学习]")
    pid = db.q_exec("INSERT INTO projects(track,keywords,direction) VALUES('美食','[]','')").lastrowid
    made = []

    def mk(title, published=0, review=None, layout="", acc="u1", content="第一段。\n第二段!"):
        cc = {"layout": layout} if layout else {}
        did = db.q_exec("INSERT INTO drafts(project_id,title,content,images,status,published,account_id,account_name,angle,ptype,review,cover_card) VALUES(?,?,?,?,'ready',?,?,?,?,?,?,?)",
                        pid, title, content, "[]", published, acc, "老账号", "主题:吃法搭配｜角度:x", "产品软文", json.dumps(review or {}), json.dumps(cc)).lastrowid
        made.append(did)
        return did

    try:
        # ---- 对号:豆腐乳 / 腐乳算同一个词 ----
        d1 = mk("挑腐乳踩过雷?看这三点", published=1)
        m = qianfan.match_drafts([{"note_id": "n1", "title": "挑豆腐乳踩过雷?看这三点", "account_id": "u1", "views": 10, "likes": 1}])
        rep.check("对号:发布时把「腐乳」改成「豆腐乳」,也能自动对上", m.get(d1, {}).get("note_id") == "n1", str(m))
        rep.check("对号:标题归一化不影响别的词", qianfan._norm_title("白粥没味道?一勺它救场!") == "白粥没味道一勺它救场" and qianfan._norm_title("豆腐乳,好吃") == "腐乳好吃")

        # ---- 最接近的笔记 ----
        items = [{"note_id": "a", "title": "白粥没味道?一勺它直接救场", "account_id": "u1"}, {"note_id": "b", "title": "白粥没味道?一勺它直接救场!!", "account_id": "u2"},
                 {"note_id": "c", "title": "完全无关的一篇", "account_id": "u1"}]
        c1 = qianfan.closest_note("白粥没味道?一勺腐乳直接救场", "u1", items, set())
        rep.check("最接近的笔记:同账号的优先(即使别的账号的更像);已被别的稿子对上的不再推荐;一点都不像返回空",
                  c1 and c1["note_id"] == "a" and c1["same_account"] and qianfan.closest_note("白粥没味道?一勺腐乳直接救场", "u1", items, {"a"})["note_id"] == "b"
                  and qianfan.closest_note("zzz", "u1", items, set()) is None)

        # ---- 打分快照 ----
        rv = {"overall": 88, "grade": "A", "dims": {"标题": 90, "结构": 80}, "quant": {"score": 70}}
        d2 = mk("3个步骤做好腐乳拌饭?", published=1, review=rv, layout="photo_title")
        snap = loop.snapshot(db.q_one("SELECT * FROM drafts WHERE id=?", d2))
        rep.check("打分快照:总分 / 等级 / 维度 / 量化分,标题写法(数字 / 问号 / emoji)和字数,封面版式,主题和帖子类型",
                  snap["score"] == 88 and snap["grade"] == "A" and snap["dims"]["标题"] == 90 and snap["quant"] == 70 and snap["t_digit"] and snap["t_mark"] and not snap["t_emoji"]
                  and snap["title_len"] == len("3个步骤做好腐乳拌饭?") and snap["cover_layout"] == "photo_title" and snap["topic"] == "吃法搭配" and snap["ptype"] == "产品软文" and snap["body_len"] > 0, str(snap))
        un = mk("还没发的稿子", published=0)
        loop.evaluate()
        f_pub = db.q_one("SELECT feat FROM drafts WHERE id=?", d2)["feat"]
        f_un = db.q_one("SELECT feat FROM drafts WHERE id=?", un)["feat"]
        rep.check("打分快照:已发布的稿子自动补存,没发布的不存", isinstance(f_pub, dict) and f_pub["score"] == 88 and not f_un, str(f_pub))
        db.q_exec("UPDATE drafts SET content=?, review=? WHERE id=?", "完全改掉的内容" * 50, json.dumps({"overall": 10}), d2)
        loop.evaluate()
        rep.check("打分快照:存下来以后固定,之后再改稿子 / 再评分也不会变(拿发布时的状态和结果对比)", db.q_one("SELECT feat FROM drafts WHERE id=?", d2)["feat"]["score"] == 88)

        # ---- 对比:评分段、维度、做法 ----
        def fake(i, score, lift, tier="good", layout="a", digit=True, dims=None):
            return {"id": i, "tier": tier, "lift": lift, "days": 5, "feat": {"score": score, "dims": dims or {"标题": score, "结构": 100 - score}, "cover_kind": "html", "cover_layout": layout,
                    "t_digit": digit, "t_mark": False, "t_emoji": False, "title_len": 15, "body_len": 400, "cta": True, "ptype": "产品软文"}}
        ev = {"cfg": {"min_days": 3}, "rows": [fake(1, 92, 2.5, "winner"), fake(2, 91, 2.0, "winner"), fake(3, 85, 1.2), fake(4, 82, 1.0, "normal"), fake(5, 75, 0.6, "weak"), fake(6, 72, 0.5, "weak"),
                                               fake(7, 60, 0.3, "weak", layout="b", digit=False), {"id": 8, "tier": "observing", "lift": None, "days": 1, "feat": None},
                                               {"id": 9, "tier": "unpublished", "lift": None, "days": None, "feat": None}]}
        L = loop.learning(ev)
        bands = {b["label"]: b for b in L["bands"]}
        rep.check("评分准不准:按分数段汇总篇数 / 爆款 / 平均表现;分数越高表现越好时相关系数为正",
                  bands["90 分以上"]["n"] == 2 and bands["90 分以上"]["winners"] == 2 and bands["90 分以上"]["lift"] == 2.25 and bands["70 分以下"]["n"] == 1
                  and L["score_corr"] > 0.8 and L["n_done"] == 7 and L["n_obs"] == 1 and L["soon_days"] == 2 and not L["ready"], str(L["bands"]) + str(L["score_corr"]))
        dd = {d["name"]: d for d in L["dims"]}
        rep.check("评分准不准:各维度和表现的相关性,方向对得上(标题分高表现好为正,倒着设的结构分为负)", dd["标题"]["r"] > 0.8 and dd["结构"]["r"] < -0.8 and dd["标题"]["n"] == 7)
        fac = {f["name"]: {x["label"]: x for x in f["items"]} for f in L["factors"]}
        rep.check("哪些做法有效:封面版式 / 标题带不带数字分组,按平均表现从高到低,带样本数",
                  fac["封面版式"]["a"]["n"] == 6 and fac["封面版式"]["b"]["lift"] == 0.3 and fac["标题带数字"]["带数字"]["n"] == 6
                  and list(fac["封面版式"])[0] == "a" and "封面类型" not in fac, str(fac.keys()))
        rep.check("评分准不准:没有有结论的稿子时给空汇总(页面只显示进度);攒够 30 篇才标记 ready",
                  loop.learning({"cfg": {"min_days": 3}, "rows": []})["n_done"] == 0 and loop.learning({"cfg": {"min_days": 3}, "rows": []})["bands"] == []
                  and loop.learning({"cfg": {"min_days": 3}, "rows": [fake(i, 80 + i % 10, 1 + i / 100) for i in range(30)]})["ready"])
        rep.check("复盘总览带 learn 汇总", "learn" in loop.overview(loop.evaluate(register=False)) and loop.overview(loop.evaluate(register=False))["learn"]["need"] == 30)

        # ---- 没对上的页面带「最接近的笔记」----
        qianfan.ingest({"kind": "notes", "items": [{"note_id": "z9", "title": "完全不同的某篇笔记", "views": 5, "account_id": "u1", "account_name": "老账号"}]})
        pg = api_user.loop_page(reg="nodata", size=100)
        mine = [x for x in pg["reg"]["items"] if x["id"] == d2]
        rep.check("标了发布但没对上:每篇带最接近的笔记(同账号、相似度),没有相近的为空对象", mine and "closest" in mine[0], str(pg["reg"]["items"][:1]))
        # ---- 发布时改了标题 / 封面:绑定笔记(候选 / 链接)、改动标记、归因过滤 ----
        def raises(fn):
            try:
                fn()
            except Exception:
                return True
            return False
        nid_a, nid_b = "a" * 24, "b" * 24
        qianfan.ingest({"kind": "notes", "items": [{"note_id": nid_a, "title": "完全改写过的新标题", "views": 50, "likes": 3, "account_id": "u1", "account_name": "老账号", "published_at": "2026-11-13"}]})
        d3 = mk("原来的标题闭环测试稿", published=1)
        r_l = api_user.link_note(d3, api_user.LinkIn(url=f"https://www.xiaohongshu.com/explore/{nid_a}?xsec_token=abc", cover=True))
        pe = db.q_one("SELECT qf_note_id, published_at, pub_edit FROM drafts WHERE id=?", d3)
        rep.check("绑定笔记:粘贴笔记链接(从链接里取 24 位笔记 ID),笔记在千帆数据里就直接对上;登记「改了封面」;发布日期取笔记的",
                  not r_l["pending"] and pe["qf_note_id"] == nid_a and pe["published_at"] == "2026-11-13" and pe["pub_edit"]["cover"] is True and pe["pub_edit"]["src"] == "manual", str(pe))
        d4 = mk("另一篇闭环测试稿", published=1)
        r_p = api_user.link_note(d4, api_user.LinkIn(url=f"https://www.xiaohongshu.com/discovery/item/{nid_b}", body=True))
        rep.check("绑定笔记:笔记还不在千帆数据里(刚发、没同步)也先记下 ID,返回 pending", r_p["pending"] and db.q_one("SELECT qf_note_id FROM drafts WHERE id=?", d4)["qf_note_id"] == nid_b)
        qianfan.ingest({"kind": "notes", "items": [{"note_id": nid_b, "title": "另一篇闭环测试稿", "views": 9, "likes": 1, "account_id": "u1", "account_name": "老账号", "published_at": "2026-11-13"},
                                                    {"note_id": nid_a, "title": "完全改写过的新标题", "views": 50, "likes": 3, "account_id": "u1", "account_name": "老账号", "published_at": "2026-11-13"}]})
        ev2 = loop.evaluate()
        rows2 = {r["id"]: r for r in ev2["rows"]}
        rep.check("绑定笔记:数据同步过来后自动对上,并算出标题相似度;改动标记保留;改写过标题的是「小改 / 大改」,原样标题 + 改了正文是「小改」",
                  (rows2[d4]["edit"] or {}).get("title_sim") == 1.0 and (rows2[d4]["edit"] or {}).get("body") and (rows2[d4]["edit"] or {}).get("level") == "小改" and (rows2[d3]["edit"] or {}).get("title_sim", 1) < 0.5 and (rows2[d3]["edit"] or {}).get("level") == "大改"
                  and rows2[d3]["views"] == 50 and db.q_one("SELECT pub_edit FROM drafts WHERE id=?", d4)["pub_edit"]["title_sim"] == 1.0, str({k: rows2[k]["edit"] for k in (d3, d4)}))
        rep.check("绑定笔记:链接里没有笔记 ID、不是 24 位 ID 都 400;note_id 为空且没链接 = 取消绑定",
                  raises(lambda: api_user.link_note(d3, api_user.LinkIn(url="https://xhslink.com/abc"))) and raises(lambda: api_user.link_note(d3, api_user.LinkIn(note_id="123")))
                  and api_user.link_note(d4, api_user.LinkIn())["ok"] and db.q_one("SELECT qf_note_id FROM drafts WHERE id=?", d4)["qf_note_id"] == "")
        api_user.set_edits(d4, api_user.EditsIn(cover=True, body=False))
        pe4 = db.q_one("SELECT pub_edit FROM drafts WHERE id=?", d4)["pub_edit"]
        rep.check("改动标记:事后补记 / 修改封面和正文;不传的不动", pe4["cover"] is True and pe4["body"] is False and pe4["title_sim"] == 1.0)
        ei = loop.edit_info
        rep.check("改动程度:没改 = 原样;标题 0.5–0.8 或只改封面 / 正文 = 小改;封面和正文都改,或标题 < 0.5 = 大改;没记录的按没改算",
                  ei(None, 1.0)["level"] == "原样" and ei({}, None)["level"] == "原样" and ei({}, 0.6)["level"] == "小改" and ei({"cover": True}, 1.0)["level"] == "小改"
                  and ei({"cover": True, "body": True}, 1.0)["level"] == "大改" and ei({}, 0.4)["level"] == "大改")

        # 归因过滤:改了封面的不算封面做法;标题改多了的不算标题做法;大改的不进评分对比
        def fe(i, lift, edit, layout="a", digit=True):
            r = fake(i, 80 + i, lift, layout=layout, digit=digit)
            r["edit"] = edit
            return r
        ok_e = {"level": "原样", "title_sim": 1.0, "cover": False, "body": False}
        ev3 = {"cfg": {"min_days": 3}, "rows": [fe(1, 2.0, ok_e), fe(2, 1.0, ok_e, layout="b", digit=False),
                                               fe(3, 3.0, {"level": "小改", "title_sim": 1.0, "cover": True, "body": False}, layout="b"),
                                               fe(4, 0.2, {"level": "小改", "title_sim": 0.6, "cover": False, "body": False}, digit=False),
                                               fe(5, 0.1, {"level": "大改", "title_sim": 0.3, "cover": True, "body": True})]}
        L3 = loop.learning(ev3)
        fac3 = {f["name"]: {x["label"]: x["n"] for x in f["items"]} for f in L3["factors"]}
        rep.check("归因过滤:改了封面的不进封面版式对比;标题改多了的不进标题带数字对比;大改的不进评分对比,单独计数",
                  fac3["封面版式"] == {"a": 2, "b": 1} and fac3["标题带数字"] == {"带数字": 2, "不带数字": 1} and L3["n_done"] == 4 and L3["n_edited"] == 1 and sum(b["n"] for b in L3["bands"]) == 4, str(fac3))
        eg = {x["label"]: x for x in L3["edits"]}
        rep.check("改过的 vs 原样:按原样 / 小改 / 大改分组的篇数和平均表现", eg["原样"]["n"] == 2 and eg["小改"]["n"] == 2 and eg["大改"]["n"] == 1 and eg["原样"]["lift"] == 1.5 and eg["小改"]["lift"] == 1.6)

        # 发布一致率
        ad = loop.adherence({"rows": [{"published": True, "created_by": "甲", "edit": {"level": "原样"}}, {"published": True, "created_by": "甲", "edit": {"level": "大改"}},
                                      {"published": True, "created_by": "甲", "edit": None}, {"published": True, "created_by": "乙", "edit": None}, {"published": False, "created_by": "乙", "edit": None}]})
        a_ = {x["name"]: x for x in ad}
        rep.check("发布一致率:每人已发布 / 对上笔记 / 对上率 / 原样发布率;没发布的不算", a_["甲"]["published"] == 3 and a_["甲"]["linked"] == 2 and a_["甲"]["link_rate"] == 0.67 and a_["甲"]["original_rate"] == 0.5
                  and a_["乙"]["published"] == 1 and a_["乙"]["link_rate"] == 0 and a_["乙"]["original_rate"] is None)

        # 候选:同账号 + 登记日期接近的排前面,别的账号的要标题像才给;最多 3 个
        its = [{"note_id": "n_same_near", "title": "毫不相干的标题甲", "account_id": "u1", "published_at": "2026-11-13", "views": 1},
               {"note_id": "n_same_far", "title": "毫不相干的标题乙", "account_id": "u1", "published_at": "2026-10-01", "views": 2},
               {"note_id": "n_other_sim", "title": "原来的标题闭环测试稿!", "account_id": "u2", "published_at": "2026-11-13", "views": 3},
               {"note_id": "n_other_diff", "title": "毫不相干的标题丙", "account_id": "u2", "published_at": "2026-11-13", "views": 4}]
        cs = qianfan.closest_notes("原来的标题闭环测试稿", "u1", its, set(), "2026-11-13")
        ids_ = [c["note_id"] for c in cs]
        rep.check("候选笔记:同账号且发布日期接近的、标题像的(哪怕是别的账号)入选,同账号但日期差很远又不像的、别的账号又不像的不给",
                  "n_same_near" in ids_ and "n_other_sim" in ids_ and "n_same_far" not in ids_ and "n_other_diff" not in ids_ and len(cs) <= 3 and cs[0]["note_id"] in ("n_other_sim", "n_same_near"), str(ids_))
        # 标记已发布后弹出的绑定窗口:每篇给候选;已自动对上的标出来;别人的 / 不存在的略过
        nid_c = "c" * 24
        qianfan.ingest({"kind": "notes", "items": [{"note_id": nid_a, "title": "完全改写过的新标题", "views": 50, "likes": 3, "account_id": "u1", "account_name": "老账号", "published_at": "2026-11-13"},
                                                    {"note_id": nid_b, "title": "另一篇闭环测试稿", "views": 9, "likes": 1, "account_id": "u1", "account_name": "老账号", "published_at": "2026-11-13"},
                                                    {"note_id": nid_c, "title": "和闭环无关的某篇笔记", "views": 7, "likes": 0, "account_id": "u1", "account_name": "老账号", "published_at": "2026-11-13"}]})
        d5 = mk("毫不相干的标题甲", published=1)
        db.q_exec("UPDATE drafts SET published_at='2026-11-13' WHERE id=?", d5)
        bc = {x["id"]: x for x in api_user.bind_cands(f"{d5},{d4},99999999,abc")["items"]}
        rep.check("绑定窗口的候选:每篇带候选笔记(同账号、日期接近),不存在的 id 略过;空参数返回空",
                  d5 in bc and any(c["note_id"] == nid_c and c["same_account"] for c in bc[d5]["cands"]) and 99999999 not in bc and api_user.bind_cands("")["items"] == [], str(bc.get(d5)))
        rep.check("绑定窗口的候选:已经自动对上笔记的稿子标出 matched、不再给候选", bc[d4]["matched"] is None or bc[d4]["cands"] == [])
        pg2 = api_user.loop_page(reg="nodata", size=100)
        mine2 = {x["id"]: x for x in pg2["reg"]["items"]}
        rep.check("没对上的页面:每篇带候选列表和已记下链接的标记", all("cands" in x for x in mine2.values()))
    finally:
        if made:
            db.q_exec("DELETE FROM drafts WHERE id IN (%s)" % ",".join(map(str, made)))
        db.q_exec("DELETE FROM projects WHERE id=?", pid)
