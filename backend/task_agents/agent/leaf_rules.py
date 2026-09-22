"""子 Agent 公共约束

背景（踩过的坑）：小模型（如 qwen3-vl-flash）在子 Agent 内部会误把**子 Agent 名**
当成工具名调用（例如 `coder(...)`、`architect(...)`），或再次调用 `task` 工具
委派给自己。结果是「报错 → 换个说法再调一次同名工具」的死循环：一条请求能空转
上千步、烧掉数十万 token，最后往往以模型侧限流/鉴权失败告终。

因此从两端堵：
1. 主 Agent 侧：在各自 prompts.py 里写明「委派只能用 task 工具 + subagent_type」。
2. 子 Agent 侧：本模块这段约束，禁用 task、列明真实工具清单、禁止原样重试。

用法：各 engine 的 agent.py 在加载 prompt.md 后追加本常量。
"""

LEAF_AGENT_RULES = """

# 执行约束（叶子 Agent，必须遵守）

- 你是终端执行者：**不要再调用 `task` 工具**去委派给自己或别的 Agent，直接动手做完。
- 你可用的工具只有：`ls` / `read_file` / `write_file` / `edit_file` / `delete` /
  `glob` / `grep` / `execute`。**子 Agent 的名字不是工具名**：
  调用 `coder(...)`、`architect(...)`、`analyst(...)` 这类东西一定会报错。
- 工具报错时换一种方式达成目标，**不要原样重试同一个失败调用**超过 1 次。
- 反复失败就把已确认的事实与失败原因如实返回给主控，不要无限尝试。

# 沙箱用法（重要）

- **路径**：`/` 就是工作区根目录。写 `report.py` 或 `/report.py` 都是工作区下的文件，
  不要写成 `/workspace/report.py`（那是工作区里的 workspace 子目录，不存在）。
- **execute 只能跑 Python**：`python script.py` / `python -c "..."` / 直接给代码，
  三选一。`ls`、`cat`、`wc`、`curl`、`pip install` 这类 shell 命令会被拒绝，
  别浪费轮次去试。
- **数据文件不要逐行读**：CSV / Excel / JSON 请用 `execute` 跑 pandas 一次性处理
  （`pd.read_csv` 等），不要反复 `read_file` 分段看——又慢又容易漏行。
- 缺少第三方库时如实说明，不要尝试安装。
"""

__all__ = ["LEAF_AGENT_RULES"]
