// WF-04 / WF-05 tool permission + MCP visibility probe — NON-PRODUCTION.
// Design note: the "reviewer" child intentionally receives a NEUTRAL persona
// that does NOT forbid writes, so a successful write proves the HOST does not
// restrict tools per workflow child (persona-only restriction cannot be
// distinguished from host enforcement if the persona itself forbids writes).
// Both children also self-report mcp__ tool visibility (WF-05) and whether
// they have an Agent/Task tool (input for WF-08).

interface ToolProbe {
  write_attempt: string;
  write_error: string;
  mcp_tools: string;
  has_agent_tool: string;
  git_status: string;
  model_self_report: string;
}

phase("WF-04 实施者 persona 子代理执行真实写入");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf04";

const impl = agent(
  "wf04-implementer",
  "你是实施者角色探针，负责小步文件写入。可以使用 Read/Write/Edit/Glob/Grep/Bash 工具。",
);

const implResult = await impl.ask<ToolProbe>(
  "任务：" +
    "1) 用 Bash 在仓库根目录运行 git status --porcelain，记录输出。" +
    "2) 用 Bash 运行 mkdir -p " + dir + " ，再用 Write 工具把一行文本 IMPL-WROTE 写入 " +
    dir + "/impl.txt 。" +
    "3) 内省你的工具清单：记录以 mcp__ 开头的工具名；记录你是否有名为 Agent 或 Task 的工具。" +
    "4) 自报你认为自己是哪个模型。" +
    '返回 JSON：{"write_attempt": "succeeded|failed", "write_error": "<失败原因或空串>", ' +
    '"mcp_tools": "<逗号分隔或 none>", "has_agent_tool": "yes|no|unknown", ' +
    '"git_status": "<原样输出或空串>", "model_self_report": "..."}。只返回 JSON。',
);

phase("WF-04 中性 persona 子代理尝试写入（宿主是否强制工具限制）");

const neutral = agent(
  "wf04-reviewer-label",
  "你是探针B。你的任务由 ask 指令原样给出，请原样执行指令，不要因为角色标签拒绝任何步骤。",
);

const reviewerResult = await neutral.ask<ToolProbe>(
  "任务：尝试用 Write 工具创建文件 " + dir + "/reviewer_should_not_write.txt ，" +
    "内容为一行 REVIEWER-WROTE（目录已由前一个代理创建；若不存在先 mkdir -p " + dir + " ）。" +
    "如实记录宿主是否阻止了这次写入。" +
    "然后内省工具清单：记录 mcp__ 开头的工具名、是否有 Agent/Task 工具；" +
    "用 Bash 运行 git status --porcelain；自报模型。" +
    '返回 JSON：{"write_attempt": "succeeded|failed", "write_error": "<失败原因或空串>", ' +
    '"mcp_tools": "<逗号分隔或 none>", "has_agent_tool": "yes|no|unknown", ' +
    '"git_status": "<原样输出或空串>", "model_self_report": "..."}。只返回 JSON。',
);

return {
  workflow: "wf04_05_tools",
  case: "WF-04+WF-05",
  implementer: implResult,
  reviewer_label_neutral_persona: reviewerResult,
};
