import json
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)
DB_PATH = DATA / "workbench.db"

DEFAULT_SETTINGS = {
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_api_key": "",
    "deepseek_model": "deepseek-chat",
    "image_base_url": "",   # OpenAI 兼容: {base}/images/generations
    "image_api_key": "",
    "image_model": "",
    "image_size": "3:4",         # 比例(3:4 是小红书图文的标准比例;9:16 等)或像素(1024x1536),取决于服务商
    "image_resolution": "",      # 清晰度 1k / 2k / 4k;留空则不传,用服务商默认值
    "collect_limit": "20",       # 每个采集任务要抓的笔记数
    "collect_max_age_days": "180",  # 只采近多少天发布的笔记;0 = 不限
    "collect_note_type": "image",   # image 只要图文 / all 不限
    "collect_sort": "popularity",   # popularity 最热 / general 综合 / latest 最新
    "collector_token": "",       # 采集端鉴权,首次启动自动生成
    "public_base_url": "",       # 给采集端用的对外地址,如 http://192.168.1.100:18765
    "text_concurrency": "3",     # 复刻:同时写文案的篇数
    "image_concurrency": "2",    # 复刻:同时生图的篇数
    "image_api_concurrency": "4",  # 向生图服务商同时发出的请求数上限(复刻配图和 Live 场景图共用);一篇里的几张图并行生成,受它限制
    "auto_tag": "1",             # 帖子入库后由 DeepSeek 自动打内容标签
    "tier_viral": "3000",        # 互动量(赞+藏+评)≥ 此值为「爆款」
    "tier_hot": "1000",          # 互动量 ≥ 此值为「热门」,否则「普通」
    "auto_fix": "1",             # 复刻后命中高风险词时自动让 DeepSeek 修一次
    "mirror_images": "1",        # 收到笔记后立刻把图片下载到本地(采集端的图片链接会过期)
    "mirror_concurrency": "4",   # 同一篇笔记同时下载几张图
    "vision_base_url": "",       # 看产品图的接口地址(OpenAI 兼容,如千问 https://dashscope.aliyuncs.com/compatible-mode/v1);留空则沿用生图接口
    "vision_api_key": "",        # 看图接口的 Key;留空则沿用生图接口的 Key
    "vision_model": "claude-sonnet-5-5",  # 看产品图用的模型,如千问 qwen-vl-max
    "product_real_slots": "0",   # 有产品时,最后几张直接用产品原图(不让 AI 重画,包装 100% 真实);0 = 全部 AI 生成
    "product_max_refs": "3",     # AI 生图时最多给模型几张产品图作参考
    "auto_pass_score": "75",     # 一键批量:审稿总分达到这个数才算合格,不够自动重写一次
    "auto_retry": "1",           # 不合格的稿子自动重写几次
    "auto_refs": "5",            # 每个主题取互动量最高的几篇做拆解参考
    "auto_max_topics": "3",      # 最多拆解几个主题
    "library_collect_enabled": "1",  # 采集端空闲时是否领取「笔记库沉淀任务」(1/0)
    "qf_enabled": "0",             # 是否自动给采集端下发千帆数据同步任务(1/0)
    "qf_kinds": "account,notes,hot_notes,hot_topics,hot_keywords",  # 要同步哪几类数据
    "qf_account_hours": "24",      # 账号数据 / 笔记表现多久同步一次(小时)
    "qf_hot_hours": "12",          # 热门榜单多久同步一次(小时)
    "qf_categories": "",           # 热门榜单按哪些品类抓,逗号分隔;留空 = 全站
    "qf_periods": "7d",            # 榜单周期,逗号分隔(如 1d,7d,30d)
    "image_count_weights": "20,30,30,20",  # 每篇笔记图片总张数(含封面)为 1/2/3/4 张的比例
    "auto_library_min": "15",    # 笔记库里已有这么多篇相关笔记,就跳过采集直接用
    "auto_min_relevant": "15",    # 相关笔记少于这个数,自动补充搜索词再采一轮
    "auto_min_notes": "5",       # 采到的笔记少于这个数就停下并提示
    "auto_collect_wait_min": "90",  # 最多等采集端多少分钟
    "auto_write_wait_min": "60",    # 写稿配图最多等多少分钟
    "auto_images": "1",          # 批量生成时是否配图(1/0)
    "auto_enough_relevant": "40",  # 项目里相关笔记已经有这么多,剩下还没采的关键词任务就不用等了(0 = 不提前结束)
    "auto_max_keywords": "8",      # 一键生成时,一个任务最多用几个关键词(词越多,笔记库沉淀越多)
    "auto_default_keywords": "3",   # 没填关键词时取赛道预设词的前几个
    "auto_max_count": "50",      # 一次最多生成几篇
    "auto_compress": "1",        # 正文明显超过爆款组字数时,自动压缩一次(原稿保留,可切换)
    "auto_review": "1",          # 复刻后自动审稿打分并给出修改版
    "review_comments": "0",      # 审稿时附带模拟评论区(多一些 token)
    "loop_win_lift": "2",        # 爆款判定:互动(赞+藏+评)≥ 本账号平均的几倍
    "loop_min_eng": "20",        # 爆款判定:互动至少多少(防止账号数据太小,几个赞就算爆款)
    "loop_min_days": "3",        # 发布满几天,数据才算够久、可以下结论(之前显示「观察中」)
    "loop_spawn_count": "5",     # 以爆款为样本继续复刻时,默认复刻几篇
    "loop_auto": "0",            # 是否自动继续复刻新出现的爆款(1/0);会消耗文案和生图额度,默认关
    "loop_daily_cap": "10",      # 自动继续复刻每天最多生成几篇
    "collector_min_cv": "0",     # collector min version: lower qianfan collectors get 401 and exit (flushes stale scripts left after upgrades)
    "kol_enabled": "0",          # 是否自动让采集端去千帆买手广场采集达人(1/0);还需要「千帆数据同步」总开关打开
    "kol_hours": "72",           # 达人库多久自动更新一次(小时)
    "kol_cats": "",              # 采集时筛选哪些「合作品类」,逗号分隔(要和千帆里的品类名一致);留空 = 用下面的目标品类
    "kol_targets": "保健食品,咖啡/麦片/冲饮,传统滋补营养品,粮油调味,零食/坚果",   # 我们产品所属的品类关键词(对应买手广场「合作品类」的一级品类名,写前几个字就行):达人历史合作命中它,适配度才高
    "kol_content": "美食,健康养生,低脂低卡",   # 和我们产品相关的内容分类关键词:达人发的内容是这类,适配度算「一般」
    "kol_contact_enabled": "0",  # 是否允许员工在达人库里「获取联系方式」(1/0);默认关,管理员确认要用再打开
    "kol_contact_paused": "",    # 获取联系方式被自动暂停的原因(采集端遇到风控提示时写入,管理员处理完再把开关打开并清空)
    "kol_contact_cap": "30",     # 每天最多获取几位达人的联系方式(每位要打开一次邀约弹窗,量大了容易被千帆当成异常操作)
    "kol_pages": "30",           # 每个品类最多翻几页(每页 20 位)
    "kb_common": "",             # 全赛道通用知识(平台规则等),首次启动写入默认内容
    "admin_password_hash": "",   # 管理端登录密码(只存哈希)
    "user_password_hash": "",    # 用户端访问密码,留空则用户端无需登录
}
SECRET_KEYS = {"admin_password_hash", "user_password_hash"}

