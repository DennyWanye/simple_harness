// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import {
  AnimationOverlay,
  type InteractionKind,
  type MotionTag,
} from "../pet-anim";
import { get_calibrated_motion_pools } from "../pet-state/PetStateMachine";
import {
  createPetEngine,
  resolveBackendFromEnv,
  type PetEngine,
} from "../pet-engine";
import {
  drawSpriteCharacter,
  drawProceduralCharacter,
  loadSpriteImage,
  type CharacterFrame,
} from "./petCharacter";

interface Live2DCanvasProps {
  modelPath: string;
  onFpsUpdate?: (fps: number) => void;
  mouthOpenY?: number; // 0.0 ~ 1.0, drives ParamMouthOpenY for lip sync
  /** 2026-05-17: pet 渲染区逻辑宽（CSS px）。桌宠窗加了左侧常驻消息
   * 面板后，窗口变宽但 Hiyori 必须只居中在右侧 pet 列里 —— 否则会
   * 居中到整窗(含面板)被面板挡住/被裁切。App 传 innerWidth - 面板宽。
   * 不传 → 退回整窗宽(旧行为，零回归)。高度仍取 innerHeight。 */
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
export interface Live2DHandle {
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
  const left = innerWidth - petWidth + petWidth * 0.25;
  const width = Math.max(40, petWidth * 0.5 * modelScaleFactor);
  const top = innerHeight * 0.2;
  const height = Math.max(40, innerHeight * 0.6 * modelScaleFactor);
  return {
    left,
    top,
    width,
    height,
    face_center_x: left + width / 2,
    face_center_y: top + height * 0.3,
    face_radius_css: Math.min(width, height) * 0.5,
  };
}

/**
 * Live2D character rendered via PixiJS WebGL → <img> tag.
 * Falls back to Canvas2D animated cat if Live2D fails.
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
export const Live2DCanvas = forwardRef<Live2DHandle, Live2DCanvasProps>(function Live2DCanvas(
  { modelPath, onFpsUpdate, mouthOpenY = 0, petWidth },
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
    w: petWidth ?? window.innerWidth,
    h: window.innerHeight,
  }));
  // Use ref for mode to avoid re-render killing the render loop. We don't
  // expose this as React state because the render loop captures it once and
  // toggling it would otherwise tear down + re-init the whole pipeline.
  const modeRef = useRef<"loading" | "canvas2d">("loading");
  const cleanupRef = useRef<(() => void) | null>(null);
  const mouthRef = useRef(mouthOpenY);
  mouthRef.current = mouthOpenY;
  // S5: modelRef removed together with the pixi-live2d-display path.
  // The engine is now the single source for the runtime model.
  // v3: AnimationOverlay instance — per Live2DCanvas mount. dispose()
  // called from cleanupRef so HMR / StrictMode double-mount can't leak.
  const overlayRef = useRef<AnimationOverlay | null>(null);
  // S5 (Live2D removal 2026-05-29): PetEngine abstraction. Only the
  // 'sprite' backend ships today; resolveBackendFromEnv warns-and-falls
  // back for any other VITE_PET_ENGINE value. Future slices may add a
  // 'mesh' backend; pet-anim never sees the swap.
  const engineRef = useRef<PetEngine | null>(null);
  const backendRef = useRef(resolveBackendFromEnv(
    (import.meta as any).env?.VITE_PET_ENGINE as string | undefined,
  ));
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
        // Reserved for future models with multiple Idle groups.
        // Hiyori has only one ("Idle"), so no-op.
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
    }),
    [],
  );

  // Track viewport. Also keep face frame current.
  useEffect(() => {
    const apply = (): void => {
      const w = petWidth ?? window.innerWidth;
      const h = window.innerHeight;
      setSize({ w, h });
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
    console.warn("[Pet] viewport:", window.innerWidth, "x", window.innerHeight, "dpr:", window.devicePixelRatio);
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined" && containerRef.current) {
      ro = new ResizeObserver(throttled);
      ro.observe(containerRef.current);
    }
    return () => {
      window.removeEventListener("resize", throttled);
      if (timeout) window.clearTimeout(timeout);
      ro?.disconnect();
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
      // S5: bench uses the engine's CoreModelLike (post-Live2D removal).
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

  // Main init — runs once
  useEffect(() => {
    if (modeRef.current !== "loading") return;

    let destroyed = false;
    let rafId = 0;

    async function init() {
      // S5 (Live2D removal 2026-05-29): the only shipping backend is
      // 'sprite'. Wires the SpritePetEngine into pet-anim and hands
      // rendering off to startCanvas2D — a 100%-original Canvas2D
      // character with zero copyright risk. The legacy PixiJS +
      // Live2DModel path was deleted together with the cubismcore /
      // pixi-live2d-display deps and the Hiyori assets.
      console.warn("[pet-engine] sprite backend → Canvas2D character");
      const engine = createPetEngine(backendRef.current);
      engineRef.current = engine;
      overlayRef.current?.setMotionPlayer((group, idx) => {
        engine.playMotion(group, idx);
      });
      // Keep face-frame in sync so hit-zone DOM + overlay gaze share
      // the same source of truth (PRD §6.0 v3).
      const ff = faceFrameRef.current;
      overlayRef.current?.setFaceCenter(ff.face_center_x, ff.face_center_y, ff.face_radius_css);
      modeRef.current = "canvas2d";
      startCanvas2D();
    }

    // Canvas2D fallback — unchanged from pre-v3, just a fallback character.
    function startCanvas2D() {
      console.warn("[Canvas2D] starting fallback");
      const width = size.w;
      const height = size.h;
      const canvas = document.createElement("canvas");
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const rawCtx = canvas.getContext("2d");
      if (!rawCtx) return;
      const ctx: CanvasRenderingContext2D = rawCtx;

      let frameCount = 0;
      let lastFpsTime = performance.now();
      let eyeBlinkTimer = 0;
      let isBlinking = false;
      // S6: try to load an external portrait sprite. Until it resolves
      // (or if it's absent / 404s) we draw the procedural chibi
      // placeholder. Drop a transparent-bg PNG at
      // public/assets/pet/character.png and it gets picked up
      // automatically on next load — that's the "real artwork" path.
      let spriteImg: HTMLImageElement | null = null;
      void loadSpriteImage("/assets/pet/character.png").then((img) => {
        spriteImg = img;
        if (img) console.warn("[pet] external sprite loaded:", img.naturalWidth, "x", img.naturalHeight);
        else console.warn("[pet] no external sprite — using procedural chibi placeholder");
      });

      function draw(ts: number) {
        if (destroyed) return;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);

        frameCount++;
        const now = performance.now();
        if (now - lastFpsTime >= 1000) {
          onFpsUpdate?.(Math.round((frameCount * 1000) / (now - lastFpsTime)));
          frameCount = 0;
          lastFpsTime = now;
        }

        // Self-driven blink scheduler (~every 3.2s, ~140ms closed).
        eyeBlinkTimer += 16;
        if (!isBlinking && eyeBlinkTimer > 3200) { isBlinking = true; eyeBlinkTimer = 0; }
        if (isBlinking && eyeBlinkTimer > 140) { isBlinking = false; eyeBlinkTimer = 0; }

        const frame: CharacterFrame = {
          w: width,
          h: height,
          t: ts,
          mouthOpen: mouthRef.current,
          blink: isBlinking ? 1 : 0,
        };
        if (spriteImg) drawSpriteCharacter(ctx, spriteImg, frame);
        else drawProceduralCharacter(ctx, frame);

        canvas.toBlob(
          (blob) => {
            if (!blob || !imgRef.current || destroyed) return;
            if (c2dBlobUrl) URL.revokeObjectURL(c2dBlobUrl);
            c2dBlobUrl = URL.createObjectURL(blob);
            imgRef.current.src = c2dBlobUrl;
          },
          "image/webp",
          0.8,
        );
        rafId = requestAnimationFrame(draw);
      }
      let c2dBlobUrl: string | null = null;
      rafId = requestAnimationFrame(draw);
    }

    init();

    cleanupRef.current = () => {
      destroyed = true;
      cancelAnimationFrame(rafId);
      // S5: release the PetEngine. No more PixiJS canvas to clean up —
      // the Canvas2D character lives off the imgRef Blob URL pipeline.
      engineRef.current?.destroy();
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
          right: 0,
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
        }}
        onPointerMove={(e) => {
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
        }}
        onPointerCancel={(e) => {
          dragStartRef.current = null;
          overlayRef.current?.setDragState("idle", e.timeStamp);
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

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
  ctx.fill();
}
