"""规则兜底创意库 —— 没有 LLM（断网 / 无 key / 额度耗尽）时的确定性产出。

存在的意义：黑客松 Demo 现场不能因为网络问题翻车。
这一层不是「随便编几句」，而是按业态预置了可直接投放的创意骨架，
LLM 可用时会被真实生成的内容替换。
"""
from __future__ import annotations

from typing import Any, Dict

# 每个业态包：(主标题, 副标题, 卖点标签, CTA, 英文视觉 prompt, 创意方向名, 一句话创意说明)
PACKS: Dict[str, Dict[str, Any]] = {
    "手作": {
        "palette": "七夕粉",
        "font_style": "serif",
        "concepts": [
            {"name": "手作时光",
             "idea": "把「亲手做一件东西」的过程拍成温柔的画面，卖的是体验而不是商品。",
             "visual_prompt": "cozy handmade perler bead craft studio interior, warm wooden table covered with colorful small beads, a pegboard holding a half-finished heart-shaped bead pattern, tweezers resting beside it, soft afternoon window light, shallow depth of field, warm inviting tones, editorial still-life photography, no people, no hands, no text, no letters, no watermark",
             "headline": "把喜欢拼进豆子",
             "subheadline": "两个人一整个下午的安静快乐",
             "bullets": ["手把手教学", "成品可带走", "适合情侣/闺蜜"],
             "cta": "来店里坐坐"},
            {"name": "节日氛围",
             "idea": "借节日把「一起做点小事」变成必打卡的仪式感。",
             "visual_prompt": "festive craft table setup with pink and red heart shaped bead patterns, dried flowers in small vase, warm string lights bokeh in background, romantic cozy atmosphere, soft pink and cream color palette, top down flat lay, no text, no letters, no watermark",
             "headline": "节日限定的手作",
             "subheadline": "做一个只属于你们的小物件",
             "bullets": ["节日限定图纸", "现场拍照出片", "礼物包装免费"],
             "cta": "提前预约占位"},
            {"name": "作品说话",
             "idea": "用大量真实成品图制造「我也想要」的冲动。",
             "visual_prompt": "grid of colorful finished perler bead artworks arranged on a wooden surface, cute characters and patterns, bright even daylight, clean minimal composition, high detail, product photography, no text, no letters, no watermark",
             "headline": "这些全都能自己做",
             "subheadline": "零基础也能一次成功",
             "bullets": ["上百款图纸", "材料全含", "随时可来"],
             "cta": "看成品图册"},
        ],
        "copy": {
            "title": "🧩 把一下午拼成喜欢的样子",
            "body": "店里最安静的位置留给你们。挑图纸、配色、一颗一颗摆上去，时间会过得特别快。做完成品可以带走，也可以装进小礼盒送人。第一次来也不用担心，店主会一直在旁边教。",
            "bullets": ["零基础可上手，全程有人教", "材料图纸现场提供，成品直接带走", "情侣 / 闺蜜 / 亲子的安静去处"],
            "hashtags": ["#手工diy", "#拼豆", "#周末去哪儿", "#手作体验", "#约会好去处", "#小众宝藏店"],
            "cta": "想来体验的话，直接私信我们留位置就好。",
        },
        "video_script": {
            "duration_s": 15,
            "shots": [
                {"t": "0-3s", "visual": "推门而入，暖光下的木桌与满墙成品", "voiceover": "在这个城市，还有一个可以慢下来的地方。", "onscreen": "城市里的安静角落"},
                {"t": "3-6s", "visual": "手指捏起彩色小豆子的特写", "voiceover": "挑图纸、配色，一颗一颗摆上去。", "onscreen": "零基础也能做"},
                {"t": "6-10s", "visual": "两个人并肩低头做手工的背影", "voiceover": "一下午，聊天、动手、什么也不用想。", "onscreen": "一下午的安静快乐"},
                {"t": "10-13s", "visual": "成品在掌心翻转的近景", "voiceover": "做好的作品可以带走，也能装成礼物。", "onscreen": "成品可带走"},
                {"t": "13-15s", "visual": "门店环境收尾镜头 + 手写店名", "voiceover": "想来的话，评论区扣个 1。", "onscreen": "评论扣1 留位置"},
            ],
        },
        "publish_advice": [
            {"channel": "小红书", "best_time": "周五 18:00-20:00", "format": "3:4 封面 + 6 张成品图 + 体验过程", "note": "标题带 emoji，正文写清价格与人均时长，评论区自己先留一条「地址」"},
            {"channel": "朋友圈", "best_time": "周四晚 20:00", "format": "1:1 方图 + 短文案", "note": "面向到店过的老客，重点突出节日限定与预约"},
            {"channel": "抖音", "best_time": "周末 11:00 / 19:00", "format": "15 秒竖版短视频", "note": "前 3 秒必须出现成品，配轻快 BGM，主页挂地址"},
        ],
    },
    "咖啡": {
        "palette": "秋日焦糖",
        "font_style": "sans",
        "concepts": [
            {"name": "季节限定",
             "idea": "用季节限定制造「错过再等一年」的紧迫感。",
             "visual_prompt": "seasonal latte in a ceramic cup on a wooden table beside scattered coffee beans and a small dried flower, warm autumn afternoon light through window, cozy cafe atmosphere, caramel and cream color palette, close up, shallow depth of field, no text, no letters, no watermark",
             "headline": "秋天第一杯",
             "subheadline": "限定口味，只卖这个季节",
             "bullets": ["当季新豆", "每日现做", "第二杯半价"],
             "cta": "到店尝鲜"},
            {"name": "自习友好",
             "idea": "把「能坐一下午」变成核心卖点，攻学生客群。",
             "visual_prompt": "quiet cozy cafe corner with a laptop and a cup of coffee on a small table, warm desk lamp, plants and bookshelves nearby, calm study atmosphere, soft natural light, muted warm tones, no text, no letters, no watermark",
             "headline": "坐一下午也不赶",
             "subheadline": "有插座、有 wifi、有安静的角落",
             "bullets": ["座位不催", "插座充足", "续杯优惠"],
             "cta": "带电脑来"},
            {"name": "出片场景",
             "idea": "把店当作拍照场景来卖，用户帮你传播。",
             "visual_prompt": "instagrammable cafe interior with big windows, warm wooden furniture, hanging plants, latte art close up in front, bright airy daylight, soft pastel and caramel tones, lifestyle photography, no text, no letters, no watermark",
             "headline": "随手一拍就好看",
             "subheadline": "窗边那两个位置最出片",
             "bullets": ["自然光充足", "出片机位多", "新品天天有"],
             "cta": "来打卡"},
        ],
        "copy": {
            "title": "☕️ 秋天该有的一杯热咖啡",
            "body": "新到的豆子带着一点焦糖和果香，热的时候最好喝。窗边的位置下午会被阳光晒到，适合坐着发呆或者写点东西。这一季的口味只做到天冷之前。",
            "bullets": ["当季限定豆，每日现磨现做", "窗边座位采光好，适合久坐", "学生凭学生证有优惠"],
            "hashtags": ["#咖啡馆", "#秋日限定", "#城市探店", "#自习咖啡馆", "#周末去哪儿", "#咖啡日常"],
            "cta": "想喝新品的，最近来店里就好。",
        },
        "video_script": {
            "duration_s": 15,
            "shots": [
                {"t": "0-3s", "visual": "咖啡液缓缓注入杯中的特写", "voiceover": "秋天到了，该换一杯热咖啡了。", "onscreen": "秋季限定上新"},
                {"t": "3-7s", "visual": "拉花完成，杯口升起热气", "voiceover": "新到的豆子，带一点焦糖和果香。", "onscreen": "当季新豆"},
                {"t": "7-11s", "visual": "窗边座位，阳光落在桌面上", "voiceover": "靠窗的位置，下午一直有太阳。", "onscreen": "窗边最好坐"},
                {"t": "11-15s", "visual": "店内全景收尾", "voiceover": "这一季的口味，只做到天冷之前。", "onscreen": "仅限本季"},
            ],
        },
        "publish_advice": [
            {"channel": "小红书", "best_time": "工作日 12:00 / 周末 10:00", "format": "3:4 封面 + 环境图 + 新品特写", "note": "强调「限定」与「窗边座位」，正文写人均价格"},
            {"channel": "朋友圈", "best_time": "周四 17:00", "format": "1:1 方图 + 一句话文案", "note": "老客维护为主，附新品口味描述"},
            {"channel": "抖音", "best_time": "周末 18:00", "format": "15 秒竖版，前 3 秒给拉花特写", "note": "挂定位，配合门店打卡话题"},
        ],
    },
    "宠物": {
        "palette": "清新薄荷",
        "font_style": "sans",
        "concepts": [
            {"name": "毛孩子主场",
             "idea": "让宠物成为画面主角，主人看得心软就会转发。",
             "visual_prompt": "happy fluffy small dog sitting on a clean grooming table in a bright pet salon, soft towels and pastel walls, warm friendly atmosphere, gentle natural lighting, adorable expression close up, no people, no hands, no text, no letters, no watermark",
             "headline": "洗一次就变乖",
             "subheadline": "毛孩子出门也能香香的",
             "bullets": ["温和洗护", "全程可视", "不关笼子"],
             "cta": "预约洗护"},
            {"name": "细节信任",
             "idea": "用「看得见的干净」打消主人的顾虑。",
             "visual_prompt": "neat pet grooming station with clean towels, brushes and stainless steel tub, neatly folded supplies, bright minimal interior, soft daylight, professional service environment, no text, no letters, no watermark",
             "headline": "每条毛巾都消毒",
             "subheadline": "主人可以全程在旁边看",
             "bullets": ["一宠一巾", "无麻醉洗护", "老客有折"],
             "cta": "随时来店里"},
            {"name": "第一次体验",
             "idea": "降低新客门槛：第一次来便宜、无风险。",
             "visual_prompt": "small fluffy pet waiting on a bright pet salon counter, neatly folded pastel towels and grooming tools arranged beside, warm wood and mint green interior, cheerful bright lighting, clean and welcoming, no people, no staff, no text, no letters, no watermark",
             "headline": "第一次来半价",
             "subheadline": "新手主人也不用担心",
             "bullets": ["首次体验价", "可陪同", "当天预约"],
             "cta": "私信预约"},
        ],
        "copy": {
            "title": "🐾 洗完澡的毛孩子，香得想抱一整天",
            "body": "店里现在是一宠一巾、一客一消毒，主人可以全程在玻璃外面看着。第一次来的毛孩子会先让它熟悉一会儿环境再开始洗，不着急也不关笼子。洗完可以顺便剪个指甲。",
            "bullets": ["一宠一巾一消毒，卫生看得见", "不关笼子，主人可陪同", "首次体验有优惠"],
            "hashtags": ["#宠物洗护", "#铲屎官日常", "#萌宠", "#宠物店推荐", "#毛孩子", "#同城宠物"],
            "cta": "要洗护的直接私信我们排时间。",
        },
        "video_script": {
            "duration_s": 15,
            "shots": [
                {"t": "0-3s", "visual": "湿漉漉的毛孩子抬头看镜头", "voiceover": "又到该洗澡的日子了。", "onscreen": "毛孩子该洗澡啦"},
                {"t": "3-7s", "visual": "温水冲淋、起泡的慢动作", "voiceover": "温水、专门的洗剂，全程都很轻。", "onscreen": "温和洗护"},
                {"t": "7-11s", "visual": "吹干后变蓬松的对比画面", "voiceover": "吹干以后蓬松得认不出来。", "onscreen": "洗完像换了只狗"},
                {"t": "11-15s", "visual": "主人抱起毛孩子离开", "voiceover": "第一次来有体验价，直接私信就行。", "onscreen": "首次体验价"},
            ],
        },
        "publish_advice": [
            {"channel": "小红书", "best_time": "周三 / 周五 20:00", "format": "3:4 封面 + 洗澡前后对比图", "note": "对比图最关键，正文写清服务流程与价格区间"},
            {"channel": "朋友圈", "best_time": "周末 10:00", "format": "1:1 方图 + 顾客返图", "note": "用真实顾客返图建立信任"},
            {"channel": "抖音", "best_time": "周五 19:30", "format": "15 秒竖版，前后对比卡点", "note": "评论区常问价格，提前置顶一条"},
        ],
    },
}

