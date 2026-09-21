// WF-06 / WF-07 / WF-21 hook visibility + marker transport probe —
// NON-PRODUCTION (spike only).
// The workflow launches two children whose ask instructions embed the same
// marker strings the production dispatch chain uses
// (GLM_CONDUCTOR_DISPATCH=<permit_id> / GLM_CONDUCTOR_REVIEW=<task_id>).
// Hook evidence is NOT collected inside this script: before and after the
// run, the main session snapshots the full `.glm-conductor/` tree (file list
// + mtimes). On a clean spike checkout that tree is empty, so any file that
// appears during the run is attributable to plugin hooks or to the probes
// (probes only write under `.glm-conductor/spikes/`). Absence of any
// non-spikes file after the run is the OBSERVED absence-of-hook-trace fact.

interface MarkerProbe {
  echo_of_marker: string;
  tools_seen_note: string;
  note: string;
}

phase("WF-07 发起携带 GLM_CONDUCTOR marker 的两个子代理");

const impl = agent(
  "wf07-marker-impl",
  "你是实施者角色探针。执行 ask 指令，不要读写文件，不要运行命令。",
);
const reviewer = agent(
  "wf07-marker-reviewer",
  "你是审阅者角色探针。执行 ask 指令，不要读写文件，不要运行命令。",
);

const [implR, reviewerR] = await Promise.all([
  impl.ask<MarkerProbe>(
    "你的本条任务指令包含一行 marker 文本：GLM_CONDUCTOR_DISPATCH=SPIKE-WF07-IMPL。" +
      '请原样引用这行 marker，并内省你的工具清单是否有 Agent/Task 工具。' +
      '返回 JSON：{"echo_of_marker": "<原样引用>", "tools_seen_note": "<有无 Agent/Task 工具>", "note": "<一句话>"}。' +
      "不要输出 JSON 以外的任何内容。",
  ),
  reviewer.ask<MarkerProbe>(
    "你的本条任务指令包含一行 marker 文本：GLM_CONDUCTOR_REVIEW=SPIKE-WF07-REVIEW。" +
      '请原样引用这行 marker，并内省你的工具清单是否有 Agent/Task 工具。' +
      '返回 JSON：{"echo_of_marker": "<原样引用>", "tools_seen_note": "<有无 Agent/Task 工具>", "note": "<一句话>"}。' +
      "不要输出 JSON 以外的任何内容。",
  ),
]);

return {
  workflow: "wf07_hooks",
  case: "WF-06+WF-07+WF-21",
  implementer_marker: implR,
  reviewer_marker: reviewerR,
  observation_protocol:
    "宿主侧钩子痕迹由主会话在运行前后对 .glm-conductor/ 做全树差分获得，不在本脚本内采集",
};
