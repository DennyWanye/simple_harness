// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * 桌宠可选形象清单 — 设置面板「桌宠形象」下拉读这里。
 *
 * post-Live2D：形象由 sprite 引擎渲染（components/petCharacter.ts）。
 * 内置形象是 100% 原创的程序化角色；把透明背景立绘 PNG 放到
 * public/assets/pet/character.png 即自动替换为立绘渲染路径。
 *
 * modelPath 保留字段形状（App/SettingsPanel 依赖），sprite 引擎当前
 * 忽略它；未来 'mesh' 后端加载 .dpet 模型时复用此字段。
 */
export interface PetModel {
  /** 稳定 id，用于 localStorage 持久化 + PetCanvas remount key。 */
  readonly id: string;
  /** 下拉框显示名。 */
  readonly name: string;
  /** 模型资源路径（sprite 引擎忽略；预留给未来 .dpet 模型）。 */
  readonly modelPath: string;
}

export const PET_MODELS: readonly PetModel[] = [
  {
    id: "sprite-default",
    name: "内置角色（Sprite）",
    modelPath: "",
  },
];

export const DEFAULT_PET_MODEL_ID = "sprite-default";

/** localStorage key — 记住用户上次选的形象。 */
export const PET_MODEL_LS_KEY = "deskpet_pet_model_id";

/** 按 id 在给定清单里找模型；找不到回退第一个（清单非空）。 */
export function resolvePetModel(
  models: readonly PetModel[],
  id: string | null | undefined,
): PetModel {
  return models.find((m) => m.id === id) ?? models[0] ?? PET_MODELS[0];
}

/**
 * 动态获取可用模型清单。sprite 时代只有内置清单；保留 async 形状
 * （App 的调用方是 await），未来 .dpet 模型目录扫描接回这里。
 */
export async function fetchPetModels(): Promise<PetModel[]> {
  return [...PET_MODELS];
}
