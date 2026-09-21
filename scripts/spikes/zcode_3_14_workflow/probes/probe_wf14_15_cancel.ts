// WF-14 / WF-15 / WF-17 background + cancel + resume probe — NON-PRODUCTION.
// Three children sleep for staggered durations (5s / 20s / 40s) and each
// writes a timestamped marker file into the ignored evidence tree. The main
// session drives the lifecycle OUTSIDE this script:
//   1. submit run, let it reach the running state;
//   2. ~15s in, stop the run (TaskStop) while s2 is mid-flight and s3 pending;
//   3. inspect status + evidence dir (s1 finished, s2/s3 partial or absent);
//   4. resume the run; s1's completed ask replays from cache (no new file
//      mtime), s2/s3 re-dispatch live (fresh timestamps);
//   5. compare file mtimes to classify cached replay vs live re-execution.

interface SleeperResult {
  name: string;
  sleep_seconds: string;
  start_epoch: string;
  end_epoch: string;
  wrote_at: string;
}

phase("WF-14 三个错峰休眠子代理并行执行（供后台观测与中途取消）");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf14";

const mkInstr = (name: string, seconds: string): string =>
  "任务：先用 Bash 运行 date +%s.%N 记录 start_epoch；用 Bash 运行 sleep " + seconds +
  "；用 Bash 运行 mkdir -p " + dir + "；再用 Write 工具把一行 " + name + "-WROTE-" +
  "<再运行一次 date +%s.%N 的输出> 写入 " + dir + "/" + name + ".txt ；" +
  "最后再运行一次 date +%s.%N 记录 end_epoch。" +
  '返回 JSON：{"name": "' + name + '", "sleep_seconds": "' + seconds + '", ' +
  '"start_epoch": "<原样>", "end_epoch": "<原样>", "wrote_at": "<写入文件时的时间戳>"}。' +
  "只返回 JSON。";

const s1 = agent("wf14-s1", "休眠探针1。执行 ask 指令。");
const s2 = agent("wf14-s2", "休眠探针2。执行 ask 指令。");
const s3 = agent("wf14-s3", "休眠探针3。执行 ask 指令。");

const [r1, r2, r3] = await Promise.all([
  s1.ask<SleeperResult>(mkInstr("s1", "5")),
  s2.ask<SleeperResult>(mkInstr("s2", "20")),
  s3.ask<SleeperResult>(mkInstr("s3", "40")),
]);

log("三个休眠子代理全部完成");

return {
  workflow: "wf14_15_cancel",
  case: "WF-14+WF-15+WF-17",
  sleepers: [r1, r2, r3],
};