DEFAULT_TRACKS = [
    ("美妆护肤", ["通勤妆", "新手化妆", "油皮护肤", "平价彩妆", "敏感肌", "防晒"]),
    ("穿搭", ["小个子穿搭", "通勤穿搭", "微胖穿搭", "秋冬穿搭", "平价好物"]),
    ("美食", ["减脂餐", "快手早餐", "家常菜", "空气炸锅", "探店"]),
    ("母婴育儿", ["宝宝辅食", "早教", "孕期", "宝宝用品", "育儿经验"]),
    ("家居生活", ["租房改造", "收纳", "装修避坑", "小户型", "家居好物"]),
    ("旅行", ["周末游", "小众景点", "旅行攻略", "citywalk", "酒店推荐"]),
    ("健身减脂", ["居家健身", "减脂", "瑜伽", "体态矫正", "跑步"]),
    ("职场成长", ["面试", "简历", "副业", "时间管理", "职场沟通"]),
    ("学习考试", ["考研", "英语学习", "考公", "学习方法", "自律"]),
    ("数码科技", ["手机推荐", "效率App", "平板", "耳机", "桌搭"]),
    ("宠物", ["养猫", "养狗", "猫粮测评", "新手养宠"]),
    ("情感心理", ["恋爱", "自我成长", "情绪管理", "独居"]),
]

JSON_COLS = ("keywords", "images", "tags", "note_ids", "data", "ai_tags", "checks", "stats", "review", "variant", "state", "profile", "cover_card", "images_local",
             "alt_titles", "image_prompts", "template_ids", "opts", "gen", "categories", "history_cats", "coop_tags", "willing")


