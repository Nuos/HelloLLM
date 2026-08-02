# HelloLLM

依据论文《Dive into Claude Code》(arXiv:2604.14228v2) 图1 七组件模型重建的最简可交互 AI 编码 Agent。

## 仓库结构

```
hello_llm/                      # S 系列阶段容器
└── S01-basic-loop/             # 阶段一：CLI 入口层 + Agent-Loop（最简可交互版本）
    ├── hello_llm/              #   Python 包（entrypoints / query / config / providers / tools / logging）
    ├── tests/                  #   34 项单元测试
    ├── docs/                   #   开发规范文档（带可伸缩侧边导航）
    ├── pyproject.toml          #   项目配置
    ├── README.md               #   阶段说明
    └── requirements.txt        #   运行时零第三方依赖（纯标准库）
```

后续阶段（权限系统 / 会话持久化 / MCP / 压缩流水线 / Hook / 子 Agent）将作为
`S02-*`、`S03-*` 并列放入 `hello_llm/` 容器。

## 快速开始

```bash
cd hello_llm/S01-basic-loop
python3 -m venv .venv
.venv/bin/python -m pip install -e .
# 配置 API key：~/.hellollm/config.json（详见 S01-basic-loop/README.md）
.venv/bin/python -m hello_llm          # 交互 REPL
.venv/bin/python -m hello_llm -p "你好" # 无头单次
```
