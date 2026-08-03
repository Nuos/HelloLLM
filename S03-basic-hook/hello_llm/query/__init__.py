"""核心层（包）—— Agent-Loop（论文图1 "Agent Loop" / §4.1）。

对应参考实现（claude-code 源码 src/query/）：
    本包 = 智能体查询循环，模型调用、工具分派与结果收集的迭代周期。

模块清单：
    agent_loop.py  query_loop() 生成器 + Conversation 会话状态
"""
