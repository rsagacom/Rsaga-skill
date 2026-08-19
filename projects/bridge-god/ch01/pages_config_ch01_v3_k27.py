#!/usr/bin/env python3
"""ch01 K2.7分镜排版 v3.2 — 按漫画节奏:特写/关键用大格,过渡用小格"""
from pathlib import Path
INPUT_DIR = Path.home()/"Desktop/桥底第一章漫画_K2.7分镜/panels"
OUTPUT_DIR = Path.home()/"Desktop/桥底第一章漫画_K2.7分镜/pages"

PAGES = [
    ("full", ['S1P1']),
    ("hero", ['S1P2', 'S1P3', 'S1P4']),
    ("1x2", ['S1P5', 'S1P6']),
    ("full", ['S2P1']),
    ("2x2", ['S2P2', 'S2P3', 'S2P4', 'S2P5']),
    ("hero", ['S3P1', 'S3P2']),
    ("full", ['S3P3']),
    ("1x2", ['S3P4', 'S3P5']),
    ("2x2", ['S4P1', 'S4P2', 'S4P3', 'S4P4']),
    ("full", ['S4P5']),
    ("hero", ['S4P6', 'S4P7']),
    ("full", ['S5P1']),
    ("hero", ['S5P2', 'S5P3']),
    ("1x2", ['S5P4', 'S5P5']),
    ("full", ['S5P6']),
]

BUBBLE_CONFIG = {
    'S1P1': [('narration', '每天早上七点五十分，他准时挤上开往市中心的地铁。', 'bottom')],
    'S1P2': [('narration', '他只是需要那个重量，那个能让他看起来有事可做的道具。', 'bottom')],
    'S1P3': [('narration', '保安换过两茬，没人问过他。', 'bottom')],
    'S1P4': [('narration', '中午，他去便利店买两个饭团。', 'bottom')],
    'S1P5': [('narration', '下午，他继续投那些永远不会有回音的简历。', 'bottom')],
    'S1P6': [('narration', '他知道自己在腐烂，只是腐烂得很安静。', 'bottom')],
    'S2P1': [('narration', '那天傍晚，他厌倦了。', 'bottom')],
    'S2P2': [('narration', '公园的入口藏在两堵危墙之间。', 'bottom')],
    'S2P3': [('narration', '公园早已荒废。', 'bottom')],
    'S2P4': [('narration', '最深处，是一座石桥。', 'bottom')],
    'S2P5': [('narration', '祁思远看见一个女人走进了那个阴影里。', 'bottom')],
    'S3P1': [('narration', '桥洞里比外面暗得多。', 'bottom')],
    'S3P2': [('narration', '那些刻痕覆盖了整个桥洞的内壁。', 'bottom')],
    'S3P3': [('narration', '它们在一明一暗地搏动，像心跳。', 'bottom')],
    'S3P4': [('narration', '他想跑，但双腿没有回应。', 'bottom')],
    'S3P5': [('narration', '“你在找什么？”', 'bottom')],
    'S4P1': [('narration', '“这里是世界的夹缝。”', 'bottom')],
    'S4P2': [('narration', '“你……你是谁？这是什么东西？”', 'bottom')],
    'S4P3': [('narration', '“你可以叫我……租客。”', 'bottom')],
    'S4P4': [('narration', '“你走进来的时候，眼睛里写满了厌倦。”', 'bottom')],
    'S4P5': [('narration', '“容器。”', 'bottom')],
    'S4P6': [('narration', '“我能给你一个机会。”', 'bottom')],
    'S4P7': [('narration', '“杀人。”', 'bottom')],
    'S5P1': [('narration', '“现在只是手臂。”', 'bottom')],
    'S5P2': [('narration', '“我答应！我答应你！”', 'bottom')],
    'S5P3': [('narration', '黑色的线停在手肘处，然后退去了。', 'bottom')],
    'S5P4': [('narration', '“我给你四小时。”', 'bottom')],
    'S5P5': [('narration', '他跑回那条待拆的街道，一直跑到双腿发软。', 'bottom')],
    'S5P6': [('narration', '他知道，有什么东西已经不一样了。', 'bottom')],
}