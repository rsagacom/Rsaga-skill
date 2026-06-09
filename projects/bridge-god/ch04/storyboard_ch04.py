#!/usr/bin/env python3
"""《桥底的溃烂神明》第四章 分镜脚本 — v0.3.0 标准（39格）"""

# 角色描述（精简 <60 词）
Q = (
    "Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs "
    "covering forehead, beige casual blazer, dark blue V-neck shirt, old black backpack, "
    "tired hollow eyes with dark circles, pale skin"
)
W = "Woman with very long straight black hair flowing down past waist, dark trench coat, seen from behind only, NO face visible"
STYLE = "Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."

PANELS = [
    # === 情绪单元1：回归的困境 ===
    ("P01_回归困境",
     f"Medium shot {Q} sitting alone at small dark apartment desk, vacant hollow eyes staring at nothing, "
     f"single dim overhead cold light, deep shadows on pale face, melancholic exhausted atmosphere. {STYLE}",
     "character"),

    ("P02_别的东西",
     f"Close-up computer screen showing job offer email, {Q} hand resting on mouse in foreground blur, "
     f"empty instant noodle cup beside keyboard, dark room, sense of unease. {STYLE}",
     "scene"),

    ("P03_他的眼睛",
     f"Extreme close-up {Q} eyes, pupils slightly constricted, tired dark circles underneath, "
     f"cold blue light reflecting in glasses lens, ominous tension, half face in deep shadow. {STYLE}",
     "character"),

    # === 情绪单元2：短暂的正常 ===
    ("P04_转正通知",
     f"{Q} standing on sidewalk at dusk, phone in hand screen glowing with notification, "
     f"faint bittersweet half-smile on tired face, blurred city lights behind, gentle spring evening. {STYLE}",
     "character"),

    ("P05_走进火锅店",
     f"{Q} pushing open glass door of hot pot restaurant seen from behind, "
     f"warm golden light and thick steam pouring out from inside, silhouette framing against bright doorway, "
     f"step toward warmth. NO people visible inside. {STYLE}",
     "scene"),

    ("P06_热气腾腾",
     "Wide shot interior hot pot restaurant, multiple tables with steam rising from boiling pots, "
     "warm red and yellow ambient lighting, bustling lively atmosphere, "
     "chopsticks and plates on tables, comfort food vibe. NO people, NO crowd. " + STYLE,
     "scene"),

    ("P07_像个正常人",
     f"{Q} sitting alone at window table, chopsticks picking up meat from boiling hot pot, "
     f"thin wisps of steam, fleeting moment of peace on exhausted face, "
     f"warm golden glow illuminating pale skin, self-deceptive comfort. {STYLE}",
     "character"),

    # === 情绪单元3：冲突爆发 ===
    ("P08_邻桌吵起",
     f"Medium shot {Q} turning head sharply toward off-frame left, chopsticks frozen mid-air, "
     f"expression shifting from calm to alert tension, warm restaurant background blurred. {STYLE}",
     "character"),

    ("P09_揪头发",
     "Drunk unshaven middle-aged man grabbing young female waitress by her hair, "
     "waitress bent over in pain wearing restaurant apron, man red-faced aggressive expression, "
     "hand yanking hair forcefully, violent composition. " + STYLE,
     "scene"),

    ("P10_不到二十岁",
     "Close-up young waitress face, teenage features under twenty, tears streaming down cheeks, "
     "hair being pulled from above out of frame, pain and fear distorting innocent face, "
     "apron strap visible on shoulder. " + STYLE,
     "character"),

    ("P11_扬起手",
     "Extreme close-up drunk man face from low angle, mouth wide open spitting curses, "
     "arm raised high about to strike downward, veins bulging on neck, "
     "aggressive violent rage, dynamic threatening composition. " + STYLE,
     "scene"),

    ("P12_没人上前",
     "Wide shot restaurant interior, multiple diners at tables turning heads toward commotion, "
     "several holding phones up recording, some whispering to each other, "
     "but ALL remaining seated frozen, collective bystander effect. NO children. " + STYLE,
     "scene"),

    # === 情绪单元4：世界褪色 ===
    ("P13_筷子停半空",
     "Extreme close-up chopsticks suspended frozen mid-air above boiling hot pot, "
     "steam rising from red bubbling broth, chopsticks sharp in focus, "
     "everything else in deep blur, moment of suspended time. " + STYLE,
     "abstract"),

    ("P14_色彩褪去",
     f"Close-up {Q} face in profile, eyes wide with shock, "
     f"surrounding restaurant scene transitioning from warm colors to grayscale pencil-line sketch, "
     f"color draining like water from his side of frame, fear dawning. {STYLE}",
     "abstract"),

    ("P15_灰白世界",
     "Wide shot same hot pot restaurant but entire scene rendered in grayscale monochrome, "
     "all diners and tables in shades of gray, steam rising in white wisps, "
     "everything desaturated lifeless, surreal uncanny atmosphere. NO children. " + STYLE,
     "abstract"),

    ("P16_红光标记",
     "Grayscale world wide shot, but single drunk man glowing with faint red aura halo, "
     "red light pulsing like targeting reticle marker, "
     "everything else flat gray monochrome, only red glow stands out ominously. " + STYLE,
     "supernatural"),

    ("P17_最熟悉的光",
     f"Close-up {Q} face, red glow from off-frame reflecting in thin black-frame glasses lenses, "
     f"pupils constricted to pinpoints, expression torn between terror and dark hunger, "
     f"dramatic chiaroscuro lighting carving pale skin, sweat on forehead. {STYLE}",
     "character"),

    # === 情绪单元5：杀戮冲动 ===
    ("P18_骨髓饥渴",
     f"Abstract symbolic composition, {Q} clutching chest with both hands, "
     f"dark shadowy energy tendrils erupting from within body outward, "
     f"swirling black smoke-like hunger manifesting, expression of internal pain. dark supernatural. {STYLE}",
     "abstract"),

    ("P19_右手颤抖",
     f"Extreme close-up {Q} right hand trembling, fingers half-curled twitching, "
     f"simple normal hand shape five fingers, sweat on palm surface, "
     f"veins visible, dramatic tension in stillness. {STYLE}",
     "hand"),

    ("P20_一个念头",
     f"Split composition: left side {Q} in dark foreground staring with intense hungry eyes, "
     f"right side blurred drunk man in background with faint red glow, "
     f"ominous distance between them, internal voice visual tension. {STYLE}",
     "character"),

    ("P21_裁决过的所有人",
     f"Abstract flashback montage, multiple dark human silhouettes collapsing to ground in sequence, "
     f"faint red target markers flickering out one by one, {Q} shadow looming in foreground, "
     f"heavy weight of past sins, symbolic guilt imagery. {STYLE}",
     "abstract"),

    ("P22_瞳孔收缩",
     f"Extreme close-up {Q} face, pupils constricted to needle points, "
     f"sweat dripping down temple, breath visible as white mist from nostrils, "
     f"extreme tension, borderline panic, dramatic shadow. {STYLE}",
     "character"),

    ("P23_指甲入肉",
     f"Extreme close-up fist clenched tight, fingernails digging deep into palm flesh, "
     f"simple normal hand shape five fingers, blood seeping from nail marks, "
     f"white knuckles, pain of self-restraint. {STYLE}",
     "hand"),

    # === 情绪单元6：警告回忆 ===
    ("P24_夹缝警告",
     f"{Q} eyes squeezed shut, head bowed, dark shadowy figure looming in mind-space above, "
     f"NO facial features, NO mouth NO nose, ONLY empty glowing cyan eye sockets, "
     f"semi-transparent shadowy form, edges dissolving into smoke, terrifying warning presence. {STYLE}",
     "supernatural"),

    # === 情绪单元7：拼命压制 ===
    ("P25_猛地闭眼",
     f"Close-up {Q} face, eyes squeezed tightly shut, facial muscles strained taut, "
     f"sweat rolling down forehead, jaw clenched, every muscle fighting internal battle, "
     f"dramatic lighting emphasizing tension. {STYLE}",
     "character"),

    ("P26_三件事",
     f"Triple overlay montage: left panel office spreadsheet on desk, "
     f"center panel subway station clock showing late hour, "
     f"right panel child hands holding small turtle, {Q} desperate mental escape, "
     f"three layers blending into single composition. {STYLE}",
     "abstract"),

    ("P27_压回深处",
     f"Full body shot {Q} standing braced against invisible weight, "
     f"fists clenched at sides, body tense like fighting gravity itself, "
     f"dark energy being forced back down into body, victorious exhaustion. {STYLE}",
     "character"),

    # === 情绪单元8：回归现实 ===
    ("P28_再次睁眼",
     f"Close-up {Q} eyes snapping open, scene around transitioning from grayscale back to warm color, "
     f"security guard figures entering frame in blurred background, "
     f"moment of return, relief dawning. {STYLE}",
     "scene"),

    ("P29_按在地上",
     "Two uniformed security guards restraining drunk man on floor, "
     "drunk man struggling face-down arms pinned behind back, "
     "dynamic action pose, justice served, warm restaurant lighting restored. " + STYLE,
     "scene"),

    ("P30_扶走",
     "Young waitress being helped away by female coworker, seen from behind, "
     "waitress shoulders shaking with sobs, disheveled hair, coworker arm around her, "
     "walking away down restaurant aisle, sympathetic warm lighting. NO children. " + STYLE,
     "scene"),

    ("P31_红光消失",
     f"Close-up {Q} face, color fully returned to warm restaurant tones, "
     f"red glow fading and disappearing from glasses reflection, "
     f"exhausted relief washing over features, deep breath visible. {STYLE}",
     "character"),

    ("P32_背后冷汗",
     f"Shot from behind {Q}, back of beige casual blazer soaked dark with sweat patch, "
     f"shoulders heaving with heavy breathing, restaurant warm background blurred, "
     f"physical toll of internal battle made visible. {STYLE}",
     "character"),

    ("P33_煮老的肉",
     f"Overhead close-up of hot pot, red oil still bubbling, "
     f"few shriveled overcooked meat pieces floating alone in red broth, "
     f"chopsticks resting abandoned on bowl rim, loneliness in food. {STYLE}",
     "scene"),

    ("P34_走出店门",
     f"{Q} standing outside hot pot restaurant on street at night, "
     f"glass door behind reflecting warm golden light, early spring breeze moving short black hair and blazer, "
     f"cool air on face, moment of quiet. {STYLE}",
     "scene"),

    ("P35_正常人",
     f"Wide shot night street scene, {Q} in foreground blurred and out of focus, "
     f"street beyond sharp: young couples laughing, elderly with grocery bags, delivery riders on scooters, "
     f"warm street lamps, normal life flowing past. NO children. {STYLE}",
     "scene"),

    # === 情绪单元9：终幕独白 ===
    ("P36_他也想是",
     f"Medium shot {Q} standing still in flowing crowd of blurred strangers, "
     f"only he is in sharp focus, face lonely and longing, "
     f"surrounded by normal people he can no longer be, deep sadness. {STYLE}",
     "character"),

    ("P37_回不去了",
     f"Close-up {Q} face half illuminated by street light half in deep shadow, "
     f"expression of heavy acceptance, eyes carrying weight of irreversible change, "
     f"street fading to darkness behind him. {STYLE}",
     "character"),

    ("P38_意识的枪",
     f"Abstract symbolic composition, {Q} black silhouette standing alone, "
     f"semi-transparent ghostly handgun shape glowing faint red hovering at waist level, "
     f"darkness surrounding, gun fused to his being. {STYLE}",
     "abstract"),

    ("P39_终幕",
     f"{Q} walking away into deepening night, seen from behind, "
     f"ghostly red gun outline hovering at his waist, city lights ahead blurring into distant bokeh, "
     f"but he walks toward darkness not toward the light, loaded gun waiting. NO people. {STYLE}",
     "abstract"),
]
