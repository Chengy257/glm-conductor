// WF-01 minimal deterministic workflow probe — NON-PRODUCTION (spike only).
// Establishes the base callable model: one child agent, one deterministic
// task, typed result returned to the workflow script.

interface Wf01Result {
  product: number;
  formula: string;
  note: string;
}

phase("WF-01 启动单个确定性子代理");

const child = agent(
  "wf01-probe",
  "你是 WF-01 探针子代理。只做被告知的确定性计算，不读写文件，不运行命令。最后只返回约定的 JSON。",
);

const result = await child.ask<Wf01Result>(
  "任务：计算 21 乘以 2。" +
    '完成后只返回一个 JSON 对象，形如 {"product": <数字>, "formula": "21*2", "note": "<一句话说明你是被 workflow 启动的子代理>"}。' +
    "不要输出 JSON 以外的任何内容。",
);

return {
  workflow: "wf01_minimal",
  case: "WF-01",
  child_name: "wf01-probe",
  result,
  result_type: typeof result,
};
