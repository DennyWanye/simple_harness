// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  AnimationOverlay,
  type CoreModelLike,
  type InteractionKind,
  type MotionTag,
} from "../pet-anim";
import { get_calibrated_motion_pools } from "../pet-state/PetStateMachine";
import { createPetEngine, resolveBackendFromEnv, type PetEngine } from "../pet-engine";
import {
  drawProceduralCharacter,
  drawSpriteCharacter,
  type CharacterFrame,
} from "./petCharacter";
import { deriveTransform } from "./petTransform";

interface PetCanvasProps {
  modelPath: string;
  onFpsUpdate?: (fps: number) => void;
  mouthOpenY?: number; // 0.0 ~ 1.0, drives ParamMouthOpenY for lip sync
  /** 角色画布的逻辑宽度（CSS px）。消息页已经是独立窗口，因此角色
   * 画布始终在桌宠主窗口中居中；不传时使用整个主窗口宽度。 */
  petWidth?: number;
}

/**
 * Imperative handle for driving Live2D state from parent components.
 *
 * v3 (2026-05-24) Pet Animation UX additions per PRD §6.1:
 *   - setMotionTagPool(tags, opts, now_t) — drive motion choice by tag
 *   - setGazeTarget / clearGazeTarget — manual gaze override (App rarely
 *     uses; we wire window-level pointermove inside the canvas instead)
 *   - pulseInteraction(kind) — emit synthetic pointer reactions for tests
 *   - getAnimationMetrics / getAnimationDebug — expose perf + state to
 *     ManualTest CASE-MET / CASE-G / CASE-MP via window globals
 */