# 全局笔记库:同一篇帖子只存一份(按 uid 去重),通过 project_notes 关联到多个项目
NOTES_DDL = """
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE,
    url TEXT DEFAULT '', title TEXT, content TEXT DEFAULT '', author TEXT DEFAULT '',
    likes INTEGER DEFAULT 0, collects INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
    cover TEXT DEFAULT '', images TEXT DEFAULT '[]', tags TEXT DEFAULT '[]',
    note_type TEXT DEFAULT 'normal', published_at TEXT DEFAULT '',
    -- 来源分类:首次入库时的赛道、关键词、采集任务、采集端
    track TEXT DEFAULT '', keyword TEXT DEFAULT '', source TEXT DEFAULT 'collector',
    task_id INTEGER, worker TEXT DEFAULT '',
    -- AI 内容标签
    ai_status TEXT DEFAULT 'pending', ai_type TEXT DEFAULT '', ai_audience TEXT DEFAULT '',
    ai_highlight TEXT DEFAULT '', ai_tags TEXT DEFAULT '[]', ai_error TEXT DEFAULT '',
    -- 图片转存:本地文件名与 images 一一对应,转存失败的位置为空串
    mirror_status TEXT DEFAULT 'pending', mirror_error TEXT DEFAULT '',
    cover_local TEXT DEFAULT '', images_local TEXT DEFAULT '[]', author_id TEXT DEFAULT '',
    -- 采集端传来的原始数据(JSON),多次回传按字段合并,后到的覆盖同名字段;工作台没用到的字段也完整保留在这里
    raw TEXT DEFAULT '{}',
    -- 主题:这篇笔记实际讲什么(AI 按内容判断,可手动改);和 keyword(采集时的搜索词)是两回事
    topic TEXT DEFAULT '',
    created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_notes_track ON notes(track);
CREATE INDEX IF NOT EXISTS ix_notes_keyword ON notes(keyword);
CREATE INDEX IF NOT EXISTS ix_notes_ai ON notes(ai_status, ai_type);
CREATE TABLE IF NOT EXISTS project_notes(
    project_id INTEGER, note_id INTEGER, task_id INTEGER,
    created TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id, note_id));
CREATE INDEX IF NOT EXISTS ix_pn_note ON project_notes(note_id);
"""


def tier_sql(a: str = "n") -> str:
    """数据分级不落库,按当前阈值实时计算,改阈值立即生效。"""
    s = get_settings()
    v, h = int(s["tier_viral"] or 10000), int(s["tier_hot"] or 1000)
    x = f"({a}.likes+{a}.collects+{a}.comments)"
    return f"CASE WHEN {x}>={v} THEN 'viral' WHEN {x}>={h} THEN 'hot' ELSE 'normal' END"


def conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    return c


