// WF-12 / WF-13 staged dependency + result handoff probe — NON-PRODUCTION.
// Stage A computes a deterministic artifact and persists it under the ignored
// evidence tree; stage B starts only after A resolves and consumes the
// artifact from the repository. The workflow's final return aggregates both
// stages, which is what the initiating main session receives.

interface StageA {
  squares: number[];
  artifact_path: string;
  note: string;
}

interface StageB {
  consumed: string;
  doubled: number[];
  note: string;
}

phase("WF-12 阶段A：产出确定性结果并落盘");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf12";
const artifact = dir + "/squares.json";

const a = agent("wf12-stage-a", "阶段A探针。执行 ask 指令。");

const ra = await a.ask<StageA>(
  "任务：计算 [1,2,3,4,5] 每个元素的平方，得到如 [1,4,9,16,25] 的数组；" +
    "用 Bash 运行 mkdir -p " + dir + "；再用 Write 工具把该 JSON 数组原样写入 " + artifact +
    "（文件内容就是 JSON 数组本身）。" +
    '返回 JSON：{"squares": <该数组>, "artifact_path": "' + artifact + '", "note": "<一句话>"}。' +
    "只返回 JSON。",
);

phase("WF-12 阶段B：依赖阶段A产物并加工");

const b = agent("wf12-stage-b", "阶段B探针。执行 ask 指令。");

const rb = await b.ask<StageB>(
  "任务：用 Read 工具读取 " + artifact + " 的内容；把其中每个数值乘 2 得到新数组；" +
    '返回 JSON：{"consumed": "<读到的原样内容>", "doubled": <新数组>, "note": "<一句话说明你读到了阶段A的产物>"}。' +
    "只返回 JSON。",
);

return {
  workflow: "wf12_13_staged",
  case: "WF-12+WF-13",
  stage_a: ra,
  stage_b: rb,
  handoff_note:
    "阶段B通过仓库文件消费阶段A产物；最终 return 聚合两阶段结构并回传主会话",
};
