# Campus Officer · 校园事务官

> **一句话介绍**：一个面向本校本科生的教务事务智能体，把「**选课（冲突裁决）→ 奖助学金政策与材料 → 学期学习规划**」三件校务事串成一条**可执行、可追溯、能产出文件**的闭环事务链。
>
> **路径归属**：路径 A（技术栈较完整的智能体应用），并具备路径 B 强调的业务闭环特性——Agent 接到了自建的教务业务 API 上。

---

## 📑 目录

- [一、解决什么问题](#一解决什么问题)
- [二、技术栈与各部分分工](#二技术栈与各部分分工)
- [三、环境要求](#三环境要求)
- [四、安装与启动（Windows）](#四安装与启动windows)
- [五、配置说明](#五配置说明)
- [六、推荐演示话术](#六推荐演示话术)
- [七、相对课堂 demo 的改动说明](#七相对课堂-demo-的改动说明)
- [八、目录结构](#八目录结构)
- [九、关键代码指认](#九关键代码指认)
- [十、运行与测试](#十运行与测试)
- [十一、已知限制与后续改进](#十一已知限制与后续改进)
- [十二、思政与合规说明](#十二思政与合规说明)

---

## 一、解决什么问题

### 1.1 场景痛点

本科生在开学季面临三件互相独立、但都**卡在"信息分散 + 规则复杂"**上的事：

| 痛点 | 现状 | 本项目怎么解决 |
|---|---|---|
| 选课怕撞车 | 教务系统只报"冲突"，不告诉你**换成什么课更好** | 确定性冲突检测 + 回溯搜索求无冲突组合，并解释**每门课为什么选/不选** |
| 奖助学金看不懂 | 政策分散在多个文件，且**新旧版本并存**，学生常按废止文件准备材料 | 条款带**文号与生效期**，过期条款自动降权并提示"以官方最新文件为准" |
| 时间排不过来 | 课表、竞赛、考研三线并行，每周靠感觉安排 | 基于真实课表算时间预算，产出四象限周计划与可导入日历 |

### 1.2 目标用户

- **主要**：本校本科生（选课决策者、奖助学金申请者、考研/竞赛备赛学生）
- **次要**：辅导员 / 教务助理（用产出物批量核对方案，事务全程留痕）

### 1.3 典型使用流程

```
①「下学期能选哪些课？避开周三下午，挑 3 门最值得上的，别超 20 学分」
     → list_courses → get_my_enrollments → check_schedule_conflict
     → 【子代理·回溯求解】→ 输出推荐组合 + 未选中原因
     → 产出《选课清单.md》

②「我 GPA 3.6，能申请哪些奖助学金？要交什么材料？什么时候截止？」
     → check_eligibility（确定性规则，逐项给依据）
     → search_policy（文号溯源 + 失效条款降权）
     → 产出《奖助学金材料自检表.md》

③「就选这两门，帮我排每周学习计划」
     → enroll_course（★二次确认★）
     → 【子代理·时间预算】→ 产出《学期学习计划.md》+ .ics
```

---

## 二、技术栈与各部分分工

### 2.1 技术栈清单

| 层次 | 技术 | 选型理由 |
|---|---|---|
| 模型 | **阿里百炼** `qwen-plus`（OpenAI 兼容接口），可切 Ollama / ModelScope | 国产合规、OpenAI 兼容接口，换模型只改 `base_url` |
| Agent 框架 | **LangGraph**（开源，StateGraph 手写节点 + 条件边） | 状态图可精确控制工具调用事件、二次确认节点与降级分支 |
| 界面 | **Streamlit** | 支持 `st.write` 流式推送，工具执行过程可实时显示 |
| 业务后端 | **FastAPI + SQLite**（自建） | Agent 通过真实 HTTP 接口调用业务能力，不是假工具 |
| MCP | **官方 MCP Python SDK**（stdio） | 知识库以标准协议暴露，便于其它客户端复用 |
| 检索 | **BM25 + jieba** | 零外部 API 依赖，断网可用；校规是术语精确匹配场景 |
| 记忆 | **SQLite**（会话）+ **AGENTS.md**（长期） | 会话可回看，画像跨会话保留 |

### 2.2 各部分解决什么问题

| 组件 | 数量 | 解决的问题 | 关键文件 |
|---|---|---|---|
| **自定义工具** | 8 | 把"查课程/查已选/算冲突/选课/查政策/判资格/写产出"变成模型可调用的能力 | `tools/` |
| **MCP 服务器** | 1（3 工具） | 把知识库以标准协议暴露：`search_policy` / `get_deadlines` / `list_policy_docs`（本地 `search_policy` 走后端，MCP 版走 stdio 标准协议，两者互补而非重复） | `mcp_server/server.py` |
| **Skills** | 2 | 规定"选课事务"与"政策解读"的执行顺序与输出规范，防止模型自由发挥 | `skills/*/SKILL.md` |
| **子代理** | 2 | 承担确定性计算：课程组合求解（回溯搜索）、学习时间预算 | `core/subagents.py` |
| **确定性算法** | 1 套 | 时间冲突用闭区间求交判定，结论可复现、可讲解 | `backend/schedule.py` |
| **降级链** | 3 层 | 百炼 → Ollama → 规则引擎，保证任何网络条件下演示都能跑完 | `core/llm.py` |

### 2.3 工具清单（8 个本地工具 + 3 个 MCP 工具）

| 工具 | 类型 | 作用 | 后端 |
|---|---|---|---|
| `list_courses` | 本地 | 课程目录查询（关键词/类别/学院/学分过滤） | `GET /api/courses` |
| `get_my_enrollments` | 本地 | 我的已选课程与时段 | `GET /api/enrollments/{sid}` |
| `get_my_profile` | 本地 | 学生档案（GPA/排名/困难认定/已修学分） | `GET /api/profile/{sid}` |
| `check_schedule_conflict` | 本地 | **确定性**时间冲突检测 | `POST /api/schedule/conflicts` |
| `enroll_course` | 本地 | 选课写入（容量/冲突/学分三重校验，**需二次确认**） | `POST /api/enrollments` |
| `search_policy` | 本地 | 校规检索（BM25 + 失效降权 + 文号溯源） | 本地知识库 |
| `check_eligibility` | 本地 | 奖助学金资格初筛（逐项给依据） | `GET /api/eligibility/{sid}` |
| `write_artifact` | 本地 | 产出物写入（**仅限 outputs/**） | 本地文件系统 |
| `search_policy` | MCP | 政策检索（stdio 协议） | `mcp_server/server.py` |
| `get_deadlines` | MCP | 截止时间查询 | `mcp_server/server.py` |
| `list_policy_docs` | MCP | 制度文件目录列举 | `mcp_server/server.py` |

### 2.4 智能体在界面上"看得见"

打开界面就能确认这不是一个查表机器人。每轮回答固定呈现**三段式**：

| 段 | 内容 | 证明什么 |
|---|---|---|
| 🧭 **规划** | 智能体把一句话拆成的 2~4 个步骤，每步标注**将要调用的工具** | 它先想清楚再动手，不是拿到查询直接返回 |
| 🔧 **执行** | 真实工具调用轨迹：工具名、入参、耗时、返回摘要 | 工具**真的被调用**了，不是模型凭空编造 |
| 📄 **结果** | 最终答复 + 产出物文件路径 | 事务有闭环、有文件留痕 |

计划由 `core/graph.py` 的 `_plan()` 阶段生成：
- **真实模型层**——大模型自主拆解（界面标注"大模型自主拆解"）；
- **规则降级层**——由意图识别映射出计划模板（标注"规则映射"）。

计划还会**注入回模型上下文**，让模型按计划推进而非自由发挥。

### 2.5 三个创新点

**① LLM 规划与规则裁决分离**
时间冲突**不由模型目测课程名**，而由 `backend/schedule.py` 的闭区间求交算法判定——同星期且节次区间相交即冲突。模型负责理解意图与解释结论，算法负责"算"。子代理用回溯搜索在"先修 ∧ 无冲突 ∧ 学分上限 ∧ 容量 ∧ 避开某天"五重约束下求组合。
> 这也正面回答了老师可能问的"**这个工具如果不调用，模型会怎样**"——模型会漏判边界相接的情况（1-2 节与 2-3 节其实是同一节课）。

**② 条款时效核验 + 文号溯源**
每条校规条款携带 `文号 / 生效日期 / 失效日期 / 来源` 四要素。检索时**过期条款排序分乘以 0.25 降权并沉底**，同时在命中上标注"已失效，请以学校官方最新文件为准"；命中结果附文号与来源，让每个结论**可核验**。

**③ 三层降级 + 事务确认**
`百炼 → Ollama → 规则引擎`逐级降级，规则层用关键词意图识别**照样调用真实业务工具**（不是假回复）。选课提交需用户点击确认，Agent 只能写 `outputs/` 目录，越权文件名被显式拒绝。

---

## 三、环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| Python | **3.10+**（推荐 3.12/3.13） | 需支持 `X \| None` 类型语法 |
| 操作系统 | Windows / macOS / Linux | 提供 `start.bat` 与 `start.sh` |
| 网络 | 首次安装依赖需要 | 运行期**不需要**网络（可走规则降级层） |
| API Key | 可选 | 配了百炼 Key 走真实模型；没配也能完整演示 |
| 内存 | ≥ 2GB | 本地跑 Ollama 7B 建议 ≥ 8GB |

---

## 四、安装与启动（Windows）

### 4.1 最简方式（一键）

```bat
:: 在项目目录打开 PowerShell 或 CMD
start.bat
```

`start.bat` 会自动完成：检查 Python → 缺依赖则安装 → 缺 `.env` 则复制模板 → 启动教务后端（端口 8811）→ 启动 Streamlit（端口 8501）。

### 4.2 手动方式（推荐，便于理解每个步骤）

```bat
:: 1) 创建并激活虚拟环境
python -m venv .venv
.venv\Scripts\activate

:: 2) 安装依赖
pip install -r requirements.txt

:: 3) 配置环境变量
copy .env.example .env
notepad .env

:: 4) 【另开一个窗口】启动教务业务后端
python backend\main.py

:: 5) 【回到原窗口】启动界面
python -m streamlit run app.py
```

浏览器访问 **http://localhost:8501**

### 4.3 macOS / Linux

```bash
chmod +x start.sh && ./start.sh
```

### 4.4 Docker（可选）

```bash
docker compose up --build
```

---

## 五、配置说明

`.env.example` 是模板，复制为 `.env` 后按需修改。**`.env` 已被 `.gitignore` 排除，真实 Key 不会进入仓库。**

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `bailian` | `bailian` / `ollama` / `mock` |
| `DASHSCOPE_API_KEY` | 空 | 百炼 Key，[控制台](https://bailian.console.aliyun.com/) → 密钥管理获取 |
| `DASHSCOPE_BASE_URL` | 百炼兼容地址 | 换 ModelScope 等服务只改此项 |
| `DASHSCOPE_MODEL` | `qwen-plus` | 支持工具调用的模型 |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | 本地地址 | 本地模型方案 |
| `CAMPUS_API_BASE_URL` | `http://127.0.0.1:8811` | 教务后端地址 |
| `OUTPUT_DIR` | `outputs` | 产出物目录（Agent 唯一可写目录） |
| `MAX_TOOL_ROUNDS` | `8` | 单轮最大工具调用次数（防死循环） |

### 关于「没有 Key 怎么办」

项目**内置规则降级引擎**（第 3 层）。当百炼与 Ollama 都不可用时自动接管：
用关键词识别意图（选课 / 冲突 / 资格 / 政策 / 截止 / 产出），**然后调用同一套真实业务工具**，把结果组织成结构化回复。

也就是说：**断网、无 Key 的教室电脑上，主路径依然能跑完，工具调用依然真实发生。**

---

## 六、推荐演示话术

界面左上角有「💡 推荐演示话术」按钮，点击可直接填入。5 条主推话术：

| # | 话术 | 演示的亮点 |
|---|---|---|
| 1 | `下学期能选哪些课？避开周三下午，挑 3 门最值得上的，别超 20 学分` | 4 个工具连续调用 + 子代理回溯求解 + 产出选课清单 |
| 2 | `我 GPA 3.6，能申请哪些奖助学金？要交什么材料？什么时候截止？` | 确定性资格判定（逐项给依据）+ 文号溯源 + 材料自检表 |
| 3 | `《人工智能导论》和《数据结构与算法》冲突吗？` | 冲突算法现场可讲解，结论可复现 |
| 4 | `帮我排一份每周学习计划` | 时间预算子代理，产出四象限周计划 |
| 5 | `帮我把选课方案整理成一份清单文件` | 产出物写入，界面侧栏可直接下载 |

**建议现场节奏**：话术 1（60 秒，看工具调用）→ 话术 2（45 秒，看溯源与合规）→ 话术 3（30 秒，看算法）→ 下载产出物（15 秒）。

---

## 七、相对课堂 demo 的改动说明

> 遵循《期末考查要求》"仅把 demo_5 改名、换提示词、换皮肤，按未完成处理"的要求，以下为本项目**可指出的增量**。

| # | 增量 | 说明 |
|---|---|---|
| 1 | **自建教务业务 API** | FastAPI + SQLite，11 个接口（其中 7 个供 Agent 工具层调用）。Agent 通过**真实 HTTP 调用**业务能力，不是把工具写成假函数 |
| 2 | **确定性冲突算法** | `backend/schedule.py` 闭区间求交 + `core/subagents.py` 回溯搜索。结论可复现、可在答辩时逐步讲解 |
| 3 | **条款时效核验机制** | 每条校规带文号与生效期，过期条款自动降权沉底。知识库里**故意放了一条已失效条款**用于演示该机制 |
| 4 | **自建 MCP 服务器** | `mcp_server/server.py` 用官方 SDK 实现，stdio 传输，3 个工具。其中 `get_deadlines`/`list_policy_docs` 经 `core/mcp.py` 常驻 session 桥接**真实挂载进智能体**，可被任意 MCP 客户端复用 |
| 5 | **2 个符合规范的 Skill** | `SKILL.md` 带 YAML frontmatter，规定执行顺序、输出结构与**禁止事项** |
| 6 | **三层降级链** | 百炼 → Ollama → 规则引擎。规则层仍调用真实工具，保证演示不中断 |
| 7 | **危险操作二次确认** | 选课提交需用户确认；产出物写入受目录限制，越权文件名显式拒绝 |
| 8 | **长期记忆** | `AGENTS.md` 跨会话记住学生画像，自动记录已办事项 |
| 9 | **产出物体系** | 选课清单 / 材料自检表 / 学习计划 / .ics 日历 / 事务日志 JSON |
| 10 | **80 项单元测试** | 覆盖冲突算法边界、资格判定口径、路径穿越防护、智能体行为可见性、意图消歧、MCP 工具挂载 |

**参考来源**：LangGraph / LangChain / MCP Python SDK / Streamlit / FastAPI 官方文档与课堂实验代码。核心逻辑（冲突算法、回溯求解、BM25 检索、降级链、资格规则）均为本项目自行实现。

---

## 八、目录结构

```
campus-officer/
├── README.md                   # 本文件
├── requirements.txt            # 依赖清单
├── .env.example                # 环境变量模板（不含真实 Key）
├── .gitignore
├── AGENTS.md                   # 长期记忆（自动维护）
├── Dockerfile / docker-compose.yml
├── start.bat / start.sh        # 一键启动
├── app.py                      # Streamlit 入口
│
├── core/                       # ── 智能体核心
│   ├── config.py               # 配置与环境变量管理
│   ├── llm.py                  # OpenAI 兼容接入 + 三层降级
│   ├── graph.py                # LangGraph 状态图（手写节点，非预构建封装）
│   ├── mcp.py                  # MCP 工具桥接（异步 stdio → 同步工具）
│   ├── prompts.py              # 系统提示词
│   ├── subagents.py            # 子代理：回溯求解 + 时间预算
│   ├── knowledge.py            # BM25 检索 + 时效降权
│   ├── api_client.py           # 业务后端 HTTP 客户端
│   ├── artifacts.py            # 产出物写入（权限隔离）
│   ├── memory.py               # 会话持久化 + 长期记忆
│   ├── rule_engine.py          # 规则降级引擎
│   └── schemas.py              # Pydantic 数据模型
│
├── tools/                      # ── 自定义工具（8 个）
│   ├── courses.py              # list_courses / get_my_enrollments
│   │                           # check_schedule_conflict / enroll_course
│   ├── policy.py               # get_my_profile / search_policy / check_eligibility
│   ├── artifacts.py            # write_artifact
│   └── registry.py             # 工具注册表
│
├── mcp_server/server.py        # ── MCP 服务器（3 工具，stdio）
│
├── skills/                     # ── Agent Skills（2 个）
│   ├── course-planning/SKILL.md + templates/
│   └── policy-explainer/SKILL.md + templates/
│
├── backend/                    # ── 自建教务业务后端
│   ├── main.py                 # FastAPI 接口（11 个，7 个供 Agent 工具层调用）
│   ├── db.py                   # SQLite 数据访问
│   ├── seed.py                 # 种子数据（虚构样例）
│   ├── schedule.py             # ★ 确定性冲突算法
│   └── eligibility.py          # 奖助学金资格规则引擎
│
├── knowledge/policies/         # ── 校规条款库（带文号与时效）
│
├── outputs/                    # ── 产出物目录（Agent 唯一可写目录）
├── data/                       # SQLite 数据库（gitignore）
├── tests/                      # ── 161 项 pytest
└── docs/presentation.html      # ── 介绍 HTML（12 页，零外链，含 5 页代码讲解）
```

---

## 九、关键代码指认

答辩时可直接跳转以下位置：

| 位置 | 内容 |
|---|---|
| `backend/schedule.py:39` | `find_conflict_pair()`——冲突判定的核心，闭区间求交 |
| `core/subagents.py:68` | `CourseCombinationAgent.solve()`——回溯搜索求课程组合 |
| `core/knowledge.py:159` | `PolicySearcher.search()`——BM25 + 失效条款降权 |
| `core/graph.py:103` | `CampusGraph._build()`——LangGraph StateGraph 装配（节点 + 条件边） |
| `core/graph.py:343` | `CampusGraph.run()`——LangGraph 状态图对外入口 |
| `core/graph.py:405` | `CampusGraph._plan()`——规划阶段 |
| `core/llm.py:107` | `ModelProvider._try_bailian()`——降级链实现 |
| `core/artifacts.py:49` | `ArtifactWriter._resolve_safe()`——产出物越权防护 |
| `mcp_server/server.py:40` | MCP 工具定义 |
| `skills/course-planning/SKILL.md` | Skill 执行规范 |

---

## 十、运行与测试

### 10.1 单元测试

```bat
pytest tests/ -v
```

预期：**161 passed**。覆盖内容：

- `tests/test_schedule.py`——冲突算法边界（区间相接、不相交、包含、多时段、三课两两冲突）
- `tests/test_eligibility_and_artifacts.py`——资格判定口径、排名未核定时不误判、合规提示、**路径穿越防护**
- `tests/test_knowledge_and_subagents.py`——失效条款降权、文号完整性、子代理五重约束求解
- `tests/test_degradation.py`——工具注册表完整性、**降级路径下工具仍被真实调用**、意图消歧 13 例
- `tests/test_app.py`——界面端到端（Streamlit AppTest），含**智能体行为可见性**断言：规划已渲染、计划与执行对齐、刷新后规划仍在

### 10.2 MCP 服务器连通性自测

```bat
python mcp_server\server.py
```

用任意 MCP 客户端（如 Inspector）连接 stdio，验证 3 个工具可调用。

### 10.3 后端接口自测

```bat
curl http://127.0.0.1:8811/api/health
curl http://127.0.0.1:8811/api/summary
```

### 10.4 端到端演示路径

1. `start.bat` 启动
2. 浏览器打开 http://localhost:8501
3. 左侧确认「模型」与「教务后端」两个指示灯均为绿色（无 Key 时模型灯为黄色，属正常降级）
4. 依次执行上面 5 条演示话术
5. 侧栏「📦 产出物」下载生成的清单文件

---

## 十一、已知限制与后续改进

| 限制 | 现状 | 计划 |
|---|---|---|
| 教务数据为虚构 | 种子数据模拟真实教务 | 接入学校教务系统只读 API（需授权） |
| 检索为 BM25 | 无语义理解 | 混合 BM25 + 向量检索，重排序 |
| 工具调用串行 | 一轮一个工具 | 引入并行工具调用与子代理并发 |
| 规则引擎表达能力有限 | 关键词意图识别 | 扩展为小型意图分类器 + 槽位填充 |
| 无语音/多模态 | 纯文本 | 接入 OCR 解析通知截图 |
| 会话存储在本地 | SQLite 单机 | 迁移到 PostgreSQL 支持多用户 |

---

## 十二、思政与合规说明

1. **服务正当学业需求**：项目服务于学生的选课与资助办事需求，不代写作业、不代写论文、不提供考试作弊相关功能。
2. **数据来源可核验**：知识库中 `is_sample: false` 的条款指向**教育部/财政部公开文件**（附文号）；`is_sample: true` 的为**虚构的校内演示样例**，在检索结果、界面与产出物中**均显著标注**，不会被误认为真实校规。
3. **金额与时限不编造**：所有金额为示例说明值，截止时间表述**保留原文限定词**（如"一般""通常"），不擅自简化成确定日期。
4. **个人信息最小化**：全部为虚构样例数据（学号 20260001 等）。系统**不采集**学号、银行卡、身份证等敏感信息，家庭经济困难认定仅读取学校已认定结论。
5. **统一合规提示**：涉及制度、资助、认定事项的回答与产出物，均附「**以学校官方最新文件为准**」。
6. **用途限制**：产出物仅供办事参考，系统不代填、不代交任何申请材料。

---

<div align="center">

**Campus Officer · 校园事务官**
《开源 AI 技术实践》期末项目 · 路径 A

</div>
