#!/usr/bin/env python3
"""第一章 分镜脚本 — v0.3.0（50格）"""
Q = "Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin"
W = "Woman with very long straight black hair flowing down past waist, dark trench coat, seen from behind only, NO face visible"
S = "Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."

PANELS = [
    # === 单元1：腐烂的日常 ===
    ("P50_扉页", f"Split composition: left side {Q} silhouette among CBD crowds and subway, right side dark bridge cave with glowing carved runes and shadowy form, title text space at top. {S}", "abstract"),
    ("P01_扮演正常人", f"Medium shot {Q} facing bathroom mirror adjusting collar, vacant hollow eyes staring at own reflection, dim morning light through small window, melancholic. {S}", "character"),
    ("P02_挤地铁", f"Wide shot crowded subway car interior, {Q} pressed among anonymous commuters, blank expressionless face, hand gripping overhead rail, morning commute atmosphere. {S}", "scene"),
    ("P03_旧电脑", f"Close-up inside old black backpack, ancient laptop with dead battery beside charger cable, worn fabric interior, symbol of fake productivity. {S}", "scene"),
    ("P04_CBD大堂", f"Wide shot {Q} sitting alone on lobby sofa in glass office building, suited workers passing by blurred, security guard in background, invisible man among the employed. {S}", "scene"),
    ("P05_饭团", f"Medium shot {Q} sitting on park bench eating convenience store rice ball, vacant stare into distance, afternoon light filtering through trees, lonely lunch break. {S}", "character"),
    ("P06_腐烂", f"Close-up {Q} face, hollow tired eyes with dark circles, pale skin under cold light, expression of quiet internal decay, dramatic shadow on half face. {S}", "character"),
    ("P07_厌倦", f"{Q} in library at desk, phone screen showing unread job application messages, laptop open, hands pushing away from table, moment of giving up, afternoon weariness. {S}", "character"),
    ("P08_老街区", f"Wide shot {Q} walking into abandoned demolition zone at sunset, rubble and exposed rebar everywhere, rust-red sunset light, figure small against desolation. NO people. {S}", "scene"),

    # === 单元2：荒废公园 ===
    ("P09_铁门", f"Narrow passage between two crumbling walls, rusty iron gate half-open, {Q} small figure approaching, sunset backlight creating dramatic silhouette, invitation or warning. {S}", "scene"),
    ("P10_荒废公园", f"Abandoned playground, crooked children slide tilted sideways with black stagnant water pooled in plastic chute, weeds growing through cracked stone path, desolate atmosphere. NO children. {S}", "scene"),
    ("P11_石桥", f"Wide shot stone bridge spanning dry riverbed deep in abandoned park, bridge arch shadow darker than surrounding dusk, like open mouth yawning, {Q} tiny figure before it. {S}", "scene"),
    ("P12_桥洞阴影", f"Extreme low angle shot of bridge cave entrance, absolute black void within, rim of arch catching last sunset light, visual of doorway into nothing. {S}", "abstract"),
    ("P13_女人背影", f"{W} walking into bridge cave darkness from behind, long black hair flowing past waist, dark trench coat silhouette, disappearing into shadow, {Q} in far background watching. NO other person, woman only. {S}", "supernatural"),
    ("P14_跟上去", f"{Q} stepping toward bridge cave entrance seen from behind, shoulders tense, one foot already in shadow, drawn by inexplicable pull, thin figure against massive darkness. {S}", "character"),

    # === 单元3：桥洞里的异常 ===
    ("P15_看不透的暗", f"Inside bridge cave, near total darkness, only faint light from both distant exits, {Q} barely visible silhouette in center, oppressive impenetrable black. {S}", "abstract"),
    ("P16_硫磺味", f"Close-up {Q} face in darkness, nose wrinkling at acrid smell, hand raised near mouth, burning wire and sulfur stench, discomfort and growing fear. {S}", "character"),
    ("P17_她不在", f"{Q} looking around empty bridge cave interior, turning head searching, darkness pressing in from all sides, woman nowhere to be seen, confusion mounting. {S}", "character"),
    ("P18_两束光", f"{Q} standing frozen at center of bridge cave, light from both exits visible as distant rectangles, paralyzed between two choices, unable to move forward or back. {S}", "character"),
    ("P19_刻痕", f"Close-up bridge cave inner wall covered in dense carved markings, not cut but grown from within stone like veins and roots, intricate intertwined pattern covering entire surface. {S}", "abstract"),
    ("P20_发光搏动", f"Extreme close-up glowing rune carvings on wall, faint phosphorescent light pulsing rhythmically like heartbeat, dim glow illuminating stone texture, alive and breathing. {S}", "supernatural"),
    ("P21_想跑", f"Full shot {Q} body frozen in terror, legs rigid unmoving, brain screaming run but body refusing, sweat on forehead, classic paralysis of mortal fear. {S}", "character"),

    # === 单元4：那个声音 ===
    ("P22_颅骨内炸响", f"Close-up {Q} head, visual sound waves emanating from inside skull outward, eyes wide with shock, hands rising toward temples, voice appearing inside brain not through ears. {S}", "supernatural"),
    ("P23_撞上墙壁", f"{Q} stumbling backward slamming against cave wall, glowing runes inches from his back, arms out for balance, terror on face, trapped between voice and wall. {S}", "character"),
    ("P24_蚂蚁的惨叫", f"Wide shot {Q} small figure surrounded by encroaching darkness, voice represented as shadowy tendrils closing in from all directions, overwhelming presence. {S}", "supernatural"),
    ("P25_你是谁", f"Close-up {Q} face, cold sweat soaking through shirt collar visible, lips trembling forming words, tears welling in eyes, voice barely a whisper, desperate question. {S}", "character"),
    ("P26_租客", f"Darkness parting to reveal shadowy humanoid form, NO facial features, NO mouth NO nose, ONLY empty glowing cyan eye sockets, semi-transparent shadowy body, edges dissolving into smoke. {S}", "supernatural"),
    ("P27_什么应聘", f"Close-up {Q} face, confusion breaking through terror, eyebrows furrowed, mouth slightly open, unexpected question cutting through fear, momentary bewilderment. {S}", "character"),
    ("P28_容器", f"Extreme close-up {Q} eyes, reflection of darkness in dilated pupils, the word piercing like blunt knife, expression of being seen through and labeled, deep wound. {S}", "character"),
    ("P29_一个机会", f"Medium shot {Q} and shadowy form in darkness, cyan eye sockets glowing, {Q} listening with mixture of fear and desperate hope, predator offering salvation to prey. {S}", "supernatural"),

    # === 单元5：交易 ===
    ("P30_杀人", f"Close-up {Q} face in shock, the word hitting like physical blow, pupils constricted to pinpoints, mouth open in disbelief, world tilting. {S}", "character"),
    ("P31_我不", f"{Q} shaking head in denial, hands raised palm out warding off the implication, eyes wide with refusal, ordinary man confronted with unthinkable demand. {S}", "character"),
    ("P32_只需选择", f"Abstract composition, blurred human silhouette in distance with red targeting marker appearing over heart area, {Q} in foreground watching, supernatural sniper scope visualization. {S}", "abstract"),
    ("P33_比正常人更强", f"Close-up {Q} face shifting, fear slowly mixing with something darker, faint glimmer of temptation in tired eyes, the offer sinking in, internal war beginning. {S}", "character"),
    ("P34_给花浇水", f"Abstract composition, wilting flower in one hand transforming to dead plant, dark metaphor of regular sacrifice, ominous visual of what happens when you stop feeding. {S}", "abstract"),
    ("P35_左臂剧痛", f"Extreme close-up {Q} left forearm, skin turning grayish-black like burnt tree bark, flesh visibly decaying in real time, agony on partially visible face. {S}", "character"),
    ("P36_黑色蔓延", f"Close-up tracking shot along arm, black necrosis line climbing upward following vein paths, muscle withering beneath skin, life being drained, body dying before eyes. {S}", "character"),
    ("P37_我没有时间", f"Medium shot {Q} collapsed against cave wall, black necrosis reaching elbow, face deathly pale, shadowy form looming above with cold cyan eyes, ultimatum delivered. {S}", "supernatural"),
    ("P38_你要没了", f"Extreme close-up {Q} face, eyes half-closed, skin gray, lips blue, consciousness fading, the moment of realizing this is real death approaching, pure primal terror. {S}", "character"),

    # === 单元6：屈服 ===
    ("P39_我答应", f"{Q} head thrown back screaming toward ceiling, mouth wide open in desperate cry, tears streaming, veins visible on neck, total surrender of human will. {S}", "character"),
    ("P40_黑色退去", f"Close-up {Q} left arm, black necrosis receding like tide pulling back, gray skin returning to normal color, blood flowing again, life returning in reverse. {S}", "character"),
    ("P41_手指能动", f"Extreme close-up {Q} left hand, fingers slowly curling and uncurling, testing movement, simple normal hand shape five fingers, miracle of restored life. {S}", "hand"),
    ("P42_瘫软", f"Wide shot {Q} collapsed on cave floor, tears and mucus on face, body limp in total exhaustion, shadowy form watching from darkness above, broken survivor. {S}", "character"),
    ("P43_四小时", f"Darkness shot with two glowing cyan eye sockets filling frame, overwhelming presence, NO other features, pure supernatural menace delivering deadline, every cell watched. {S}", "supernatural"),
    ("P44_冲出桥洞", f"Wide shot {Q} running out of bridge cave, sunset now deep orange, silhouette running toward light, escape from darkness, desperate flight. {S}", "scene"),

    # === 单元7：逃脱与余波 ===
    ("P45_狂奔", f"Dynamic action shot {Q} running through abandoned park, weeds and rubble blurring past, arms pumping, face desperate, fleeing through three environments blurring together. {S}", "scene"),
    ("P46_跪倒", f"Medium shot {Q} collapsing to knees beneath unlit streetlamp, gasping for breath, hands on ground, suburban street empty around him, dusk deepening. {S}", "character"),
    ("P47_干干净净", f"Extreme close-up {Q} left hand held up before face, palm open fingers spread, completely normal clean skin, no trace of what happened, simple normal hand shape. {S}", "hand"),
    ("P48_不一样了", f"Medium shot {Q} kneeling under streetlamp, lamp suddenly flickering on casting harsh light, face half illuminated half in deep shadow, realization dawning, point of no return. {S}", "character"),
    ("P49_终幕", f"Wide shot {Q} standing up under streetlamp at night, back to viewer facing dark city ahead, single long shadow stretching behind, four hours counting down, loaded gun at his waist only he can see. NO people. {S}", "abstract"),
]
