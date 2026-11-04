/**
 * 布置数据。
 *
 * 只有"被测链路本身"才用 UI 走一遍（登录、建库对话框、上传面板、检索、问答）；
 * 其余依赖（比如检索用例需要库里先有文档）走 API 布置——快十倍，而且不会因为
 * 上传面板的文案改动让检索用例连带失败。
 */

import { createKnowledgeBase, seedKbWithDocument, type AuthContext } from "./api";
import { POWER_MODULE_TEXT, UPLOAD_FILENAME } from "./fixtures";
import { readState } from "./state";

/** 把 global-setup 存下的令牌包成 `api.ts` 要的形状。 */
export function authContext(): AuthContext {
  const state = readState();
  return { token: state.token, workspaceId: state.workspaceId };
}

/** 建一个空知识库，返回 kb_id。 */
export async function seedEmptyKb(name: string): Promise<string> {
  const kb = await createKnowledgeBase(authContext(), name);
  return kb.kb_id;
}

/** 建库 + 传一份可检索的文档 + 等作业成功。 */
export async function seedKbWithDoc(name: string): Promise<{ kbId: string; documentId: string }> {
  return seedKbWithDocument(authContext(), name, UPLOAD_FILENAME, POWER_MODULE_TEXT);
}
