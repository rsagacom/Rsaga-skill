#!/usr/bin/env python3
"""第二章 分镜脚本 — v0.3.0（40格）"""
Q="Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin"
S="Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."

PANELS=[
("P40_扉页",f"Split composition: {Q} silhouette clutching chest with glowing thread extending to distant figure collapsing, title space. {S}","abstract"),
# 单元1 倒计时
("P01_13点34",f"Close-up phone screen showing 13:34, {Q} face in background blur running from bridge, timestamp burning into retina, countdown begins. {S}","character"),
("P02_无头苍蝇",f"Wide shot {Q} walking aimlessly through city streets, pedestrians blurred around him, winter sunlight harsh and glaring, lost and hunted look. {S}","scene"),
("P03_审判的目光",f"Medium shot {Q} face, bright winter sun harsh on pale skin, every passing stranger glance feeling like judgment spotlight, paranoia visible. {S}","character"),
("P04_冲进医院",f"{Q} pushing through hospital entrance doors, urgent desperate movement, white hospital corridor stretching ahead, seeking easy targets. {S}","scene"),
("P05_ICU徘徊",f"{Q} in ICU corridor looking through glass at patients on life support, internal debate visible, moral justification forming, dim hospital lighting. {S}","character"),
# 单元2 医院放弃
("P06_老人的手",f"Close-up elderly patient hand gripping bedsheet, oxygen mask over face,枯瘦 fingers clutching fabric, will to live in gesture. {S}","character"),
("P07_求生的眼神",f"Extreme close-up elderly patient eyes behind oxygen mask, cloudy but fierce will to live, NO plea for death only fight for survival, {Q} reflection in pupil. {S}", "character"),
("P08_落荒而逃",f"{Q} running away down hospital corridor seen from behind, fleeing from impossible choice, white walls blurring past, escape from conscience. {S}","character"),
("P09_监狱高墙",f"Wide shot {Q} tiny figure before massive prison walls topped with razor wire, looking up, impenetrable barrier, can not see inside, gamble too large. {S}","scene"),
# 单元3 时间耗尽
("P10_时间跳变",f"Three-panel composition showing phone screen times: 16:02, 16:17, 16:43, {Q} on park bench in each, time running out visual countdown. {S}","character"),
("P11_不到一小时",f"Medium shot {Q} head in hands on park bench, posture of despair, phone beside him showing time, weight of deadline crushing down. {S}","character"),
("P12_沈骏",f"Close-up {Q} face lifting from despair, eyes widening as name surfaces from memory like lightning strike, sudden clarity cutting through panic, name floating visually. {S}","character"),
("P13_童年恶霸",f"Flashback: young boy being beaten in stairwell by older bigger boy, {Q} as small child cornered, fist raised, childhood trauma memory. {S}","character"),
# 单元4 沈骏的罪
("P14_沙坑",f"Flashback: young boy face pushed into sandpit, older boy laughing above, sand filling mouth, childhood humiliation, grainy memory texture. {S}","character"),
("P15_小光",f"Flashback: thin small boy with slight stutter smiling shyly at camera, innocent face, summer day by river, the boy who would not come home. {S}","character"),
("P16_芦苇荡",f"Wide shot riverside reed marsh at dusk, police flashlights cutting through darkness, small body shape barely visible among reeds, tragic discovery. NO children visible. {S}","scene"),
("P17_不了了之",f"Teen Shen Jun crying being led away by wealthy parents past grieving family, money changing hands暗示, justice denied, two families two fates. {S}","scene"),
("P18_妈妈的眼睛",f"Extreme close-up grieving mother eyes, completely hollow empty void, the look of someone who lost the only thing that mattered, nothing left to lose, haunting gaze. {S}","character"),
("P19_摆平过人命",f"Adult Shen Jun at dinner table boasting drunk, surrounded by drinking buddies, bragging about past, arrogant face, words hanging in air. {S}","scene"),
# 单元5 选择
("P20_填坑",f"Close-up {Q} face, expression hardening from hesitation to cold resolve, eyes changing, first step into darkness, moral line crossed internally. {S}","character"),
("P21_我选好了",f"{Q} eyes closed, head slightly bowed as if in prayer to dark god, lips forming words, reaching out mentally to the presence, summoning the landlord. {S}","character"),
("P22_杀戮的欲望",f"Darkness with glowing cyan eye sockets appearing, shadowy form amused, NO facial features NO mouth NO nose, predator pleased with prey compliance. {S}","supernatural"),
("P23_勾勒轮廓",f"Close-up {Q} eyes closed, faint outline of Shen Jun face forming in mind space above, mental image sharpening into target lock, visual of psychic targeting. {S}","character"),
("P24_不需要解释",f"Shadowy form with cyan eyes, cold command energy radiating, invisible force beginning to separate from {Q} body like剥离, power being unleashed. {S}","supernatural"),
("P25_寒意剥离",f"Abstract composition: translucent cold force separating from {Q} chest area, something being pulled out leaving lightness behind, visible departing energy. {S}","abstract"),
# 单元6 第一次裁决
("P26_看不见的风",f"Abstract: invisible wind visualized as flowing energy streaming across city skyline toward southwest, predator missile in flight, death en route. {S}","abstract"),
("P27_丝线断了",f"{Q} chest area with glowing thread extending to distant horizon, thread suddenly snapping clean, connection severed, the moment of kill confirmation. {S}","abstract"),
("P28_物流园",f"Shen Jun at logistics park loading cargo, cigarette dangling from lips, casual worker pose, unaware target, last moments of normal life. {S}","scene"),
("P29_心脏捏爆",f"Abstract composition: dark invisible hand gripping human heart, squeezing crushing, second squeeze harder, heart bursting, {Q} distant blurred. {S}","abstract"),
("P30_烟头滚落",f"Shen Jun collapsing face-down on concrete warehouse floor, cigarette rolling away still trailing smoke, body motionless, sudden death aftermath. {S}","scene"),
("P31_急救车",f"Wide shot logistics park, ambulance lights flashing in distance, small crowd gathered around body on ground, official cause: sudden cardiac death. {S}","scene"),
("P32_第一次裁决",f"Split composition: left side {Q} on park bench staring at hands, right side Shen Jun body being covered, connected by fading invisible thread, first kill complete. {S}","abstract"),
# 单元7 代价与奖赏
("P33_骨髓的冷",f"Close-up {Q} hands held before face, clean normal hands but somehow stained, no trembling no nausea, just bone-deep cold that will never warm. simple normal hand shape five fingers. {S}","hand"),
("P34_短信来了",f"Phone screen close-up showing new message notification, light illuminating {Q} face in darkness, unexpected chime breaking silence. {S}","scene"),
("P35_恭喜通过",f"Phone screen showing interview invitation message text, {Q} face partially lit by screen glow, expression complex mix of relief and horror at the transaction. {S}","character"),
("P36_看了很久",f"Medium shot {Q} sitting alone on bench under streetlamp, phone held before him, staring at screen for long moment, darkness closing in around, park empty. {S}","character"),
("P37_走向地铁",f"{Q} standing up pocketing phone, walking toward subway entrance, back to viewer, streetlamp light behind, descending into underground. {S}","scene"),
("P38_天全黑了",f"Wide shot city at night, subway entrance glowing, {Q} tiny figure disappearing down stairs, sky completely black above, first chapter of new life closing. {S}","scene"),
("P39_终幕",f"{Q} in subway car, reflection in window glass, eyes in reflection slightly changed from before, something different behind them, cost of power beginning to show. {S}","character"),
]
