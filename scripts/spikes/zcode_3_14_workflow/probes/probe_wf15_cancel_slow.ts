// WF-15 / WF-17 cancel-while-running + cancel-before-start + resume probe —
// NON-PRODUCTION. Two children total: "long" sleeps 90s then writes a
// timestamped file; "late" runs in a SECOND phase (never dispatched before
// the first resolves). The main session stops the run ~35s in: "long" is
// mid-flight (file unwritten), "late" was never started. Then
// ResumeWorkflowRun: "long" re-dispatches live (fresh timestamps), "late"
// starts for the first time. File mtimes + run/actor ids classify rerun vs
// replay.

interface SleeperResult {
  name: string;
  start_epoch: string;
  end_epoch: string;
  wrote_at: string;
}

interface LateResult {
  name: string;
  ran_at: string;
  note: string;
}

phase("WF-15 长眠子代理执行中（90 秒）");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf15";

const long = agent("wf15-long", "休眠探针。执行 ask 指令。");

const longR = await long.ask<SleeperResult>(
  "任务：先用 Bash 运行 date +%s.%N 记录 start_epoch；用 Bash 运行 sleep 90；" +
    "用 Bash 运行 mkdir -p " + dir + "；再用 Write 工具把一行 long-WROTE-<date +%s.%N 输出> 写入 " +
    dir + "/long.txt ；最后运行 date +%s.%N 记录 end_epoch。" +
    '返回 JSON：{"name": "long", "start_epoch": "<原样>", "end_epoch": "<原样>", "wrote_at": "<写入时的时间戳>"}。' +
    "只返回 JSON。",
);

phase("WF-15 第二阶段子代理（取消发生时应尚未启动）");

const late = agent("wf15-late", "迟到探针。执行 ask 指令。");

const lateR = await late.ask<LateResult>(
  "任务：用 Bash 运行 date +%s.%N 作为你的运行时刻。" +
    '返回 JSON：{"name": "late", "ran_at": "<原样>", "note": "<一句话说明你是恢复后才首次运行的>"}。' +
    "只返回 JSON。",
);

log("两个阶段全部完成");

return {
  workflow: "wf15_cancel_slow",
  case: "WF-15+WF-17",
  long: longR,
  late: lateR,
};
