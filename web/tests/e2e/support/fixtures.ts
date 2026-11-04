/**
 * 用例共用的资料与提问。
 *
 * 这四行文字不是随手编的：它们同时满足两条硬约束，换掉任何一个词都会让问答用例
 * 掉进别的分支——
 * 1. **能被检索到**：问句与片段有足够的 CJK 重叠二元组（电源/源接/接反），
 *    baseline 策略才排得出结果；
 * 2. **能过证据门控**：`services/rag/evidence.py` 的 SUPPORTED 需要问句锚点全部被
 *    片段覆盖；问句没有数字锚点、片段里有明确结论，才会走到"调模型并流式输出"，
 *    而不是 refusal 分支。
 *
 * 另外 `Buffer` 只能在这里造：上传时用 `{ name, mimeType, buffer }` 直接喂给
 * `setInputFiles`，避免依赖"测试进程的工作目录"。
 */

/** 上传用的文件名；`DocumentTable` 与作业轮询都按它断言。 */
export const UPLOAD_FILENAME = "power-module.txt";

/** 一段自带结论的说明文；内容与后端契约无关，只为触发检索与门控。 */
export const POWER_MODULE_TEXT = [
  "电源模块使用说明",
  "",
  "型号：PX-200。",
  "",
  "电源接反会导致保险丝熔断。此时请先断开电源，再更换同规格保险丝，不得用铜丝短接。",
  "",
  "安装时必须确保接地线可靠连接，接地电阻应小于 4 欧姆。",
  "",
  "设备工作温度为 0 到 40 摄氏度，相对湿度不超过 80%。",
  "",
].join("\n");

/** 检索与问答共用的问句：与片段有 3 个以上重叠二元组。 */
export const SUPPORTED_QUESTION = "电源接反会怎样？";

/** `setInputFiles` 的入参：不经过磁盘，也就不依赖 cwd。 */
export const UPLOAD_FILE = {
  name: UPLOAD_FILENAME,
  mimeType: "text/plain",
  buffer: Buffer.from(POWER_MODULE_TEXT, "utf8"),
} as const;
