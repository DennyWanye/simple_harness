# 用户决定入账（原话 SHA-256）
1. provider 配置与本地持久化（llm_runtime.json 明文=产品既有配置面；APIKEY 不进仓库/日志/证据）：
   用户原话 ".env里面有了，用5.6luna这个模型吧，本地配置弄好，不要每次都重新输入 provider信息"
   sha256=53e8d640d1af411974efd0192a2d3a63dd28b209732e7178c1b06a746eae0500（user-initiated）
2. 向量模型 WeMM-Embedding-2B 取代 BGE-M3：
   用户原话 "对了，向量模型使用这个：https://huggingface.co/tencent/WeMM-Embedding-2B， 而不是BGE"
   sha256=804fa34260665595109c1acbd0d46559c1cf7941995a924fb03a4285b0323747（user-initiated）
3. plan 批准："批准" sha256=8cbe697b157364a5b13646285b38409dc53ec5287deeb7913493e65b275cd14d