export interface PetCanvasHandle {
  /** Apply a named expression. Silently no-ops if unknown/unloaded. */
  setExpression: (name: string) => void;
  /** Trigger a motion group. Silently no-ops if unknown/unloaded. */
  playMotion: (group: string) => void;
  /** P5-S3: eye-blink frequency Hz overlaid on base motion. */
  setBlinkRate: (hz: number) => void;
  /** P5-S3: persistent head tilt (degrees). */
  setHeadTilt: (degrees: number) => void;
  /** P5-S3: advisory hint for Idle motion subset. */
  setIdleSubset: (motionIds: string[]) => void;
  /** v3 PRD FR-5: drive motion by tag pool. */
  setMotionTagPool: (
    tags: MotionTag[],
    opts: { force_switch_now: boolean },
    now_t: number,
  ) => void;
  /** v3 PRD FR-4 (rarely-needed manual override). */
  setGazeTarget: (clientX: number, clientY: number, now_t: number) => void;
  clearGazeTarget: (now_t: number) => void;
  /** v3 PRD FR-6 (test/synthetic). */
  pulseInteraction: (kind: InteractionKind) => void;
  /** v3 PRD FR-7. */
  getAnimationMetrics: () => ReturnType<AnimationOverlay["getAnimationMetrics"]>;
  /** v3 PRD §6.1 debug surface. */
  getAnimationDebug: () => ReturnType<AnimationOverlay["getAnimationDebug"]>;
  /** v2 PRD §6.1: A1 drag state machine input. */
  setDragState: (state: "idle" | "being_held", now_t: number) => void;
  /** v2 PRD §6.1: B1 user-input observer wiring. */
  setUserInputActive: (active: boolean, now_t: number) => void;
  /** v2 PRD §6.1: B2 thinking observer wiring. */
  setThinkingActive: (active: boolean, now_t: number) => void;
  /** v2 PRD §6.1: B4 deterministic mouth fade. */
  fadeMouthToZero: (duration_ms: number, now_t: number) => void;
  /** v2 PRD §6.1: B4 800ms silence-timeout fallback (M-4). */
  armMouthFadeTimeout: (silence_timeout_ms: number, now_t: number) => void;
  /** v2 PRD §6.1: cancel pending/in-flight mouth fade (new viseme arrived). */
  cancelMouthFade: () => void;
  /** v2 PRD §6.1: B3 main path — push a single viseme frame. */
  setVisemeFrame: (frame: import("../pet-anim/visemeLipsync").VisemeFrame) => void;
  /** v2 PRD §6.1: B3 fallback — bulk-load estimated viseme stream. */
  setPhonemeEstimatorReady: (
    stream: import("../pet-anim/visemeLipsync").VisemeFrame[],
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: flush viseme queue on tts_end. */
  flushVisemeQueue: () => void;
  /** v2 PRD §6.1: D1 emotion lock setter. */
  setEmotion: (
    emotion: import("../pet-anim/emotionMapper").EmotionCode,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: C1 low_energy state. */
  setLowEnergy: (active: boolean, now_t: number) => void;
  /** v2 PRD §6.1: C2 welcome pulse trigger. */
  triggerWelcome: (
    intensity: import("../pet-anim/idleWatcher").WelcomeIntensity,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: E1 edge attached state. */
  setEdgeAttached: (
    edge: import("../pet-anim/edgeWatcher").Edge,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: F1 DND state with reasons. */
  setDNDActive: (
    active: boolean,
    reasons: import("../pet-anim/dndDetector").DNDReason[],
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: AC-10-03 — red supervisor severity (DND must not suppress). */
  setRedAlertActive: (active: boolean) => void;
  /** v2 PRD §6.1: C3 / D2 celebration trigger (3s happy_intense + TapBody). */
  triggerCelebration: (
    kind: "hourly" | "anniversary" | "milestone",
    message: string,
    now_t: number,
  ) => void;
  /** v2 PRD §6.1: full v2 debug surface. */
  getV2Debug: () => ReturnType<AnimationOverlay["getV2Debug"]>;
  /** 2026-05-31 fun: pointer down on the pet (begins drag/longPress/burst). */
  funPointerDown: (clientX: number, clientY: number, now_t: number) => void;
  /** 2026-05-31 fun: pointer move during hold (drag kinematics). */
  funPointerMove: (clientX: number, clientY: number, now_t: number) => void;
  /** 2026-05-31 fun: pointer up — ends drag, returns burst classification. */
  funPointerUp: (now_t: number) => {
    burst_count: number;
    burst_intensity: import("../pet-anim/funInteractions").TapBurstIntensity;
    double_tap: boolean;
  };
  /** 2026-05-31 fun: any user activity (chat input / app focus / etc.). */
  funMarkInteraction: (now_t: number) => void;
}

interface FaceFrame {
  left: number;
  top: number;
  width: number;
  height: number;
  face_center_x: number;
  face_center_y: number;
  face_radius_css: number;
}

/**
 * Compute hit-zone bounding box + face centre + radius from window
 * geometry + pet rendering width. PRD §6.0 v3 single source of truth —
 * the same values feed both the <div data-pet-hitzone> style and
 * overlay.setFaceCenter so they never drift.
 */
function computeFaceFrame(
  petWidth: number,
  innerHeight: number,
  innerWidth: number,
  modelScaleFactor = 1,
): FaceFrame {
  // 2026-05-31 fun-ux: 扩大 hit-zone 覆盖整个角色可见区（头顶→脚），
  // 之前只覆盖脸+躯干中间 50%×60% 的窄带，用户点裙子/腿/头发/手臂都没
  // 反应。现在覆盖角色整列宽 × 几乎全高，点哪都能触发交互。
  // face_center 仍锁在脸部（用于 gaze 凝视 + proximity/shy/dizzy 计算）。
  // 角色画布固定为 petWidth，但主窗口可能比角色画布更宽。把画布放在
  // 窗口正中，而不是继续沿用旧版“贴右侧角色列”的位置。
  const left = Math.max(0, (innerWidth - petWidth) / 2);
  const width = Math.max(40, petWidth * modelScaleFactor);
  const top = innerHeight * 0.05;
  const height = Math.max(40, innerHeight * 0.9 * modelScaleFactor);
  return {
    left,
    top,
    width,
    height,
    // 脸约在角色立绘的上部 ~22% 处（Hiyori 全身站姿）。
    face_center_x: left + width / 2,
    face_center_y: top + height * 0.22,
    // face_radius 用角色宽的一半（脸 + 周边的合理凝视/害羞判定半径）。
    face_radius_css: Math.max(60, width * 0.5),
  };
}

/**
 * Pet character rendered via the sprite engine (Canvas2D) → <img> tag.
 *
 * WebView2 transparent windows don't composite <canvas>/<WebGL>.
 * We render offscreen and display each frame via <img> (HTML = composites OK).
 *
 * v3 (2026-05-24) — wires AnimationOverlay for FR-1~FR-7:
 *   - Render loop calls overlay.applyTo(coreModel, timestamp) instead of
 *     hand-rolled blink/tilt code
 *   - window-level pointermove → overlay.setGazeTarget (PRD §6.0)
 *   - <div data-pet-hitzone> covers face+torso → overlay.pulseInteraction
 *   - ResizeObserver + window resize keep face_center synced (PRD §6.0)
 *   - toBlob callback records visual_latency (PRD §6.8 FIFO pairing)
 */
export const PetCanvas = forwardRef<PetCanvasHandle, PetCanvasProps>(function PetCanvas(
  // modelPath 留在 props 接口里（App 传入、未来 .dpet 模型消费），
  // sprite 引擎当前不读取，故不解构。
  { onFpsUpdate, mouthOpenY = 0, petWidth },
  ref,
) {
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const hitZoneRef = useRef<HTMLDivElement>(null);
  // FIX-R3: tracks pointerdown position for manual drag detection on
  // hit-zone. We avoid `data-tauri-drag-region` because it eats click.
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  // FIX-R3 (round-3 retest): startDragging MUST be called synchronously
  // while the mouse button is still pressed. `await import()` adds enough
  // latency that the user releases the button first → SendMessage
  // WM_NCLBUTTONDOWN is a no-op. We pre-load on mount and cache the
  // bound function so the threshold-trigger call is synchronous.
  const startDraggingRef = useRef<(() => Promise<unknown>) | null>(null);
  const [size, setSize] = useState(() => ({
    // 跨 DPI 裁切修复：列宽 cap 在视口内（详见下方 apply 注释）。
    w: Math.min(petWidth ?? window.innerWidth, window.innerWidth),
    h: window.innerHeight,
    // 2026-06-03 跨 DPI 修复：把 devicePixelRatio 纳入 size 状态，使画布 resize
    // effect 能在「拖到不同缩放显示器(逻辑尺寸不变但 dpr 变)」时重跑。
    dpr: window.devicePixelRatio || 1,
  }));
  const cleanupRef = useRef<(() => void) | null>(null);
  // Latest viewport — read by the render loop each frame (avoids the
  // mount-time capture that used to require a separate resize effect).
  const sizeRef = useRef(size);
  sizeRef.current = size;
  // Pet engine instance (created in the main effect below).
  const engineRef = useRef<PetEngine | null>(null);
  const mouthRef = useRef(mouthOpenY);
  mouthRef.current = mouthOpenY;
  // v3: AnimationOverlay instance — per PetCanvas mount. dispose()
  // called from cleanupRef so HMR / StrictMode double-mount can't leak.
  const overlayRef = useRef<AnimationOverlay | null>(null);
  // v3: face frame state. Updated by ResizeObserver + window resize + once
  // on model load. Drives both hit-zone DOM and overlay.setFaceCenter via
  // a single source `computeFaceFrame`.
  const faceFrameRef = useRef<FaceFrame>(
    computeFaceFrame(petWidth ?? window.innerWidth, window.innerHeight, window.innerWidth),
  );

  // Construct overlay once. We construct it eagerly so imperative handle
  // methods can no-op-write into it even before the Live2D model loads.
  if (overlayRef.current === null) {
    overlayRef.current = new AnimationOverlay({
      motionLabelsLoader: get_calibrated_motion_pools as () =>
        | Record<MotionTag, number[]>
        | null,
    });
  }

  useImperativeHandle(
    ref,
    () => ({
      setExpression(name: string) {
        // Sprite backend has a single neutral expression today; the engine
        // keeps the call surface so future backends can honor it.
        engineRef.current?.setExpression(name);
      },
      playMotion(group: string) {
        engineRef.current?.playMotion(group);
      },
      setBlinkRate(hz: number) {
        overlayRef.current?.setBlinkHz(Math.max(0, Number.isFinite(hz) ? hz : 0));
      },
      setHeadTilt(degrees: number) {
        const clamped = Math.max(-15, Math.min(15, Number.isFinite(degrees) ? degrees : 0));
        overlayRef.current?.setStateBaseHeadTilt(clamped);
      },
      setIdleSubset(_motionIds: string[]) {
        // Reserved for future backends with multiple Idle groups.
        // The sprite backend has a single Idle loop, so no-op.
      },
      setMotionTagPool(tags, opts, now_t) {
        overlayRef.current?.setMotionTagPool(tags, opts, now_t);
      },
      setGazeTarget(clientX, clientY, now_t) {
        overlayRef.current?.setGazeTarget(clientX, clientY, now_t);
      },
      clearGazeTarget(now_t) {
        overlayRef.current?.clearGazeTarget(now_t);
      },
      pulseInteraction(kind) {
        overlayRef.current?.pulseInteraction(kind, performance.now());
      },
      getAnimationMetrics() {
        return (
          overlayRef.current?.getAnimationMetrics() ?? {
            interaction: { p50: 0, p95: 0, max: 0, samples: [] },
            visual: { p50: 0, p95: 0, max: 0, samples: [] },
          }
        );
      },
      getAnimationDebug() {
        return (
          overlayRef.current?.getAnimationDebug() ?? {
            gaze_target_yaw: 0,
            gaze_smoothed_yaw: 0,
            last_input_age_ms: 0,
            current_state: "rest",
            current_motion_idx: null,
          }
        );
      },
      // ───────── v2 setters ─────────
      setDragState(state, now_t) {
        overlayRef.current?.setDragState(state, now_t);
      },
      setUserInputActive(active, now_t) {
        overlayRef.current?.setUserInputActive(active, now_t);
      },
      setThinkingActive(active, now_t) {
        overlayRef.current?.setThinkingActive(active, now_t);
      },
      fadeMouthToZero(duration_ms, now_t) {
        overlayRef.current?.fadeMouthToZero(duration_ms, now_t);
      },
      armMouthFadeTimeout(silence_timeout_ms, now_t) {
        overlayRef.current?.armMouthFadeTimeout(silence_timeout_ms, now_t);
      },
      cancelMouthFade() {
        overlayRef.current?.cancelMouthFade();
      },
      setVisemeFrame(frame) {
        overlayRef.current?.setVisemeFrame(frame);
      },
      setPhonemeEstimatorReady(stream, now_t) {
        overlayRef.current?.setPhonemeEstimatorReady(stream, now_t);
      },
      flushVisemeQueue() {
        overlayRef.current?.flushVisemeQueue();
      },
      setEmotion(emotion, now_t) {
        overlayRef.current?.setEmotion(emotion, now_t);
      },
      setLowEnergy(active, now_t) {
        overlayRef.current?.setLowEnergy(active, now_t);
      },
      triggerWelcome(intensity, now_t) {
        overlayRef.current?.triggerWelcome(intensity, now_t);
      },
      setEdgeAttached(edge, now_t) {
        overlayRef.current?.setEdgeAttached(edge, now_t);
      },
      setDNDActive(active, reasons, now_t) {
        overlayRef.current?.setDNDActive(active, reasons, now_t);
      },
      setRedAlertActive(active) {
        overlayRef.current?.setRedAlertActive(active);
      },
      triggerCelebration(kind, message, now_t) {
        overlayRef.current?.triggerCelebration(kind, message, now_t);
      },
      getV2Debug() {
        return (
          overlayRef.current?.getV2Debug() ?? {
            held_state: "idle" as const,
            held_wobble_deg: 0,
            held_surprise: 0,
            user_input_active: false,
            thinking_active: false,
            mouth_fade_mode: "idle" as const,
            current_emotion: "neutral" as const,
            viseme_queue_size: 0,
            low_energy: false,
            welcome_active: false,
            welcome_intensity: "normal" as const,
            edge_attached: null,
            dnd_active: false,
            dnd_reasons: [],
            celebration_active: false,
            red_alert_active: false,
          }
        );
      },
      // 2026-05-31 fun interactions
      funPointerDown(clientX, clientY, now_t) {
        overlayRef.current?.funPointerDown(clientX, clientY, now_t);
      },
      funPointerMove(clientX, clientY, now_t) {
        overlayRef.current?.funPointerMove(clientX, clientY, now_t);
      },
      funPointerUp(now_t) {
        return (
          overlayRef.current?.funPointerUp(now_t) ?? {
            burst_count: 0,
            burst_intensity: "look_up" as const,
            double_tap: false,
          }
        );
      },
      funMarkInteraction(now_t) {
        overlayRef.current?.funMarkInteraction(now_t);
      },
    }),
    [],
  );

  // Track viewport. Also keep face frame current.
  useEffect(() => {
    const apply = (): void => {
      // 2026-06-03 跨 DPI 裁切修复：petWidth 是固定角色列宽(282)，但某些机器的
      // webview devicePixelRatio 高于显示器缩放（实测 dpr 2.13/1.42，可能叠加了
      // Windows 文本缩放 142%）→ 视口 innerWidth 仅 ~253 CSS < 282 → 角色 <img>
      // 比视口宽 → 右侧被裁、显示不全。把列宽 cap 在视口内：innerWidth≥petWidth 时
      // 仍用 petWidth(解耦不变)，否则收敛到 innerWidth → 角色完整可见(代价：略小)。
      const w = Math.min(petWidth ?? window.innerWidth, window.innerWidth);
      const h = window.innerHeight;
      const dpr = window.devicePixelRatio || 1;
      // 2026-06-03 跨 DPI 诊断：每次 viewport/dpr 变化都记一行，便于真机拖动时
      // 观察 innerWidth/dpr 随显示器切换的实际值（排查角色裁切）。
      console.warn(`[Pet] viewport: ${window.innerWidth} x ${window.innerHeight} dpr: ${dpr} petWidth: ${petWidth} size.w: ${w}`);
      setSize({ w, h, dpr });
      const ff = computeFaceFrame(w, h, window.innerWidth);
      faceFrameRef.current = ff;
      overlayRef.current?.setFaceCenter(ff.face_center_x, ff.face_center_y, ff.face_radius_css);
    };
    apply();
    let timeout: number | undefined;
    const throttled = (): void => {
      if (timeout) return;
      timeout = window.setTimeout(() => {
        timeout = undefined;
        apply();
      }, 100);
    };
    window.addEventListener("resize", throttled);
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined" && containerRef.current) {
      ro = new ResizeObserver(throttled);
      ro.observe(containerRef.current);
    }
    // 2026-06-03 跨 DPI 修复：拖到不同缩放显示器时 devicePixelRatio 变化，但窗口
    // 逻辑尺寸不变 → resize 事件不一定触发 → 画布 renderer 卡在旧 DPR → 角色被裁。
    // matchMedia(resolution) 是 DPR 变化的可靠信号；每次变化后用新 DPR 重新 arm。
    let mql: MediaQueryList | null = null;
    const onDprChange = (): void => {
      throttled();
      armDpr();
    };
    const armDpr = (): void => {
      if (typeof window.matchMedia !== "function") return;
      mql?.removeEventListener("change", onDprChange);
      mql = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      mql.addEventListener("change", onDprChange);
    };
    armDpr();
    return () => {
      window.removeEventListener("resize", throttled);
      if (timeout) window.clearTimeout(timeout);
      ro?.disconnect();
      mql?.removeEventListener("change", onDprChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [petWidth]);


  // FIX-R3: pre-load Tauri window startDragging so the manual drag
  // handler can call it synchronously during the gesture.
  useEffect(() => {
    let cancelled = false;
    import("@tauri-apps/api/window")
      .then((m) => {
        if (cancelled) return;
        try {
          const w = m.getCurrentWindow();
          startDraggingRef.current = () => w.startDragging();
        } catch {
          /* ignore — Tauri runtime unavailable (dev-browser preview) */
        }
      })
      .catch(() => {
        /* ignore */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Window-level pointermove for gaze. PRD §6.0 v3: even with
  // ignore_cursor_events=true the WebView JS still receives this
  // (Day-0 Probe-3); listener lives here so a hit-zone-bounded fallback
  // can be slotted in by flipping the addEventListener target later.
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const onMove = (e: PointerEvent): void => {
      overlay.setGazeTarget(e.clientX, e.clientY, e.timeStamp);
      // 2026-05-31 fun: feed shy-away + circle-dizzy observers.
      overlay.funCursorMove?.(e.clientX, e.clientY, e.timeStamp);
    };
    const onBlur = (): void => {
      overlay.clearGazeTarget(performance.now());
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // Expose metrics + debug + bench on window for ManualTest helpers.
  useEffect(() => {
    const w = window as unknown as Record<string, unknown>;
    w["__deskpet_anim_metrics"] = () => overlayRef.current?.getAnimationMetrics();
    w["__deskpet_anim_debug"] = overlayRef.current?.getAnimationDebug();
    // Keep debug pointer fresh — read from overlay each access via getter.
    Object.defineProperty(w, "__deskpet_anim_debug", {
      configurable: true,
      get: () => overlayRef.current?.getAnimationDebug(),
    });
    if (import.meta.env.DEV) {
      w["__deskpet_anim_bench"] = {
        applyToOnce: (t: number) => {
          const core = engineRef.current?.getCoreModel();
          if (core && overlayRef.current) {
            overlayRef.current.applyTo(core, t);
          }
        },
      };
      // v2 observability bridge for ManualTest §0.2 helpers (round-1 FAIL fix).
      // Exposes the AnimationOverlay instance + v2 debug surface to DevTools/CDP
      // so manual tests can call setEmotion / setDragState / etc. directly and
      // read getV2Debug() without going through the React tree.
      Object.defineProperty(w, "__deskpet_anim_overlay", {
        configurable: true,
        get: () => overlayRef.current,
      });
      Object.defineProperty(w, "__deskpet_anim_debug_v2", {
        configurable: true,
        get: () => overlayRef.current?.getV2Debug(),
      });
    }
    return () => {
      try {
        delete w["__deskpet_anim_metrics"];
        delete w["__deskpet_anim_debug"];
        delete w["__deskpet_anim_bench"];
        delete w["__deskpet_anim_overlay"];
        delete w["__deskpet_anim_debug_v2"];
      } catch {
        /* ignore */
      }
    };
  }, []);

  // Main init — runs once. Sprite engine + Canvas2D character renderer
  // (100% original artwork; see components/petCharacter.ts). The Live2D
  // code path was removed when this project split off from DeskPet.
  //
  // Render pipeline: offscreen <canvas> → toBlob(webp) → <img>. Kept from
  // the original implementation because WebView2 transparent windows don't
  // composite <canvas> directly (Windows); the same path works fine on
  // macOS WKWebView, so it stays the single cross-platform code path.
  useEffect(() => {
    let destroyed = false;
    let rafId = 0;

    const engine = createPetEngine(
      resolveBackendFromEnv(import.meta.env.VITE_PET_ENGINE as string | undefined),
    );
    engineRef.current = engine;

    const canvas = document.createElement("canvas");
    const ctx2d = canvas.getContext("2d");

    // Optional real artwork: drop a transparent-bg portrait at
    // /assets/pet/character.png and it replaces the procedural character.
    let spriteImg: HTMLImageElement | null = null;
    {
      const img = new Image();
      img.onload = () => { spriteImg = img; };
      img.onerror = () => { spriteImg = null; };
      img.src = "/assets/pet/character.png";
    }

    const TARGET_FPS = 30;
    const FRAME_INTERVAL = 1000 / TARGET_FPS;
    let frameCount = 0;
    let lastFpsTime = performance.now();
    let lastFrameTime = 0;
    let pendingBlob = false;
    let currentBlobUrl: string | null = null;

    function draw(timestamp: number) {
      if (destroyed || !ctx2d) return;
      const ctx = ctx2d;

      const delta = timestamp - lastFrameTime;
      if (delta >= FRAME_INTERVAL && !pendingBlob) {
        lastFrameTime = timestamp - (delta % FRAME_INTERVAL);
        frameCount++;

        const now = performance.now();
        if (now - lastFpsTime >= 1000) {
          onFpsUpdate?.(Math.round((frameCount * 1000) / (now - lastFpsTime)));
          frameCount = 0;
          lastFpsTime = now;
        }

        // Follow the live viewport each frame (window resize / DPR change) —
        // this replaces the old separate renderer-resize effect.
        const { w: width, h: height, dpr } = sizeRef.current;
        const physW = Math.round(width * dpr);
        const physH = Math.round(height * dpr);
        if (canvas.width !== physW || canvas.height !== physH) {
          canvas.width = physW;
          canvas.height = physH;
        }

        // Drive pet-anim: reset → baseline → applyTo → read back.
        let tf = { rotateDeg: 0, offsetX: 0, offsetY: 0, scaleX: 1, scaleY: 1 };
        let blink = 0;
        let mouth = Math.max(0, Math.min(1, mouthRef.current));
        const core: CoreModelLike | null = engine.getCoreModel();
        const overlay = overlayRef.current;
        if (core && overlay) {
          (core as CoreModelLike & { resetParameters?: () => void }).resetParameters?.();
          // Cubism-style baseline: eyes default OPEN (=1). Blink lands as a
          // MULTIPLY, so a zeroed baseline would leave the eyes shut forever.
          core.setParameterValueByIndex(core.getParameterIndex("ParamEyeLOpen"), 1);
          core.setParameterValueByIndex(core.getParameterIndex("ParamEyeROpen"), 1);
          overlay.setMouthOpenY(mouthRef.current);
          overlay.applyTo(core, timestamp);
          tf = deriveTransform(core);
          const eyeL = core.getParameterValueByIndex(core.getParameterIndex("ParamEyeLOpen"));
          blink = Math.max(0, Math.min(1, 1 - eyeL));
          const m = core.getParameterValueByIndex(core.getParameterIndex("ParamMouthOpenY"));
          mouth = Math.max(0, Math.min(1, m));
        }

        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);
        const frame: CharacterFrame = {
          w: width,
          h: height,
          t: timestamp,
          mouthOpen: mouth,
          blink,
          rotateDeg: tf.rotateDeg,
          offsetX: tf.offsetX,
          offsetY: tf.offsetY,
          scaleX: tf.scaleX,
          scaleY: tf.scaleY,
        };
        if (spriteImg) {
          drawSpriteCharacter(ctx, spriteImg, frame);
        } else {
          drawProceduralCharacter(ctx, frame);
        }

        if (imgRef.current) {
          pendingBlob = true;
          try {
            canvas.toBlob(
              (blob: Blob | null) => {
                pendingBlob = false;
                if (destroyed || !blob || !imgRef.current) return;
                if (currentBlobUrl) URL.revokeObjectURL(currentBlobUrl);
                currentBlobUrl = URL.createObjectURL(blob);
                imgRef.current.src = currentBlobUrl;
                // v3: record visual latency — pair this frame with the
                // oldest pending click event (FIFO per §3.8).
                overlayRef.current?.recordVisualFrameTs(performance.now());
              },
              "image/webp",
              0.8,
            );
          } catch {
            pendingBlob = false;
          }
        }
      }

      rafId = requestAnimationFrame(draw);
    }
    rafId = requestAnimationFrame(draw);

    cleanupRef.current = () => {
      destroyed = true;
      cancelAnimationFrame(rafId);
      if (currentBlobUrl) {
        try { URL.revokeObjectURL(currentBlobUrl); } catch { /* ignore */ }
      }
      engine.destroy();
      engineRef.current = null;
      // v3: dispose the overlay so HMR + StrictMode unmounts don't leak.
      overlayRef.current?.dispose();
      overlayRef.current = null;
    };

    return () => {
      cleanupRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run once only

  // hit-zone DOM (PRD §6.0). Sized in absolute CSS px from
  // computeFaceFrame so it stays in sync with overlay's face_center.
  // z-index is intentionally below HiyoriMotionTuner / SettingsPanel /
  // other UI surfaces (which all use 100+) so they remain clickable on
  // top — see CASE-PR-06.
  const ff = faceFrameRef.current;
  return (
    <>
      <div ref={containerRef} style={{ display: "none" }} />
      <img
        ref={imgRef}
        alt=""
        style={{
          width: `${size.w}px`,
          height: `${size.h}px`,
          position: "absolute",
          top: 0,
          left: "50%",
          transform: "translateX(-50%)",
          pointerEvents: "none",
        }}
      />
      <div
        ref={hitZoneRef}
        data-pet-hitzone="1"
        style={{
          position: "fixed",
          left: ff.left,
          top: ff.top,
          width: ff.width,
          height: ff.height,
          pointerEvents: "auto",
          background: "transparent",
          // FIX-R3 (2026-05-24): z-index 25→5. With z=25 the hit-zone
          // covered the upper portion of DialogBar (z:10) at the face
          // bbox's bottom edge, eating mousedown and preventing text
          // selection on assistant replies. Dropping to z:5 keeps
          // hit-zone above the pet <img> (pointer-events:none anyway)
          // but BELOW DialogBar (z:10), input bar (z:20),
          // PetDebugOverlay (z:30), Toolbar and other UI surfaces —
          // anything visible on top now wins pointer events, while the
          // empty face area still triggers hit-zone reactions.
          zIndex: 5,
        }}
        onPointerEnter={(e) => {
          overlayRef.current?.pulseInteraction("hover_enter", e.timeStamp);
        }}
        onPointerLeave={(e) => {
          overlayRef.current?.pulseInteraction("hover_leave", e.timeStamp);
        }}
        onPointerDown={(e) => {
          // FIX-R3: manual drag detection. We can't use
          // `data-tauri-drag-region` because that attribute triggers
          // Win32 WM_NCLBUTTONDOWN on mousedown — the OS then owns the
          // gesture and React's onClick never fires (so the TapBody
          // pulse is lost). Instead we record the down point and watch
          // for movement > DRAG_THRESHOLD_PX; if it exceeds, we
          // explicitly call appWindow.startDragging(). A pure
          // mousedown+up without movement falls through to onClick
          // (preserving the click pulse).
          dragStartRef.current = { x: e.clientX, y: e.clientY };
          // 2026-05-31 fun: kick off drag/longPress/burst observer.
          overlayRef.current?.funPointerDown(e.clientX, e.clientY, e.timeStamp);
        }}
        onPointerMove={(e) => {
          // 2026-05-31 fun: feed pointermove into drag kinematics (only when
          // pressed — funPointerDown sets ctx.active true; sample is no-op
          // when inactive).
          overlayRef.current?.funPointerMove(e.clientX, e.clientY, e.timeStamp);
          const start = dragStartRef.current;
          if (!start) return;
          const dx = e.clientX - start.x;
          const dy = e.clientY - start.y;
          if (dx * dx + dy * dy > 25 /* 5px threshold squared */) {
            dragStartRef.current = null;
            // v2 A1: hand the overlay into being_held so wobble + surprise fire
            // for the duration of the drag. Cleared on pointerup below.
            overlayRef.current?.setDragState("being_held", e.timeStamp);
            // Synchronous call — the cached startDragging was prepared
            // at mount, so SendMessage WM_NCLBUTTONDOWN runs before the
            // user can release the button.
            const fn = startDraggingRef.current;
            if (fn) {
              try {
                void fn();
              } catch {
                /* ignore */
              }
            }
          }
        }}
        onPointerUp={(e) => {
          dragStartRef.current = null;
          // v2 A1: tell overlay drag ended → spring_back begins.
          overlayRef.current?.setDragState("idle", e.timeStamp);
          // 2026-05-31 fun: ends drag + classify tap burst.
          overlayRef.current?.funPointerUp(e.timeStamp);
        }}
        onPointerCancel={(e) => {
          dragStartRef.current = null;
          overlayRef.current?.setDragState("idle", e.timeStamp);
          overlayRef.current?.funPointerUp(e.timeStamp);
        }}
        onClick={(e) => {
          const ts = e.timeStamp;
          const overlay = overlayRef.current;
          if (!overlay) return;
          const before = performance.now();
          overlay.pulseInteraction("click", ts);
          overlay.recordInteractionLatency(performance.now() - before);
        }}
      />
    </>
  );
});

