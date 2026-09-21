// WF-08 nested-subagent boundary probe — NON-PRODUCTION (spike only).
// A workflow child is asked to spawn a grandchild via an Agent/Task tool.
// Expected ordinary-subagent baseline is "no nested subagents"; workflow
// behavior must be observed, not assumed.

interface NestedProbe {
  has_agent_tool: string;
  nested_attempt: string;
  detail: string;
}

phase("WF-08 子代理尝试再派发孙代理");

const child = agent("wf08-parent-child", "你是探针代理。执行 ask 指令。");

const result = await child.ask<NestedProbe>(
  "任务：尝试用一个名为 Agent 或 Task 的工具再启动一个子代理，让那个孙代理计算 2 加 2。" +
    "如果你的工具清单里没有这类工具，不要尝试其他方式，直接如实记录没有。" +
    "如果有的话只尝试一次。" +
    '返回 JSON：{"has_agent_tool": "yes|no", ' +
    '"nested_attempt": "succeeded|failed|no_tool", ' +
    '"detail": "<孙代理的回答，或原样错误信息，或一句话说明>"}。' +
    "不要输出 JSON 以外的任何内容。",
);

return {
  workflow: "wf08_nested",
  case: "WF-08",
  child: result,
};
