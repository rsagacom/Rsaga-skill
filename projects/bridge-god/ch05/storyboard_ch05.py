#!/usr/bin/env python3
"""第五章 分镜脚本 — v0.3.0（45格）"""
Q="Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, tired hollow eyes with dark circles, pale skin"
S="Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."

PANELS=[
("P45_扉页",f"Split composition: {Q} at office desk normal left side, right side shadowy figure flickering between human and circuit-board horror. title text. {S}","abstract"),
# 单元1 新生活
("P01_奥信商贸",f"Exterior shot small trading company building, ordinary office block, {Q} entering through front door, new beginning. {S}","scene"),
("P02_整理单据",f"{Q} at desk in operations department, stacks of shipping documents, computer screen showing logistics software, boring but cherished routine work. {S}","character"),
("P03_周姐",f"Medium shot female supervisor in forties, sharp capable demeanor, speaking to {Q}, professional but caring, office mentor figure. {S}","scene"),
("P04_林小燕",f"Young female coworker fresh from college at adjacent desk, smiling warmly, offering snack to {Q}, cheerful collegial atmosphere. {S}","scene"),
("P05_扮演正常人",f"{Q} at office茶水间 chatting with colleagues, holding coffee cup, practiced smile, learning to complain about weather, fitting in performance. {S}","character"),
# 单元2 加班之夜
("P06_加班",f"Wide shot office at night, overtime work, desks mostly empty, {Q} at workstation under fluorescent light, clock showing nearly 10pm. {S}","scene"),
("P07_只剩下两人",f"Office wide shot, {Q} packing up, only one other person visible in far corner, new intern barely noticeable, almost invisible. {S}","character"),
("P08_安静的实习生",f"Corner desk with male intern, head lowered, long bangs covering most of face, very quiet, arrived two weeks ago but barely spoken, ghostlike presence. {S}","character"),
("P09_还不走吗",f"{Q} passing by intern desk with coffee cup, casual question, last train warning, friendly senior gesture, about to leave. {S}","character"),
("P10_没有抬头",f"Close-up intern at desk, not responding, fingers typing on keyboard softly, focused on screen, ignoring {Q}, silence in empty office. {S}","scene"),
# 单元3 异常
("P11_滋滋",f"Close-up {Q} ear area, electrical interference sound visual waves emanating, ozone crackle sensation, something wrong with reality. {S}","character"),
("P12_凑近一步",f"{Q} stepping closer to intern desk, coffee cup in hand, confusion on face, trying to identify strange sound source. {S}","character"),
("P13_血液凝固",f"Extreme close-up {Q} face, eyes widening in horror, blood draining from face, seeing something impossible, pure primal fear. {S}","character"),
("P14_闪烁",f"Intern body flickering like bad TV signal, edges blurring becoming indistinct, reality glitching, human shell momentarily transparent revealing what lies beneath. {S}","supernatural"),
("P15_电路板",f"Close-up intern torso during flicker: beneath skin, black burnt circuit board structure visible, dense lines, twisted runes, same carvings as bridge cave walls,活着的刻痕. {S}","supernatural"),
("P16_硫磺味",f"{Q} nostrils flaring, familiar acrid sulfur stench filling office air, same smell from bridge cave, past and present merging, nightmare returning. {S}","character"),
# 单元4 那张脸
("P17_你在看哪里",f"Intern looking up at {Q}, addressing him as senior, casual polite tone, but something terribly wrong beneath normal surface. {S}","character"),
("P18_抬起头",f"Slow reveal: intern face lifting, bangs parting, revealing deathly pale skin, bloodless white, unnatural pallor. {S}","supernatural"),
("P19_漆黑深渊",f"Extreme close-up intern eyes: pupils completely black voids, no whites no irises, only two bottomless black holes staring, abyss looking back. {S}","supernatural"),
("P20_咖啡洒了",f"{Q} hand dropping coffee cup, liquid splashing on floor, chair knocked over behind him, stumbling backward against wall, gasping for breath in terror. {S}","character"),
("P21_再看",f"{Q} blinking hard rubbing eyes, looking again, hoping it was hallucination, desperate for normal. {S}","character"),
("P22_恢复正常",f"Intern face now completely normal, black and white eyes, confused concerned expression,刘海遮半张脸, exactly like before, as if nothing happened. {S}","scene"),
# 单元5 不是幻觉
("P23_你没事吧",f"Intern standing walking toward {Q}, concerned colleague, suggesting rest, normal human warmth, hand reaching out to help. {S}","scene"),
("P24_体温是温的",f"Close-up intern hand gripping {Q} arm, warm human temperature, real physical contact, {Q} frozen in place, every sense screaming wrongness. {S}","character"),
("P25_低血糖",f"Close-up {Q} face, voice hoarse like sandpaper, lying about low blood sugar, forced composure, survival instinct kicking in, play along. {S}","character"),
("P26_倒杯热水",f"Intern walking away toward茶水间, back view, normal walking posture, everything normal, {Q} watching, knowing what he saw was real. {S}","scene"),
("P27_不是幻觉",f"Close-up {Q} face, cold certainty in eyes, what he saw was not hallucination, something is very wrong with this intern, investigation must begin. {S}","character"),
# 单元6 不存在的人
("P28_翻通讯录",f"{Q} at desk early next morning, flipping through HR directory, finding intern name: Zhang Mingyuan, writing it down, detective mode. {S}","character"),
("P29_问林小燕",f"{Q} asking young female coworker casually about Zhang Mingyuan, which school graduated from, conversational cover, searching for confirmation. {S}","scene"),
("P30_谁",f"Close-up coworker face, confused blank expression, who, not recognizing the name at all, {Q} pointing toward corner desk. {S}","character"),
("P31_那里有人吗",f"Wide shot: {Q} pointing at corner desk, coworker looking same direction, corner completely empty to her, she sees nothing, {Q} heart sinking. {S}","scene"),
("P32_问周姐",f"{Q} asking supervisor during lunch, woman checking phone, frowning, no Zhang Mingyuan in operations department, did you misremember. {S}","scene"),
("P33_人事部",f"{Q} at HR department, clerk flipping through roster, looking up with strange examining gaze, no male interns hired recently, are you under too much pressure. {S}","scene"),
("P34_可能记错了",f"Close-up {Q} face, lying again, saying maybe remembered wrong, but internal certainty growing: he exists but nobody else can see him. {S}","character"),
# 单元7 浮现
("P35_空无一人的角落",f"Wide shot office, {Q} at desk staring at empty corner, nothing there, ordinary office furniture, empty chair. {S}","scene"),
("P36_第三秒",f"Time-lapse: corner empty at second 1 and 2, then at second 3 something materializing from air, visual of manifestation beginning. {S}","supernatural"),
("P37_一点一点浮现",f"Close-up corner: outline forming first, then hair, then gray hoodie never changed, Zhang Mingyuan materializing from nothing, sitting at computer, typing, as if always there. {S}","supernatural"),
("P38_微微一笑",f"Close-up Zhang Mingyuan face turning toward {Q}, slight smile, knowing smile, smile that says I know you see me and I know nobody else does. {S}","supernatural"),
("P39_攥紧鼠标",f"Extreme close-up {Q} hand on mouse, fingers trembling uncontrollably, squeezing hard to suppress shaking, veins visible, simple normal hand shape five fingers. {S}","hand"),
("P40_低下头",f"{Q} lowering head staring at shipping documents, refusing to look up, pretending to work, survival tactic, do not engage do not acknowledge. {S}","character"),
# 终幕
("P41_终幕",f"Wide shot office, {Q} small figure at desk looking down, Zhang Mingyuan in corner watching him, predator and prey in ordinary office under fluorescent lights,被什么东西盯上了. {S}","abstract"),
]