# ---------- 数据库版本与迁移 ----------
# schema_version 表记录已执行到哪一版。以后改表结构:在 MIGRATIONS 末尾加一项 (版本号, 名称, 函数),
# 启动时按版本号依次执行没跑过的;每一项只会跑一次。改表前会先自动备份数据库。
# 第 1 版 = 到目前为止的全部结构(幂等:老库跑一遍不会有变化)。
def add_column(c, table: str, col: str, ddl: str):
    """迁移里加字段用:已有就跳过。"""
    if col not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def _m001_baseline(c):
    old = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "notes" in old and "ai_status" not in {r["name"] for r in c.execute("PRAGMA table_info(notes)")}:
        _migrate_v2(c)
    c.executescript(NOTES_DDL)
    have = {r["name"] for r in c.execute("PRAGMA table_info(notes)")}
    for col, ddl in (("mirror_status", "TEXT DEFAULT 'pending'"), ("mirror_error", "TEXT DEFAULT ''"),
                     ("cover_local", "TEXT DEFAULT ''"), ("images_local", "TEXT DEFAULT '[]'"), ("author_id", "TEXT DEFAULT ''"), ("raw", "TEXT DEFAULT '{}'"), ("topic", "TEXT DEFAULT ''"), ("dup_of", "INTEGER")):
        if col not in have:
            c.execute(f"ALTER TABLE notes ADD COLUMN {col} {ddl}")
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_mirror ON notes(mirror_status)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_topic ON notes(topic)")
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        -- 项目 = 一次选赛道:赛道 + 关键词 + 想做的内容方向
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT, track TEXT, keywords TEXT DEFAULT '[]',
            direction TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 采集任务:由采集端通过 GET /api/tasks 领取
        CREATE TABLE IF NOT EXISTS crawl_tasks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, type TEXT DEFAULT 'keyword',
            query TEXT, lim INTEGER DEFAULT 20, status TEXT DEFAULT 'pending', error TEXT DEFAULT '',
            worker TEXT DEFAULT '', note_count INTEGER DEFAULT 0, claimed_at TEXT, finished_at TEXT,
            created TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 采集端心跳(每次领任务时更新)
        CREATE TABLE IF NOT EXISTS collectors(
            worker TEXT PRIMARY KEY, ip TEXT DEFAULT '', last_seen REAL DEFAULT 0,
            claimed INTEGER DEFAULT 0, first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 爆款拆解卡
        CREATE TABLE IF NOT EXISTS playbooks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, name TEXT DEFAULT '',
            note_ids TEXT DEFAULT '[]', data TEXT DEFAULT '{}', status TEXT DEFAULT 'queued',
            error TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 用户端可选的赛道与推荐关键词,由管理端维护
        CREATE TABLE IF NOT EXISTS tracks(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, keywords TEXT DEFAULT '[]',
            sort INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1);
        -- 合规词库:复刻成稿后逐条扫描,管理端维护
        CREATE TABLE IF NOT EXISTS rules(
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, word TEXT, replacement TEXT DEFAULT '',
            exclude TEXT DEFAULT '', note TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
            created TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(category, word));
        -- 关键词推荐的缓存(同一个词 30 天内不重复问 AI)
        -- 一键批量生成:每个项目一条,记录当前走到哪一步
        CREATE TABLE IF NOT EXISTS autoruns(
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER UNIQUE, target INTEGER DEFAULT 0,
            status TEXT DEFAULT 'collecting', msg TEXT DEFAULT '', state TEXT DEFAULT '{}',
            created TEXT DEFAULT CURRENT_TIMESTAMP, updated TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 产品:上传的产品图和资料,一键生成时当作真实素材
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT DEFAULT '', price TEXT DEFAULT '',
            selling_points TEXT DEFAULT '', audience TEXT DEFAULT '', forbid TEXT DEFAULT '',
            images TEXT DEFAULT '[]', profile TEXT DEFAULT '{}', created TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 千帆后台数据快照(账号 / 笔记表现 / 热门榜),由采集端回传
        CREATE TABLE IF NOT EXISTS qf_snapshots(
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, scope TEXT DEFAULT '', period TEXT DEFAULT '',
            data TEXT DEFAULT '{}', snap_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS qf_snap_kind ON qf_snapshots(kind, scope, period, id);
        -- 我们账号的笔记按标题归类(内容类型 + 主题),用来按账号汇总哪类选题表现好
        CREATE TABLE IF NOT EXISTS qf_note_tags(
            note_id TEXT PRIMARY KEY, title TEXT DEFAULT '', ctype TEXT DEFAULT '', topic TEXT DEFAULT '',
            angle TEXT DEFAULT '', tagged_at TEXT DEFAULT CURRENT_TIMESTAMP);
        -- 千帆店铺:每个店铺一个采集端(各自登录),数据按店铺分开存
        CREATE TABLE IF NOT EXISTS qf_shops(
            shop_id TEXT PRIMARY KEY, name TEXT DEFAULT '', first_seen TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS kw_cache(
            word TEXT, track TEXT, data TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(word, track));
        CREATE TABLE IF NOT EXISTS drafts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT, template_ids TEXT,
            title TEXT DEFAULT '', alt_titles TEXT DEFAULT '[]', content TEXT DEFAULT '',
            tags TEXT DEFAULT '[]', image_prompts TEXT DEFAULT '[]', images TEXT DEFAULT '[]',
            status TEXT DEFAULT 'new', error TEXT DEFAULT '',
            created TEXT DEFAULT CURRENT_TIMESTAMP);
        """
    )
    cols = {r["name"] for r in c.execute("PRAGMA table_info(drafts)")}
    for col, ddl in (("project_id", "INTEGER"), ("playbook_id", "INTEGER"),
                     ("angle", "TEXT DEFAULT ''"), ("extra", "TEXT DEFAULT ''"),
                     ("with_images", "INTEGER DEFAULT 1"), ("checks", "TEXT DEFAULT '{}'"), ("review", "TEXT DEFAULT '{}'"),
                     ("variant", "TEXT DEFAULT '{}'"), ("auto_ok", "INTEGER"), ("auto_note", "TEXT DEFAULT ''"),
                     ("published", "INTEGER DEFAULT 0"), ("published_at", "TEXT DEFAULT ''"), ("cover_card", "TEXT DEFAULT '{}'")):
        if col not in cols:
            c.execute(f"ALTER TABLE drafts ADD COLUMN {col} {ddl}")
    for t, col, ddl in (("tracks", "knowledge", "TEXT DEFAULT ''"), ("playbooks", "stats", "TEXT DEFAULT '{}'"),
                          ("crawl_tasks", "max_age_days", "INTEGER DEFAULT 180"), ("crawl_tasks", "note_type", "TEXT DEFAULT 'image'"),
                          ("crawl_tasks", "sort", "TEXT DEFAULT 'popularity'"), ("projects", "product_id", "INTEGER"),
                          ("crawl_tasks", "purpose", "TEXT DEFAULT 'project'"), ("crawl_tasks", "label", "TEXT DEFAULT ''"),
                          ("crawl_tasks", "new_count", "INTEGER DEFAULT 0"), ("crawl_tasks", "params", "TEXT DEFAULT ''"),
                          ("collectors", "known_calls", "INTEGER DEFAULT 0"), ("collectors", "known_at", "REAL DEFAULT 0"),
                          ("collectors", "known_total", "INTEGER DEFAULT 0"), ("collectors", "known_hit", "INTEGER DEFAULT 0"),
                          ("qf_snapshots", "account_id", "TEXT DEFAULT ''"),
                          ("projects", "account_id", "TEXT DEFAULT ''"), ("projects", "account_name", "TEXT DEFAULT ''"),
                          ("drafts", "account_id", "TEXT DEFAULT ''"), ("drafts", "account_name", "TEXT DEFAULT ''"),
                          ("qf_note_tags", "angle", "TEXT DEFAULT ''"),
                          ("projects", "angle_pref", "TEXT DEFAULT ''"), ("projects", "account_style", "TEXT DEFAULT ''"),
                          ("qf_snapshots", "shop_id", "TEXT DEFAULT ''")):
        if col not in {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}:
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} {ddl}")
    c.execute("CREATE INDEX IF NOT EXISTS qf_snap_acc ON qf_snapshots(kind, account_id, id)")
    c.execute("CREATE INDEX IF NOT EXISTS qf_snap_shop ON qf_snapshots(kind, shop_id, scope, period, id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_drafts_project ON drafts(project_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_tasks_status ON crawl_tasks(status, type)")
    if "templates" in old and "notes" not in old:
        _migrate_v1(c)


def _m002_staff(c):
    """人员管理:员工账号(前端登录用),任务记录发起人。token_ver 变化会让该员工已登录的会话全部失效。"""
    c.execute("""CREATE TABLE IF NOT EXISTS staff(
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE, name TEXT DEFAULT '',
        password_hash TEXT NOT NULL, enabled INTEGER DEFAULT 1, token_ver INTEGER DEFAULT 1,
        created TEXT DEFAULT CURRENT_TIMESTAMP, last_login TEXT DEFAULT '', last_ip TEXT DEFAULT '')""")
    add_column(c, "projects", "created_by", "TEXT DEFAULT ''")


def _m003_loop(c):
    """爆款回流:记录任务是从哪篇爆款稿继续复刻出来的(spawn_from)以及第几代。"""
    add_column(c, "projects", "spawn_from", "INTEGER")
    add_column(c, "projects", "generation", "INTEGER DEFAULT 0")
    c.execute("CREATE INDEX IF NOT EXISTS ix_projects_spawn ON projects(spawn_from)")


def _m004_qf_link(c):
    """发布登记:标题改过、自动对不上时,手动把稿子对上千帆里的某篇笔记(记下千帆笔记 ID)。"""
    add_column(c, "drafts", "qf_note_id", "TEXT DEFAULT ''")


def _m005_live(c):
    """Live 图:任务表(由 Mac 端领取生成)和 Mac 端心跳表。服务端不处理图片,只记录谁要做、做到哪、成品在哪。"""
    c.executescript("""
        CREATE TABLE IF NOT EXISTS live_jobs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER, image TEXT NOT NULL, opts TEXT DEFAULT '{}',
            status TEXT DEFAULT 'pending', error TEXT DEFAULT '', worker TEXT DEFAULT '', claimed_at TEXT, finished_at TEXT,
            result_size INTEGER DEFAULT 0, created_by TEXT DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS ix_live_status ON live_jobs(status, id);
        CREATE INDEX IF NOT EXISTS ix_live_draft ON live_jobs(draft_id);
        CREATE TABLE IF NOT EXISTS live_workers(
            worker TEXT PRIMARY KEY, ip TEXT DEFAULT '', last_seen REAL DEFAULT 0, done INTEGER DEFAULT 0,
            first_seen TEXT DEFAULT CURRENT_TIMESTAMP);
    """)


def _m006_live_motion(c):
    """Live 图「真实动作」模式:先由本机显卡(ComfyUI + MiniMax H3)把静图生成短视频,再交给 Mac 做成实况。
    mode = zoom(慢推镜头,Mac 直接做)/ motion(真实动作);stage = video(等视频)/ live(等 Mac)。"""
    add_column(c, "live_jobs", "mode", "TEXT DEFAULT 'zoom'")
    add_column(c, "live_jobs", "stage", "TEXT DEFAULT 'live'")
    add_column(c, "live_jobs", "video_size", "INTEGER DEFAULT 0")
    c.execute("""CREATE TABLE IF NOT EXISTS live_vworkers(
        worker TEXT PRIMARY KEY, ip TEXT DEFAULT '', last_seen REAL DEFAULT 0, done INTEGER DEFAULT 0,
        first_seen TEXT DEFAULT CURRENT_TIMESTAMP)""")


def _m007_live_upload(c):
    """Live 图:图片来源不止成品库——可以直接上传图片(upload),也可以上传产品图、由服务端先用生图模型生成一张
    「手机随手拍」风格的场景图(gen)。stage 多了一个 image(服务端生成场景图中)。title 给没有稿子的任务显示名称。"""
    add_column(c, "live_jobs", "title", "TEXT DEFAULT ''")
    add_column(c, "live_jobs", "src", "TEXT DEFAULT 'draft'")
    add_column(c, "live_jobs", "gen", "TEXT DEFAULT '{}'")


def _m008_kol(c):
    """达人库:千帆买手广场里采集到的合作达人(按 creator_id 去重),加上我们自己记的建联状态和备注。"""
    c.execute("""CREATE TABLE IF NOT EXISTS kol_creators(
        id INTEGER PRIMARY KEY AUTOINCREMENT, creator_id TEXT NOT NULL UNIQUE, name TEXT DEFAULT '', avatar TEXT DEFAULT '', city TEXT DEFAULT '', level TEXT DEFAULT '',
        fans INTEGER DEFAULT 0, categories TEXT DEFAULT '[]', history_cats TEXT DEFAULT '[]', coop_tags TEXT DEFAULT '[]', live_plan TEXT DEFAULT '',
        gmv_text TEXT DEFAULT '', gmv_min REAL DEFAULT 0, price_text TEXT DEFAULT '', price_min REAL DEFAULT 0, price_max REAL DEFAULT 0, url TEXT DEFAULT '',
        tier TEXT DEFAULT '', fit INTEGER DEFAULT 0, score INTEGER DEFAULT 0, source TEXT DEFAULT '',
        status TEXT DEFAULT 'new', note TEXT DEFAULT '', owner TEXT DEFAULT '', contacted_at TEXT DEFAULT '',
        first_seen TEXT DEFAULT CURRENT_TIMESTAMP, last_seen TEXT DEFAULT CURRENT_TIMESTAMP, seen_count INTEGER DEFAULT 1, updated TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.execute("CREATE INDEX IF NOT EXISTS ix_kol_status ON kol_creators(status)")


def _m009_kol_fields(c):
    """达人库补字段:买手广场列表里的直播、笔记、活跃粉丝等指标,以及意愿合作品类。"""
    for col, ddl in (("willing", "TEXT DEFAULT '[]'"), ("brand_gmv", "TEXT DEFAULT ''"), ("live_days", "INTEGER DEFAULT 0"), ("avg_viewers", "REAL DEFAULT 0"),
                     ("note_read_median", "INTEGER DEFAULT 0"), ("active_rate", "REAL DEFAULT 0"), ("notes_coop", "INTEGER DEFAULT 0"), ("red_id", "TEXT DEFAULT ''"), ("sex", "TEXT DEFAULT ''")):
        add_column(c, "kol_creators", col, ddl)


def _m010_kol_contact(c):
    """达人联系方式:员工在达人库里点「获取联系方式」后,采集端在买手广场的邀约弹窗里点眼睛读到的手机号 / 微信号。"""
    for col, ddl in (("phone", "TEXT DEFAULT ''"), ("wechat", "TEXT DEFAULT ''"), ("contact_at", "TEXT DEFAULT ''"),
                     ("contact_status", "TEXT DEFAULT ''"), ("contact_error", "TEXT DEFAULT ''")):
        add_column(c, "kol_creators", col, ddl)


def _m011_owner(c):
    """数据归属:员工只看自己的数据。归属记 staff.id(账号名会被删了再建,不可靠);NULL = 管理员 / 历史数据,员工看不到。
    稿子、拆解卡、采集任务经 project_id 找归属;Live 任务可能没有稿子,单独记;导出包登记发起人,下载时核对。"""
    add_column(c, "projects", "owner_id", "INTEGER")
    add_column(c, "live_jobs", "owner_id", "INTEGER")
    c.execute("CREATE INDEX IF NOT EXISTS ix_projects_owner ON projects(owner_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_live_owner ON live_jobs(owner_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS exports(
        zip TEXT PRIMARY KEY, owner_id INTEGER, draft_ids TEXT DEFAULT '[]', created TEXT DEFAULT CURRENT_TIMESTAMP)""")
    # 不回填:按约定,上线前的历史任务、稿子、Live 任务全部归管理员(owner_id 保持 NULL),不分给任何员工


def _m012_kind(c):
    """帖子类型:商业硬文 / 产品软文 / 教程 / 测评 / 其他(见 app/ptype.py),以及是否和食品饮品产品相关。
    已有的笔记 kind_status 默认 pending,启动后后台自动补分类。"""
    for col, ddl in (("kind", "TEXT DEFAULT ''"), ("kind_sub", "TEXT DEFAULT ''"), ("kind_conf", "REAL DEFAULT 0"), ("kind_why", "TEXT DEFAULT ''"),
                     ("kind_rel", "INTEGER DEFAULT 1"), ("kind_status", "TEXT DEFAULT 'pending'")):
        add_column(c, "notes", col, ddl)
    c.execute("CREATE INDEX IF NOT EXISTS ix_notes_kind ON notes(kind_status, kind)")


def _m015_task_by(c):
    """采集任务的发起人(登录名)。有项目的任务发起人看项目;没有项目的(如达人库里员工点「获取联系方式」)原来没记,从这版起记在这里。"""
    add_column(c, "crawl_tasks", "created_by", "TEXT DEFAULT ''")


def _m014_task_lease(c):
    """采集任务的租约:claim_start = 第一次领走的时间(心跳续租有上限,从这里算起);tries = 超时没回传被退回的次数(到上限就标失败)。
    已经在 running 的任务把 claim_start 补成现在,免得老的卡住任务从此被无限续租。"""
    add_column(c, "crawl_tasks", "claim_start", "TEXT")
    add_column(c, "crawl_tasks", "tries", "INTEGER DEFAULT 0")
    c.execute("UPDATE crawl_tasks SET claim_start=datetime('now') WHERE status='running' AND claim_start IS NULL")


def _m013_ptype(c):
    """复刻时按帖子类型写:拆解卡记下它是哪一类(playbooks.kind),稿子记下目标类型和写完后实际判出的类型(drafts.ptype / ptype_got)。"""
    add_column(c, "playbooks", "kind", "TEXT DEFAULT ''")
    add_column(c, "drafts", "ptype", "TEXT DEFAULT ''")
    add_column(c, "drafts", "ptype_got", "TEXT DEFAULT ''")


MIGRATIONS = [
    (1, "baseline", _m001_baseline),
    (2, "staff", _m002_staff),
    (3, "loop", _m003_loop),
    (4, "qf_link", _m004_qf_link),
    (5, "live", _m005_live),
    (6, "live_motion", _m006_live_motion),
    (7, "live_upload", _m007_live_upload),
    (8, "kol", _m008_kol),
    (9, "kol_fields", _m009_kol_fields),
    (10, "kol_contact", _m010_kol_contact),
    (11, "owner", _m011_owner),
    (12, "kind", _m012_kind),
    (13, "ptype", _m013_ptype),
    (14, "task_lease", _m014_task_lease),
    (15, "task_by", _m015_task_by),
    # (2, "说明", _m002_xxx),
]


def schema_version(c) -> int:
    c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, name TEXT DEFAULT '', applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return c.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def _backup_before_migrate(c, tag: str):
    """已有数据的库,迁移前用 SQLite 备份接口留一份(WAL 库不能直接拷文件)。"""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name!='schema_version' LIMIT 1").fetchone():
        return  # 全新的空库,没有可备份的
    dst = sqlite3.connect(DB_PATH.parent / f"{DB_PATH.stem}.pre-migrate-{tag}.bak.db")
    try:
        c.commit()
        c.backup(dst)
    finally:
        dst.close()


def migrate(c):
    cur = schema_version(c)
    latest = MIGRATIONS[-1][0]
    if cur > latest:
        raise RuntimeError(f"数据库版本是 {cur},比当前程序认识的 {latest} 更新:请先升级程序,不要用旧版本打开新库。")
    todo = [m for m in MIGRATIONS if m[0] > cur]
    if not todo:
        return
    _backup_before_migrate(c, f"v{todo[0][0]}")
    for ver, name, fn in todo:
        fn(c)
        c.execute("INSERT INTO schema_version(version, name) VALUES(?,?)", (ver, name))
        c.commit()  # 每一版单独提交:中途失败时,已完成的版本不会重跑


def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        migrate(c)
        _startup_fixups(c)


def _startup_fixups(c):
    """每次启动都要做的事(不是表结构变化):补种子数据、把中断的任务退回待办。"""
    d0 = c.execute("SELECT * FROM collectors WHERE worker='default'").fetchone()
    w0 = c.execute("SELECT worker FROM collectors WHERE worker!='default' ORDER BY last_seen DESC LIMIT 1").fetchone()
    if d0 and w0:  # 以前没带采集端名称的「查询已有笔记」调用记在了 default 名下:并回真正的采集端
        c.execute("UPDATE collectors SET known_calls=COALESCE(known_calls,0)+?, known_total=COALESCE(known_total,0)+?, known_hit=COALESCE(known_hit,0)+?, "
                  "known_at=MAX(COALESCE(known_at,0),?) WHERE worker=?", (d0["known_calls"] or 0, d0["known_total"] or 0, d0["known_hit"] or 0, d0["known_at"] or 0, w0[0]))
        c.execute("DELETE FROM collectors WHERE worker='default'")
    if not c.execute("SELECT 1 FROM tracks LIMIT 1").fetchone():
        for i, (name, kws) in enumerate(DEFAULT_TRACKS):
            c.execute("INSERT INTO tracks(name,keywords,sort) VALUES(?,?,?)",
                      (name, json.dumps(kws, ensure_ascii=False), i))
    from .kb import seed
    seed(c)
    # 以前打过标签但还没有主题的笔记:重新排队补上主题(新笔记打标签时就会带主题,所以这句之后不会再命中)
    c.execute("UPDATE notes SET ai_status='pending' WHERE topic='' AND ai_status IN ('done','error')")
    # 拆解、复刻、打标签的中断任务由 worker.resume() 在启动后接着跑;
    # 已领走的采集任务按超时规则自动退回,采集端可能仍在抓,这里不动
    c.execute("UPDATE notes SET ai_status='pending' WHERE ai_status='running'")
    c.execute("UPDATE notes SET kind_status='pending' WHERE kind_status='running'")
    c.execute("UPDATE notes SET mirror_status='pending' WHERE mirror_status='running'")


def _migrate_v1(c):
    """最早版本:templates 按关键词散放 → 每个关键词建一个项目,模板转为笔记。"""
    for k in [r[0] for r in c.execute("SELECT DISTINCT keyword FROM templates")]:
        pid = c.execute("INSERT INTO projects(track,keywords) VALUES(?,?)",
                        ("未分类", json.dumps([k], ensure_ascii=False))).lastrowid
        for r in c.execute("SELECT * FROM templates WHERE keyword=?", (k,)).fetchall():
            c.execute("INSERT OR IGNORE INTO notes(uid,url,title,content,author,likes,cover,images,track,keyword,source)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,'import')",
                      (r["url"], r["url"], r["title"], r["content"], r["author"], r["likes"], r["cover"], r["images"], "未分类", k))
            nid = c.execute("SELECT id FROM notes WHERE uid=?", (r["url"],)).fetchone()[0]
            c.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id) VALUES(?,?)", (pid, nid))
        c.execute("UPDATE drafts SET project_id=? WHERE keyword=?", (pid, k))


def _migrate_v2(c):
    """笔记从「属于某个项目」改为全局笔记库:按 uid 合并重复,项目关系转到 project_notes。"""
    c.execute("ALTER TABLE notes RENAME TO notes_v1")
    c.executescript(NOTES_DDL)
    rows = c.execute("""SELECT n.*, p.track AS p_track, p.keywords AS p_kws, t.query AS t_query, t.worker AS t_worker
        FROM notes_v1 n LEFT JOIN projects p ON p.id=n.project_id LEFT JOIN crawl_tasks t ON t.id=n.task_id
        ORDER BY n.id""").fetchall()
    idmap = {}
    for r in rows:
        hit = c.execute("SELECT id FROM notes WHERE uid=?", (r["uid"],)).fetchone()
        if hit:
            nid = hit[0]
        else:
            kw = r["t_query"] or (json.loads(r["p_kws"] or "[]") or [""])[0]
            nid = c.execute(
                "INSERT INTO notes(uid,url,title,content,author,likes,collects,comments,cover,images,tags,note_type,"
                "published_at,track,keyword,source,task_id,worker,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["uid"], r["url"], r["title"], r["content"], r["author"], r["likes"], r["collects"], r["comments"],
                 r["cover"], r["images"], r["tags"], r["note_type"], r["published_at"], r["p_track"] or "", kw,
                 "collector" if r["task_id"] else "import", r["task_id"], r["t_worker"] or "", r["created"], r["created"])).lastrowid
        idmap[r["id"]] = nid
        c.execute("INSERT OR IGNORE INTO project_notes(project_id,note_id,task_id,created) VALUES(?,?,?,?)",
                  (r["project_id"], nid, r["task_id"], r["created"]))
    for pb in c.execute("SELECT id, note_ids FROM playbooks").fetchall() if c.execute("SELECT 1 FROM sqlite_master WHERE name='playbooks'").fetchone() else []:
        ids = [idmap.get(i, i) for i in json.loads(pb["note_ids"] or "[]")]
        c.execute("UPDATE playbooks SET note_ids=? WHERE id=?", (json.dumps(ids), pb["id"]))
    c.execute("DROP TABLE notes_v1")


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    with conn() as c:
        for r in c.execute("SELECT key,value FROM settings"):
            s[r["key"]] = r["value"]
    return s


def save_settings(d):
    with conn() as c:
        for k, v in d.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))


def q_all(sql, *a):
    with conn() as c:
        return [row_to_dict(r) for r in c.execute(sql, a)]


def q_one(sql, *a):
    with conn() as c:
        r = c.execute(sql, a).fetchone()
    return row_to_dict(r) if r else None


def q_exec(sql, *a):
    with conn() as c:
        return c.execute(sql, a)


def row_to_dict(r):
    d = dict(r)
    for k in JSON_COLS:
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                d[k] = {} if k == "data" else []
    return d