GENERIC: Dict[str, Any] = {
    "palette": "治愈奶油",
    "font_style": "sans",
    "concepts": [
        {"name": "场景故事",
         "idea": "把这个店放进一个具体的日常场景里，让人产生「我也想去」的画面。",
         "visual_prompt": "inviting small local shop interior, warm lighting, wooden furniture, neatly arranged products on shelves, an empty seat by the window, cozy welcoming atmosphere, soft natural daylight, cinematic still-life photography, no people, no customers, no text, no letters, no watermark",
         "headline": "值得专门来一趟",
         "subheadline": "很多人在这个角落坐了很久",
         "bullets": ["环境舒服", "性价比高", "回头客多"],
         "cta": "来看看"},
        {"name": "细节品质",
         "idea": "用细节特写证明专业度，比喊口号可信。",
         "visual_prompt": "close up detail shot of carefully crafted product on a clean wooden surface, soft directional light, high detail texture, minimal composition, warm neutral tones, product photography, no text, no letters, no watermark",
         "headline": "细节都看得见",
         "subheadline": "每一份都现做现给",
         "bullets": ["现做现给", "用料实在", "干净整洁"],
         "cta": "试一次"},
        {"name": "活动号召",
         "idea": "直接给一个现在就来的理由。",
         "visual_prompt": "bright welcoming storefront counter with decorative seasonal arrangement, cheerful colorful accents, daylight, clean minimal composition, friendly local business, no text, no letters, no watermark",
         "headline": "本周限定活动",
         "subheadline": "错过这周就要等下个月",
         "bullets": ["限时优惠", "到店即享", "名额有限"],
         "cta": "现在就来"},
    ],
    "copy": {
        "title": "✨ 这周的活动，只做到周末",
        "body": "这段时间来店里的人明显多了，很多人是朋友带着来的。活动内容我们直接写在图上，不用问、不用等，来了就能享。位置不太好找，可以私信我们发定位。",
        "bullets": ["活动期间到店即享，不用等", "环境舒服，适合慢慢坐", "老客常来，回头率高"],
        "hashtags": ["#同城探店", "#周末去哪儿", "#小众好店", "#本地生活", "#打卡圣地", "#限时活动"],
        "cta": "私信我们，给你发具体位置。",
    },
    "video_script": {
        "duration_s": 15,
        "shots": [
            {"t": "0-3s", "visual": "门店门口推门而入的第一视角", "voiceover": "这家店在网上被推荐过很多次。", "onscreen": "被推荐很多次的店"},
            {"t": "3-7s", "visual": "产品/服务过程特写", "voiceover": "来了才知道为什么回头客多。", "onscreen": "回头客特别多"},
            {"t": "7-11s", "visual": "顾客满意的瞬间", "voiceover": "这段时间有活动，到店就能享。", "onscreen": "限时活动"},
            {"t": "11-15s", "visual": "店内整体环境收尾", "voiceover": "位置在评论区第一条。", "onscreen": "位置见评论区"},
        ],
    },
    "publish_advice": [
        {"channel": "小红书", "best_time": "周四 / 周五 20:00", "format": "3:4 封面 + 实拍环境图", "note": "标题写清活动与地址，正文带价格区间"},
        {"channel": "朋友圈", "best_time": "周末 11:00", "format": "1:1 方图 + 短文案", "note": "面向老客，突出限定与到店福利"},
        {"channel": "抖音", "best_time": "周末 19:00", "format": "15 秒竖版短视频", "note": "挂定位，前三秒给最有冲击力的画面"},
    ],
}


