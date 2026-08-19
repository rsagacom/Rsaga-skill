#!/usr/bin/env python3
"""启动本地 AI 漫剧工作台 API。"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("studio_api.main:app", host="127.0.0.1", port=8787, reload=False)
