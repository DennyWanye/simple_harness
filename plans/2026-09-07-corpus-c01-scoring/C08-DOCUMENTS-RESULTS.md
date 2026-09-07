# C08 历史纪要与检查列表来源验证

最后更新：2026-09-07。

主候选 `dbb7a83a`，H0710/M619/S0313 installed：两个新控制首次 **2 PASS / 16.01s**。旧 scalar13、retained5、dispatcher1 未重跑。

C08-15 的旧会议决定及 assistant 纪要、C08-16 的旧项目决定及 assistant 检查列表，经实际 main、受控 loopback HTTP、SDK Run/终态与原 Host 来源持久化。沿原 USER job 生成非空记忆后，公开 suppression 隐藏旧 USER 和派生 assistant；重开生产 authority，下一实际 HTTP 请求不含旧内容。映射漂移/跨 case 拒绝也由这两个控制检查。

这是历史对话中的文档文本来源，不是文件、TaskScope、独立 rolling summary 或当前模型生成质量证明。共享正式 dispatcher 目前仍只允许 01/06/11/18，15/16 接入另待；剩余 derived 来源也未全部闭合。

命令：当前 installed target 优先 PYTHONPATH，经共享 `run_resource_bounded.py --rss-mib 2048 --seconds 180`，`pytest -q -x backend/tests/quality/test_corpus_c08_documents_main.py`，basetemp 位于下列 ignored 目录。

PG87213 exit0，16.958s，peak564432KiB，minDisk4280MiB，remaining=[]、stop=null、cleanup=null。两个 child 自然退出。防熄屏仍保持。原始证据不进入 Git。

| 本机证据 | SHA-256 |
|---|---|
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/command.log` | `f4f3f91abe600cc9d14f1b65df682a7c62a69f77fa92edd071cc57bcafd2a654` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/resource.json` | `8c4b4dad5f271d3c879349258544297fc76562a593ea62c20a8c977a5b853f4d` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/tmp/test_actual_main_document_sour0/main-retained/child.log` | `3caec3a9c79f576f96df5bae072929962b49e35904b3155c823a6420e7bfe156` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/tmp/test_actual_main_document_sour0/main-retained/control.json` | `fc5f54930a345d5b0045b9860ab19e7ffbeeb5f3f384d73f6cad59dbeb355db3` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/tmp/test_actual_main_document_sour0/main-retained/result.json` | `f85b52bcff475b42e3ef8dd9fd586ab465653cd63d70217548f90e24d5325a37` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/tmp/test_actual_main_document_sour1/main-retained/child.log` | `4655e36df869e103dbd923a743eda52d6bb0035fcc54d2ad232826e80ff034e8` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/tmp/test_actual_main_document_sour1/main-retained/control.json` | `2064f3a0b6e7eaf3a888d70b9e41d6fb0652322036e81b89ce13a447e70b17f0` |
| `.local-test-evidence/2026-09-07/corpus-c08-documents/r1/tmp/test_actual_main_document_sour1/main-retained/result.json` | `e4ad87390cca24fefd4a81cb9887b6d7fbfcc788d9bb57389e1f9460dfbd5198` |
