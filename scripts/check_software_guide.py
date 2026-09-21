"""Live check: verify the agent reads the software guide before answering."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import build_agent
from unittest.mock import patch
import agent

with patch.object(agent, "read_software_guide", wraps=agent.read_software_guide) as guide:
    result = build_agent(group_chat=True).invoke(
        {"messages": [{"role": "user", "content": '@AI 介绍一下这个软件，能看懂图片吗？'}]},
        config={"recursion_limit": 20},
    )
    read = guide.invoke.called
print("GUIDE_READ_BEFORE_MODEL:", read)
print(result["messages"][-1].text)
sys.exit(0 if read else 1)
