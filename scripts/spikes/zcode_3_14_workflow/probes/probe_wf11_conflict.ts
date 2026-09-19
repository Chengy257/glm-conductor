// WF-11 conflicting parallel writes probe — NON-PRODUCTION (spike only).
// Two children concurrently write incompatible single-line contents to the
// SAME fixture file inside the ignored evidence tree. A read-only third
// child then reports the final on-disk content. This establishes whether the
// host serializes, detects, or silently last-writer-wins conflicting writes.

interface WriterResult {
  content_written: string;
  write_time: string;
  note: string;
}

interface Readback {
  final_content: string;
  stat_output: string;
}

phase("WF-11 两子代理并行冲突写同一文件");

const dir = ".glm-conductor/spikes/zcode-3.14-workflow/wf11";
const target = dir + "/shared_conflict.md";

const alpha = agent("wf11-alpha", "探针Alpha。执行 ask 指令。");
const beta = agent("wf11-beta", "探针Beta。执行 ask 指令。");

const [ra, rb] = await Promise.all([
  alpha.ask<WriterResult>(
    "任务：用 Bash 运行 mkdir -p " + dir + "；用 Bash 运行 date +%s.%N 记录时间；" +
      "再用 Write 工具把恰好一行 ALPHA-CONTENT-<刚才的时间戳> 写入 " + target +
      "（整文件只有这一行）。" +
      '返回 JSON：{"content_written": "<原样一行>", "write_time": "<原样时间戳>", "note": "<一句话>"}。只返回 JSON。',
  ),
  beta.ask<WriterResult>(
    "任务：用 Bash 运行 mkdir -p " + dir + "；用 Bash 运行 date +%s.%N 记录时间；" +
      "再用 Write 工具把恰好一行 BETA-CONTENT-<刚才的时间戳> 写入 " + target +
      "（整文件只有这一行）。" +
      '返回 JSON：{"content_written": "<原样一行>", "write_time": "<原样时间戳>", "note": "<一句话>"}。只返回 JSON。',
  ),
]);

phase("WF-11 只读子代理回读冲突文件的最终状态");

const reader = agent("wf11-reader", "只读回读探针。");
const rr = await reader.ask<Readback>(
  "任务：用 Read 工具读取 " + target + " 的当前完整内容；" +
    "再用 Bash 运行 ls -l --time-style=full-iso " + target + " 记录输出（若无 stat/ls 选项可用则用 ls -l " + target + "）。" +
    '返回 JSON：{"final_content": "<文件当前原样内容>", "stat_output": "<原样>"}。只返回 JSON。',
);

return {
  workflow: "wf11_conflict",
  case: "WF-11",
  alpha: ra,
  beta: rb,
  final: rr,
};
