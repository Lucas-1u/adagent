"""Creative Agent —— AI Marketing Employee 的广告素材生成 Agent（队友 4）。

模块入口：
    from creative_agent import CreativeAgent
    agent = CreativeAgent()
    result = agent.run(state, out_dir="output/demo")

命令行入口：
    python -m creative_agent --brief demo/briefs/pindou_qixi.json --out output/demo
"""

from .agent import CreativeAgent
from .schema import CreativeResult, normalize_state

__all__ = ["CreativeAgent", "CreativeResult", "normalize_state"]
__version__ = "0.1.0"