def _pick_pack(customer: str, category: str, theme: str) -> Dict[str, Any]:
    blob = f"{category}{customer}{theme}"
    if any(k in blob for k in ("拼豆", "手作", "DIY", "diy", "手工", "陶", "银饰", "画室")):
        return PACKS["手作"]
    if any(k in blob for k in ("咖啡", "茶", "奶茶", "饮品", "面包", "烘焙")):
        return PACKS["咖啡"]
    if any(k in blob for k in ("宠物", "猫", "狗", "洗护", "美宠")):
        return PACKS["宠物"]
    return GENERIC


# 规则兜底也要给语义断行：程序均分断词是最容易被一眼看出的"外行感"
HEADLINE_BREAKS = {
    "把喜欢拼进豆子": ["把喜欢", "拼进豆子"],
    "节日限定的手作": ["节日限定", "的手作"],
    "这些全都能自己做": ["这些全部", "能自己做"],
    "秋天第一杯": ["秋天第一杯"],
    "坐一下午也不赶": ["坐一下午", "也不赶"],
    "随手一拍就好看": ["随手一拍", "就好看"],
    "洗一次就变乖": ["洗一次", "就变乖"],
    "每条毛巾都消毒": ["每条毛巾", "都消毒"],
    "第一次来半价": ["第一次来", "半价"],
    "值得专门来一趟": ["值得专门", "来一趟"],
    "细节都看得见": ["细节全都", "看得见"],
    "本周限定活动": ["本周限定", "活动"],
}


def rule_based_brief(customer: str, analysis: str, strategy: str,
                     channels: str, theme_override: str = "") -> Dict[str, Any]:
    """构造与 LLM 输出同构的 brief，字段名完全一致，因此上层无需分支。"""
    import copy

    pack = copy.deepcopy(_pick_pack(customer, "", strategy))
    pack["theme"] = theme_override or (strategy.split("；")[0].replace("营销主题：", "") if strategy else "本季限定活动")
    if not pack["theme"]:
        pack["theme"] = "本季限定活动"
    for c in pack["concepts"]:
        c["headline_lines"] = HEADLINE_BREAKS.get(c.get("headline", ""), [c.get("headline", "")])
    out = {
        "theme": pack["theme"][:14],
        "palette": pack["palette"],
        "font_style": pack["font_style"],
        "concepts": pack["concepts"],
        "copy": pack["copy"],
        "video_script": pack["video_script"],
        "publish_advice": pack["publish_advice"],
        "_source": "rule_based",
    }
    return out
