#!/usr/bin/env python3
"""第九章 陈姝 — 未来世界（42格）"""
Q="Young Chinese man 25yo, thin black-frame glasses, short black hair with bangs, beige casual blazer, tired hollow eyes, pale skin"
G="High school girl with ponytail, school uniform, tired knowing eyes, young face,棒棒糖"
S="Manhua ink wash, black white, dramatic lighting, G-pen linework, grayscale, realistic."
PANELS=[
("P42_扉页",f"Split: young girl kneeling before secret地下实验室, futuristic city skyline in honey sky above. title text. {S}","abstract"),
# 陈姝的过去
("P01_葬礼第三天",f"Young girl sitting on living room floor holding ceramic mug, face buried in cup trying to inhale mother scent, parents funeral three days ago. {S}","character"),
("P02_优雅地debug",f"Close-up mug with printed text: world就算是个bug也要优雅地debug, laboratory equipment number on bottom barcode, childhood memory of mother. {S}","scene"),
("P03_车库钥匙",f"Extreme close-up small old key with yellowed label reading two characters:车库, hidden in mother desk drawer, taken secretly during funeral. {S}","scene"),
("P04_三天勇气",f"Young girl standing before suburban garage door, three days to gather courage,隐约 knowing what lies inside, mother always lowered voice saying garage. {S}","character"),
("P05_臭氧味",f"Close-up young girl face as卷帘门 rises, familiar ozone scent washing over, like someone just turned off machine running for very long time. {S}","character"),
("P06_太干净了",f"Wide shot garage interior: thirty square meters, shelves with tools and old computers and water cases, looks normal storage but too clean, dust thin, corners spotless. {S}","scene"),
("P07_银灰色轿车",f"Wide shot ordinary silver-gray sedan parked center of garage, faded平安符 hanging from rearview mirror, interior spotless, no crease on seat. {S}","scene"),
("P08_裂隙",f"Extreme close-up steering wheel underside: barely visible hairline fissure in metal surface, not mechanical button, like incision from something sharp. {S}","scene"),
("P09_插入钥匙",f"Young girl inserting key into fissure, key disappearing completely into metal, no sound no vibration no仪表盘 change. {S}","character"),
("P10_车库在下沉",f"Wide shot: entire garage floor sinking smoothly silently like ancient elevator,卷帘门 rising above, walls sliding past revealing deeper darker space below. {S}","abstract"),
("P11_地下实验室",f"Wide shot reveal: vast underground laboratory space, lights activating row by row, holographic displays, quantum computing arrays, secret父母遗产. {S}","scene"),
# 未来世界
("P12_巷道",f"Wide shot {Q} and {G} in narrow巷道 between gray-white walls with faint blue荧光纹路, narrow strip of蜜色 sky above, alien world first view. {S}","scene"),
("P13_二十三年后",f"Close-up {G} looking at bare wrist, casually announcing objective time: twenty-three years and seven months passed, 2024 to 2047, weekend afternoon. {S}","character"),
("P14_城市街道",f"Wide shot futuristic city street: road immaculately clean every seam dust-free, buildings five-six stories covered in semi-transparent流动 membrane displaying colors and information. {S}","scene"),
("P15_算力支局",f"Wide shot building with floating Chinese characters:淮海中路第三算力支局, following rows of incomprehensible numbers and symbols, public标识. {S}","scene"),
("P16_悬浮路灯",f"Close-up floating light sphere hovering four meters above ground, no pole no wire, slowly rotating, surface displaying time temperature data streams. {S}","scene"),
("P17_后颈标签",f"Wide shot pedestrians walking fast, each with指甲盖大小 circular tag on back of neck, colors from浅绿 to橙红, real-time physiological display. {S}","scene"),
("P18_命图指数",f"Close-up {G} explaining without looking back:命运华容道 model接入 every person from birth, computing optimizing allocating, tag shows算力消耗. {S}","character"),
("P19_绿色橙色红色",f"Visual explanation: green stable命运 prediction, orange命运路径变动 emergency纠偏, red模型判定即将危害系统最优解快要到头了. {S}","abstract"),
("P20_淡红色男人",f"Wide shot man with淡红 neck tag walking past, expressionless, behind him two meters: large hovering水滴 shape drone, pitch black surface, completely silent. {S}","scene"),
("P21_算力纠察",f"Close-up {G} explaining:纠察 drone follows red-tagged, three seconds to lock in维度夹缝 if action taken,两天放出路径已被修正. {S}","character"),
("P22_没有如果",f"{G} flat tone: model predicts behavior three to five days ahead, by time person realizes what they want to do,纠察队 already waiting楼下 four days. {S}","character"),
("P23_药店培养皿",f"Wide shot storefront: pharmacy displaying sealed petri dishes not medicine boxes,气质补丁 shop promising三天改善抑郁概率匹配社会最优人际路径. {S}","scene"),
("P24_命运校准站",f"Wide shot building labeled命运校准站第117号, seven or eight people queuing with麻木 expressions, like waiting for public toilet. {S}","scene"),
("P25_基因深处的不适",f"Close-up {Q} face, something rising from脊椎底部, not fear not anger, older emotion, animal removed from natural habitat into fully artificial control. {S}","character"),
("P26_算力场密度",f"Close-up {G} tapping temple: what you feel is not air light temperature, it is算力场 density, 2047 air filled with seven orders magnitude more量子计算波动 than 2024. {S}","character"),
("P27_神经系统排异",f"Medium shot {Q} leaning on wall,神经系统 in rejection, {G} explaining she grew up here but going to 2024 then returning creates same feeling. {S}","character"),
("P28_终幕街道",f"Wide shot {Q} and {G} walking deeper into蜜色-lit city, rows of floating light spheres, pedestrians with neck tags,命运华容道 model humming beneath everything. {S}","scene"),
]
