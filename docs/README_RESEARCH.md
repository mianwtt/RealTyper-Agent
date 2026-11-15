# Agent 项目 README 研究

2026-11-15 品牌更新：项目统一使用 **RealTyper-Agent** 与 **Real Typing, Not Text Insertion.**；横幅和两个真实界面截图同步更新。验证台截图在仅本地词典模式生成计划，未执行输入。本地 LLM 入门补充引用智泊AI产品经理教程的 [Ollama 部署 Qwen3-VL 文章](https://blog.csdn.net/2301_80239908/article/details/154696963)，注明作者、标题、平台和原文链接，并区分文章的示例模型与本项目默认模型；操作命令另用 Ollama 官方文档核对。

## 2026-11-15：面向 GitHub 发布的改写

用户所说的“百星项目”指超过 100 Star 的项目。本轮重新查看 [Browser Use 官方 README](https://github.com/browser-use/browser-use/blob/main/README.md)、[smolagents 官方 README](https://github.com/huggingface/smolagents/blob/main/README.md) 和 [CrewAI 官方 README](https://github.com/crewAIInc/crewAI/blob/main/README.md)。当日仓库页显示 Browser Use 约 121k、smolagents 约 30.4k，符合样本条件；数字仅作筛选依据。

本轮采用的结构是：一句话定位 → 本项目实录 → 最短启动命令与三步操作 → 能力表 → 选词闭环 → 兼容范围 → 可复查验证 → 折叠 FAQ。首屏突出用户操作和结果，详细历史移交发布与验证文档。当前版本统一为 v1.1.1、150 ms 按键基准和每段三次尝试，85 项测试在 2026-11-15 重新运行通过。

学习的是信息顺序与表达方式，没有复制第三方宣传文案、Logo 或演示。真实截图保留界面来源说明；输入实录与历史实际输入记录不标为 v1.1.1 新录制。未增加未经授权的开源许可证、虚构 CI 徽章或通用兼容承诺。

## 2026-11-10：初版研究记录

研究日期：2026-11-10。目标是学习 Agent 项目如何呈现价值、演示能力和引导上手，并应用到 RealTyper-Agent V1。

## 样本与来源

Star 为当天 GitHub 仓库页面显示的约数，均超过 1,000；它们用于筛选样本。下文是对文档结构的编辑分析，不能据此证明某种写法带来了多少 Star。

| 项目与官方 README | 页面显示 Star | 观察到的表达方式 | 对本项目的启发 |
| --- | ---: | --- | --- |
| [Browser Use](https://github.com/browser-use/browser-use#readme) | 约 120k | 品牌图、简洁定位、真实任务演示，再分不同使用路径进入 Quickstart。 | 首屏先说明输入价值；用实录展示拼音组合与上屏；快捷模式和验证台分别说明。 |
| [OpenHands](https://github.com/OpenHands/OpenHands#readme) | 约 93.1k | 当前 README 以 Agent Canvas 为入口，定位后紧接导航、能力表和多种部署选项。 | 用清楚的入口替代调试历史；让读者快速找到启动、说明和验证记录。 |
| [CrewAI](https://github.com/crewAIInc/crewAI#readme) | 约 60.7k | 先解释核心概念的价值，随后组织 Why、入门步骤、功能和案例。 | 将分词、选词、纠错翻译为用户得到的结果；示例和细节分层呈现。 |
| [AutoGen](https://github.com/microsoft/autogen#readme) | 约 61.8k | 最小示例逐步扩展到复杂协作，并链接后续文档。当前仓库明确标记已归档。 | 保留一个可运行的最小入口；明确版本边界。这里只参考文档结构，不作为当前框架选型建议。 |
| [smolagents](https://github.com/huggingface/smolagents#readme) | 约 30.3k | 用简短定位与精炼特性列表建立认知，随后给出很短的 Quick demo 和演示视频；可选细节折叠。 | 快速开始给出直接可执行的命令；将高级参数移入独立使用说明。 |

GitHub REST API 本次触发未认证限流，Star 使用仓库页面核验值，不标成精确 API 数值。README 正文另从各项目官方 `raw.githubusercontent.com/.../main/README.md` 读取；公开的 [来源摘要](evidence/readme-sources.json) 保存链接与正文 SHA-256。本地研究快照保存在被忽略的 `runs/readme-research/`，不将第三方 README 或图片复制为项目宣传素材。

## V1 README 的表达决策

| 位置 | 读者的问题 | 采用的内容 |
| --- | --- | --- |
| 首屏 | 这是什么，能替我做什么？ | “按词组输入，按原文交付。”配原创横幅、一句话定位和导航。 |
| 演示 | 它是否真的经过拼音输入法？ | 真实录制的 GIF；明确实录与流程示意的区别。 |
| 核心卖点 | 为什么值得用？ | 词组规划、实际拼音按键、回读纠错、可调节奏、快捷入口、本机配置。 |
| 快速开始 | 怎样先跑起来？ | 安装依赖后启动快捷模式；已有环境直接双击启动器。 |
| 产品界面 | 日常使用与检查计划分别在哪里？ | V1 真实快捷窗口截图和实际模型规划截图。 |
| 原理与边界 | 怎样选词，能在哪些地方使用？ | 输入闭环图、候选回读说明及兼容表。 |
| 可信度 | 有什么证据？ | 定版验收摘要、可运行验证命令和原始记录入口。 |

文案以已实现能力为准：不把规则节奏描述成真人数据拟合，不承诺固定次数选对候选，不宣称任意应用兼容，不虚构 Star、CI 状态、性能排名或许可证。默认本机模型与不调用模型的规则模式分别说明。

## 图像来源

- `assets/hero.svg`、`assets/workflow.svg`：为本项目编写的原创矢量图；首屏选词窗口明确标注为流程示意。
- `assets/quick-mode.png`：运行 V1 快捷模式并真实注册快捷键后的窗口截图。
- `assets/planner.png`：当前为 RealTyper-Agent v1.1.1 使用本地词典生成计划的真实界面，截图时未执行录入。初版截图使用过本机 Qwen。
- `assets/pinyin-demo.gif`：从已有的微软拼音真实输入录像转换而来，展示多字组合与上屏。该录像来自 V1 沿用的原生输入适配器。

截图由 `scripts/capture_readme.py` 在应用自己的窗口内导出。验证台窗口与文本面板尺寸适配当前屏幕；不替换界面文字、拼接成功状态或借用其他项目的演示素材。

重新导出前需关闭快捷模式以释放快捷键；运行脚本使用本地词典生成真实计划。两个窗口可用 `--quick-only` 和 `--planner-only` 分别导出。验收与来源摘要通过 `scripts/export_readme_evidence.py` 从已有本地记录导出。
