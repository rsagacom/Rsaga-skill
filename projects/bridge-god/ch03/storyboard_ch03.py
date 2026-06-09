#!/usr/bin/env python3
"""第三章 分镜脚本 — v0.3.0（48格）"""
Q="Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin"
S="Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."

PANELS=[
("P48_扉页",f"Split composition: left {Q} as normal office worker, right shadowy executioner figure with red targeting markers, central gavel of justice. title space. {S}","abstract"),
# 单元1 恐惧麻木习惯
("P01_三次",f"Three-panel vertical strip: first panel {Q} terrified face, second panel numb empty face, third panel cold habitual face, progression of deadening. {S}","character"),
("P02_暴君",f"Abstract: shadowy form with cyan eyes growing larger more demanding, {Q} shrinking before it, appetite increasing visualized as expanding darkness. {S}","supernatural"),
("P03_一周三天两天",f"Visual timeline: calendar pages flipping, interval markers showing weekly then every three days then two per day, accelerating demand spiral. {S}","abstract"),
("P04_餍足的猫",f"Abstract composition: shadowy form curled like satisfied cat in corner of {Q} mind space, purring vibration visual, predator content after feeding. {S}","supernatural"),
("P05_不再噩梦",f"Close-up {Q} face now, calm but wrong kind of calm, eyes no longer haunted by victims faces, faces he can not remember anymore. {S}","character"),
("P06_锁定确认丝线",f"Abstract: three-step sequence visual: target lock reticle, confirmation checkmark, thread snapping, {Q} satisfaction at button pressed error cleared. {S}","abstract"),
# 单元2 寻找目标
("P07_寻找目标",f"{Q} hunched over computer in dark room, multiple screens showing news articles, forum posts, hunting for prey among headlines, vigilante research. {S}","character"),
("P08_证据确凿的恶人",f"Montage of newspaper headlines: rapist, animal abuser, scammer, drug trafficker, {Q} eyes scanning selecting, self-justification forming. {S}","scene"),
("P09_迟来的正义",f"Close-up {Q} face, cold righteous expression, self-narrative visible: not killer, executioner, not murder, justice, this unfair world finally balanced. {S}","character"),
("P10_就是这两个",f"{Q} sitting at convenience store window eating rice ball, phone showing news of two juvenile offenders, blurred faces but visible cruelty, target acquired. {S}","character"),
("P11_锁定他们",f"Close-up {Q} eyes, pupils focusing, mental targeting, two faces forming in mind space with red markers appearing, lock-on achieved. {S}","character"),
# 单元3 行刑
("P12_眼光越来越好",f"Darkness with cyan eye sockets, amused tone, {Q} in foreground, landlord praising student progress, sinister mentor approval. {S}","supernatural"),
("P13_走得慢一点",f"Shadowy form radiating malice, the word reward twisted into punishment延伸, deliberate cruelty as gift, {Q} face reacting with cold acceptance. {S}","supernatural"),
("P14_寒意离体",f"Abstract: cold force separating from {Q} body like剥离, visible departing energy streaming toward distant horizon, death sent. {S}","abstract"),
("P15_少管所",f"Two juvenile offenders in detention facility, suddenly clutching chests simultaneously, faces turning purple, collapsing to concrete floor, hands clawing at clothing and skin. {S}","scene"),
("P16_三分钟",f"Visual countdown: two boys writhing on ground, time markers 1min 2min 3min, slow agonizing death prolonged deliberately, lungs filling like drowning. {S}","scene"),
("P17_法医报告",f"Close-up coroner report document: cardiac sudden death, unexplained violent onset, official stamp, truth hidden in medical terminology, no one knows. {S}","scene"),
# 单元4 半年两百命
("P18_半年两百",f"Abstract: calendar pages flipping rapidly, each page with number tally climbing, 230 lives counted, {Q} face aging hardening across panels. {S}","abstract"),
("P19_城中村",f"{Q} in cramped rental room in urban village, multiple monitors glowing, walls covered with notes and news clippings, war room of vigilante, no longer job hunting. {S}","character"),
("P20_暗网幽灵",f"{Q} face lit only by computer screen glow in dark room, fingers on keyboard, anonymous forum posts on screen, digital ghost hunting criminals. {S}","character"),
("P21_论坛帖子",f"Close-up computer screen showing forum post: recently another bastard died mysteriously did you hear, comments cheering below, unknown executioner celebrated anonymously. {S}","scene"),
# 单元5 房东消失
("P22_12月上旬",f"Calendar showing early December, {Q} waking up in bed, morning light through window, something different about this morning, absence felt before understood. {S}","character"),
("P23_呼唤房东",f"{Q} eyes closed calling mentally, reaching out to familiar presence, but only silence echoes back, empty mind space where压迫 once lived. {S}","character"),
("P24_一片死寂",f"Abstract: vast empty mind space, where shadowy form once occupied now completely vacant, eerie silence, {Q} tiny figure in emptiness, abandoned. {S}","abstract"),
("P25_试着使用能力",f"{Q} staring at news article about criminal on screen, concentrating, reaching for power, but nothing happens, no thread extends, ability gone. {S}","character"),
("P26_等了一天两天一周",f"Calendar pages showing days passing: day1 day2 day7, {Q} waiting in various poses, increasing uncertainty, the voice never returns. {S}","character"),
# 单元6 回归日常
("P27_恐慌变庆幸",f"Three faces of {Q} in sequence: panic, confusion, then relief, emotion journey from abandoned to freed, maybe it is really gone. {S}","character"),
("P28_重新投简历",f"{Q} at computer writing job applications again, resume document open, half-year gap difficult to explain, starting over, small hope. {S}","character"),
("P29_入职小公司",f"{Q} in small trading company office, basic operations desk, low salary but enough,地铁 commute, conference room meetings, pretending to be normal again. {S}","scene"),
("P30_挤地铁赔笑脸",f"Three small panels: {Q} squeezed in subway, smiling nodding in meeting, standing eating lunch at convenience store, normal life mosaic. {S}","character"),
("P31_回来了",f"Close-up {Q} face, telling himself everything is back, self-deception visible, trying to believe his own lie, fragile normalcy. {S}","character"),
# 单元7 便利店新声音
("P32_下班路过",f"Wide shot {Q} walking toward convenience store at dusk, ordinary evening scene, glass door ahead,最后一刻的平静 before everything changes. {S}","scene"),
("P33_推开门",f"{Q} hand pushing convenience store glass door, half in half out, threshold moment, chime of door bell, universe about to shift. {S}","scene"),
("P34_喂",f"Extreme close-up {Q} ear area, sound wave visual drilling into ear from behind, sudden intrusion, word hanging in air, new voice not the landlord. {S}","character"),
("P35_少女的声音",f"Abstract: visual representation of girlish voice,清脆 tired, like someone who just ran 800 meters, completely different from metallic landlord voice, unexpected. {S}","abstract"),
("P36_购物篮差点掉",f"{Q} hand jerking, shopping basket nearly dropping, sudden shock, turning head sharply,身后 only shelves and checkout clerk on phone, no one there. {S}","character"),
("P37_别找了",f"{Q} searching confused, invisible voice speaking from nowhere, checkout clerk oblivious in background, supernatural intrusion into mundane convenience store. {S}","character"),
("P38_他死了",f"Abstract: dark landlord form crumbling dissolving into nothing, defeated in higher dimension battle, complete annihilation visualized, {Q} barely visible witnessing. {S}","supernatural"),
("P39_回路没拆",f"Close-up {Q} head, brain area glowing with residual circuit patterns left behind, landlord gone but wiring remains,私有财产 and time bomb simultaneously. {S}","abstract"),
("P40_什么意思",f"Close-up {Q} face, confusion and dawning fear, trying to process implications, the gift that keeps taking. {S}","character"),
# 单元8 警告
("P41_别再用能力",f"{Q} frozen in convenience store aisle, invisible girl voice delivering警告, serious urgent tone, last chance warning before irreversible consequence. {S}","character"),
("P42_夹缝",f"Abstract: {Q} body suspended between two worlds, half in reality half in void, frozen motionless, eyes open breathing but forever stuck,活雕塑 horror visualized. {S}","abstract"),
("P43_寒意升起",f"Close-up {Q} back, chill rising along spine visible as cold aura, goosebumps, the weight of the warning sinking in, cold fear. {S}","character"),
("P44_你是谁",f"{Q} asking into empty air, desperate question, who is this mysterious helper, why does she know everything, convenience store荧光灯 buzzing overhead. {S}","character"),
("P45_好心的路人",f"Convenience store wide shot, {Q} standing alone among shelves, invisible presence somewhere, ordinary fluorescent lighting, voice saying final words: forget those six months, be ordinary, only path to survival. {S}","scene"),
# 单元9 一切都变了
("P46_信号切断",f"Close-up {Q} face, sudden silence, connection severed, alone again in convenience store, fluorescent light humming, world rushing back in. {S}","character"),
("P47_攥着购物篮",f"Medium shot {Q} standing frozen before shelves, hand gripping shopping basket containing bento and water bottle, checkout clerk still on phone,门外下班人流车流, everything looks the same. {S}","scene"),
("P48_一切都变了",f"Close-up {Q} face, same convenience store same products same lighting, but eyes show knowledge that nothing is the same, everything has changed, fragile ordinary life shattered again. {S}","character"),
]
