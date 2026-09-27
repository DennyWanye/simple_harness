// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/** 「完成后发布文件」的纯函数（见 PublishCriterionHelper）。 */

const OPERATION_WORDS = /发布|上传|发送|部署|推送|上线|发到|寄出|提交到|publish|upload|deploy/i;

export function publishCriterion(fileName: string): string {
  return `action:file_publish.publish:${fileName.trim().replace(/^\/+/, "")}`;
}

/** 要求里提到发布却没有 action: 行（普通中文写的发布要求走不通）。 */
export function plainPublishMention(criteria: string): boolean {
  const lines = criteria.split("\n").map((line) => line.trim()).filter(Boolean);
  return !lines.some((line) => line.startsWith("action:")) && lines.some((line) => OPERATION_WORDS.test(line));
}
