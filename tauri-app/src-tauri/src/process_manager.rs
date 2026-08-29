// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::process::{Child, Command, Stdio};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use ring::rand::SystemRandom;
use ring::signature::{Ed25519KeyPair, KeyPair};
use serde::{Deserialize, Serialize};
use tauri::{command, AppHandle, Emitter, Manager, State, WebviewWindow};

use crate::backend_launch::{self, BackendLaunch};
use crate::control_command_canonical::{
    credential_signed_payload, hex_encode, sha256_hex,
};

/// P3-S8: hard cap on how long we'll wait for the backend to print its
/// SHARED_SECRET line. Cold boot on a fresh machine (torch + CUDA init +
/// whisper load) has measured 30–45 s in P3-S4 profiling, so 90 s gives
/// generous headroom while still failing fast when the backend is
/// actually wedged (e.g. Python import error, missing DLL).
const SECRET_TIMEOUT_SECS: u64 = 90;

/// Backend FastAPI port. Default 8100; overridable via the
/// `DESKPET_BACKEND_PORT` env var so a second checkout / git worktree
/// can run its own dev backend without colliding on the port. The
/// Python side reads the SAME env var (see backend/config.py) so both
/// halves of the handshake always agree.
const DEFAULT_BACKEND_PORT: u16 = 8100;

fn backend_port() -> u16 {
    std::env::var("DESKPET_BACKEND_PORT")
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
        .unwrap_or(DEFAULT_BACKEND_PORT)
}

/// How many times the supervisor will respawn a crashed backend before it
/// gives up. Hitting this likely means a config bug, not a transient fault.
const MAX_RESTARTS_PER_WINDOW: u32 = 5;

/// Sliding window for `MAX_RESTARTS_PER_WINDOW`. If uptime between crashes
/// exceeds this, the restart counter resets — so a daily intermittent crash
/// doesn't eventually trigger "give up".
const RESTART_WINDOW_SECS: u64 = 60;

/// Cooldown between a crash and the next spawn attempt. Kept short so we
/// meet the V5 §1.1 "crash self-heal within 10s" bar even after counting
/// the time it takes the child to actually exit.
const RESTART_COOLDOWN_MS: u64 = 2_000;

fn reserve_restart_attempt(counter: &AtomicU32) -> bool {
    counter.fetch_add(1, Ordering::SeqCst) + 1 <= MAX_RESTARTS_PER_WINDOW
}

struct BackendControlSigner {
    backend_process_instance_id: String,
    pkcs8: Vec<u8>,
    public_key_hex: String,
}

impl BackendControlSigner {
    fn generate() -> Result<Self, String> {
        let rng = SystemRandom::new();
        let pkcs8 = Ed25519KeyPair::generate_pkcs8(&rng)
            .map_err(|_| "window_control_key_generation_failed")?;
        let pair = Ed25519KeyPair::from_pkcs8(pkcs8.as_ref())
            .map_err(|_| "window_control_key_parse_failed")?;
        Ok(Self {
            backend_process_instance_id: uuid::Uuid::new_v4().to_string(),
            pkcs8: pkcs8.as_ref().to_vec(),
            public_key_hex: hex_encode(pair.public_key().as_ref()),
        })
    }

    fn sign(&self, payload: &[u8]) -> Result<String, String> {
        let pair = Ed25519KeyPair::from_pkcs8(&self.pkcs8)
            .map_err(|_| "window_control_key_parse_failed")?;
        Ok(hex_encode(pair.sign(payload).as_ref()))
    }

    fn bootstrap_line(&self, documents_root: &Path) -> Result<String, String> {
        let canonical = documents_root
            .canonicalize()
            .map_err(|e| format!("host_documents_unavailable:{e}"))?;
        let metadata = canonical
            .metadata()
            .map_err(|e| format!("host_documents_unavailable:{e}"))?;
        if !metadata.is_dir() {
            return Err("host_documents_unavailable:not_directory".into());
        }
        #[cfg(unix)]
        let identity_material = {
            use std::os::unix::fs::MetadataExt;
            let os_name = if cfg!(target_os = "macos") { "darwin" } else { std::env::consts::OS };
            format!("v1\0{}\0{}\0{}", os_name, metadata.dev(), metadata.ino())
        };
        #[cfg(not(unix))]
        let identity_material = format!("v1\0{}\0{}", std::env::consts::OS, canonical.display());
        let documents_root_text = canonical.to_string_lossy().into_owned();
        let documents_identity = sha256_hex(identity_material.as_bytes());
        serde_json::to_string(&ControlBootstrap {
            schema: "host-bootstrap-v2",
            backend_process_instance_id: &self.backend_process_instance_id,
            public_key_hex: &self.public_key_hex,
            documents_root: &documents_root_text,
            documents_identity: &documents_identity,
        })
        .map(|value| format!("WINDOW_CONTROL_BOOTSTRAP={value}\n"))
        .map_err(|e| format!("window_control_bootstrap_encode_failed:{e}"))
    }
}

#[derive(Serialize)]
struct ControlBootstrap<'a> {
    schema: &'static str,
    backend_process_instance_id: &'a str,
    public_key_hex: &'a str,
    documents_root: &'a str,
    documents_identity: &'a str,
}

/// 按 PID 杀进程（kill_child 的兜底路径，见 BackendProcess::child_pid）。
fn kill_pid(pid: u32) {
    #[cfg(unix)]
    {
        // SIGKILL：backend 退出路径不需要优雅关闭（无未落盘状态），
        // 且此处已是应用退出的最后一步，不能再等。
        unsafe { libc::kill(pid as libc::pid_t, libc::SIGKILL) };
    }
    #[cfg(windows)]
    {
        // Job Object 已保证连带回收；这里只做兜底，忽略失败。
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/PID", &pid.to_string()])
            .output();
    }
}

pub struct BackendProcess {
    /// Serialize overlapping frontend start requests. React StrictMode can
    /// invoke the bootstrap effect twice before the first Python process has
    /// published its secret; without this gate both calls pass the empty-
    /// secret check and the second one reports our own port as occupied.
    start_gate: tokio::sync::Mutex<()>,
    child: Mutex<Option<Child>>,
    /// 2026-08-05 真机发现（B13 违反）：supervisor 线程为了 wait() 会把
    /// `child` handle take 走，于是 backend 活着的绝大多数时间里
    /// `child` 都是 None——kill_child() 因此形同虚设。Windows 靠
    /// Job Object 在进程退出时由内核连带回收，mac/Linux 的
    /// `job_object::assign_to_global` 是 no-op，于是退出应用后 backend
    /// 变成孤儿并继续占着 8100（正是 Job Object 当年要解决的问题）。
    /// 记录 PID 作为独立于 handle 的兜底杀进程依据。
    child_pid: Mutex<Option<u32>>,
    shared_secret: Mutex<Option<String>>,
    /// P3-S3: single source of truth for *how* to spawn the backend,
    /// replacing the old `python_path` + `backend_dir` string pair.
    /// Populated by `start_backend` from `backend_launch::resolve(app)`;
    /// the supervisor reads it back to respawn with identical args.
    launch: Mutex<Option<BackendLaunch>>,
    /// Set true by stop_backend / window-destroy. The supervisor reads
    /// this to distinguish "user asked to stop" from "process crashed".
    shutdown_requested: Arc<AtomicBool>,
    /// Incremented every time the supervisor respawns. Reset when the
    /// child has been up longer than RESTART_WINDOW_SECS.
    restart_count: Arc<AtomicU32>,
    /// P3-S8: last human-readable start-up error so the frontend can
    /// render a dialog even if the Err from start_backend already got
    /// swallowed by a React effect retry.
    startup_error: Mutex<Option<String>>,
    /// Private signing material for the current backend process. It is never
    /// returned through invoke, env, logs or the child bootstrap pipe.
    control_signer: Mutex<Option<BackendControlSigner>>,
}

impl BackendProcess {
    pub fn new() -> Self {
        Self {
            start_gate: tokio::sync::Mutex::new(()),
            child: Mutex::new(None),
            child_pid: Mutex::new(None),
            shared_secret: Mutex::new(None),
            launch: Mutex::new(None),
            shutdown_requested: Arc::new(AtomicBool::new(false)),
            restart_count: Arc::new(AtomicU32::new(0)),
            startup_error: Mutex::new(None),
            control_signer: Mutex::new(None),
        }
    }

    /// P3-S8: expose latest startup failure to the frontend.
    pub fn set_startup_error(&self, msg: Option<String>) {
        if let Ok(mut guard) = self.startup_error.lock() {
            *guard = msg;
        }
    }

    /// Fire-and-forget teardown used by the window-destroyed handler.
    /// Sets `shutdown_requested` so any in-flight supervisor bails, kills
    /// the child if we still have a direct handle, and clears
    /// `shared_secret` so the next `start_backend` invocation is treated
    /// as a fresh spawn rather than a no-op idempotent return.
    pub fn kill_child(&self) {
        self.shutdown_requested.store(true, Ordering::SeqCst);
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
        // PID 兜底：handle 通常已被 supervisor 的 wait() take 走（见
        // child_pid 字段注释），此时上面的分支什么都杀不掉。unix 上直接
        // 按 PID 发 SIGKILL；Windows 保留 Job Object 作为主要保障，这里
        // 的兜底同样无害。
        if let Ok(mut guard) = self.child_pid.lock() {
            if let Some(pid) = guard.take() {
                kill_pid(pid);
            }
        }
        if let Ok(mut guard) = self.shared_secret.lock() {
            *guard = None;
        }
        if let Ok(mut guard) = self.control_signer.lock() {
            *guard = None;
        }
    }

    /// P4-S21 #1: read-only access for the IPC bridge command.
    /// Returns None when backend isn't running (no SHARED_SECRET captured
    /// yet) — the caller surfaces that as "backend not running yet".
    pub fn shared_secret_clone(&self) -> Option<String> {
        self.shared_secret
            .lock()
            .ok()
            .and_then(|g| g.clone())
    }

    /// P4-S21 #1: backend listen port. Currently a const (8100) — pulled
    /// out into an accessor so the IPC bridge doesn't have to re-import
    /// the module-private constant.
    pub fn port(&self) -> u16 { backend_port() }
}

/// Spawn the Python backend and read its SHARED_SECRET announcement.
/// Shared logic between the initial `start_backend` command and the
/// supervisor's respawn path.
///
/// Provider credentials are resolved by the backend provider registry. The
/// launcher deliberately does not read the retired single-key credential
/// slot: doing so makes macOS show a Keychain authorization dialog on every
/// backend spawn. An explicitly supplied `DESKPET_CLOUD_API_KEY` remains
/// inherited by `Command` for development/backward-compatibility use.
/// P3-S8: quick precheck that 8100 is free. Returning Err early here
/// swaps the generic "Backend exited without printing SHARED_SECRET"
/// failure for the far more actionable "端口已被占用". We bind+drop on
/// 127.0.0.1:PORT; if another process has the port we get an OS error.
fn check_port_free(port: u16) -> Result<(), String> {
    match TcpListener::bind(("127.0.0.1", port)) {
        Ok(l) => {
            drop(l);
            Ok(())
        }
        Err(e) => Err(format!(
            "端口 {port} 已被其它程序占用（错误：{e}）。\n\
             请关闭其它 Simple Harness 实例或占用该端口的程序后重试。"
        )),
    }
}

fn spawn_once(
    launch: &BackendLaunch,
    documents_root: &Path,
) -> Result<(Child, String, BackendControlSigner), String> {
    // P3-S8: port precheck. Have to do it here (not only in start_backend)
    // because the supervisor respawn path also goes through spawn_once —
    // if a zombie backend survived a window close, we want the friendly
    // message on every retry, not just the initial attempt.
    check_port_free(backend_port())?;

    let mut cmd = match launch {
        BackendLaunch::Bundled { exe } => {
            let mut c = Command::new(exe);
            // cwd = exe's directory so the frozen PyInstaller onedir can
            // find its bundled _internal/ next to it.
            if let Some(parent) = exe.parent() {
                c.current_dir(parent);
            }
            c
        }
        BackendLaunch::Dev { python, backend_dir } => {
            let mut c = Command::new(python);
            c.arg("main.py").current_dir(backend_dir);
            c
        }
    };
    cmd.stdout(Stdio::piped())
        .stdin(Stdio::piped())
        .stderr(Stdio::inherit())
        // Windows 下 structlog 写入 piped stdout 时如果系统默认非 UTF-8
        // （GBK/CP936）会对中文日志抛 OSError[Errno 22]，backend 立刻崩在
        // lifespan 的 "preloading models..." 行。手动钉死 UTF-8 + 无缓冲，
        // 这样 SHARED_SECRET 也能被及时读到。
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        // Non-secret transport marker. Manual/pytest backend launches omit it
        // and therefore never block while trying to read stdin.
        .env("DESKPET_WINDOW_CONTROL_BOOTSTRAP", "stdin-v2");

    // 路径单一事实源：把 Rust 解析出的 userdata 钉给 backend，
    // 防 Rust/Python 双解析漂移（config.toml/state.db 落不同目录的根因）。
    if let Some(ud) = crate::paths::user_data_dir() {
        let _ = std::fs::create_dir_all(&ud);
        cmd.env("DESKPET_USER_DATA_DIR", ud.to_string_lossy().to_string());
    }

    // P4-S21 #8: suppress the orphan console window on Windows.
    //
    // PyInstaller spec keeps `console=True` so the bundled exe is a
    // console-subsystem program (needed because Rust still reads
    // SHARED_SECRET from stdout and stdout/stderr are piped to us).
    // When Windows spawns a console-subsystem child without an explicit
    // creation flag, it allocates a brand-new console window and shows
    // it — that's the "black cmd window next to the pet" users complain
    // about. The window is visually distinct from a normal app window,
    // and closing it kills the backend (very confusing UX).
    //
    // CREATE_NO_WINDOW (0x08000000) tells the OS "don't allocate a
    // console; the parent already has one or doesn't need it shown."
    // The piped stdout / stderr handles work the same way either path.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }

    let signer = BackendControlSigner::generate()?;
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to spawn backend: {e}"))?;

    // One-shot public bootstrap channel. The private PKCS#8 bytes remain only
    // in the Rust parent. The child reads this single line during Task 2
    // composition and EOF follows immediately when this handle is dropped.
    if let Some(mut stdin) = child.stdin.take() {
        if let Err(e) = stdin.write_all(signer.bootstrap_line(documents_root)?.as_bytes()) {
            let _ = child.kill();
            return Err(format!("window_control_bootstrap_write_failed:{e}"));
        }
    } else {
        let _ = child.kill();
        return Err("window_control_bootstrap_pipe_missing".into());
    }

    // 孤儿进程修复：把刚 spawn 的 backend 绑定到全局 Job Object
    // (KILL_ON_JOB_CLOSE)。这样无论 deskpet.exe 如何退出（正常 / panic /
    // 被 taskkill），OS 都会连带终止这个 python 子进程，避免它残留占用 8100
    // 端口。supervisor respawn 路径也走 spawn_once，所以每次重启的新 backend
    // 同样受保护。详见 crate::job_object 模块文档。
    crate::job_object::assign_to_global(&child);

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "No stdout on spawned backend".to_string())?;

    // P3-S8: wall-clock timeout on SHARED_SECRET. The previous version
    // read_line'd on the main thread, which blocks forever if Python hangs
    // mid-import (we've seen this with missing CUDA DLLs). Move the reader
    // to a worker thread and recv_timeout from a channel — the worker
    // keeps the pipe open (crucial on Windows, see the long comment below)
    // and we flip it into "drain silently" mode after the secret arrives.
    //
    // Why we must NOT drop the reader: on Windows, closing the pipe read
    // end makes Python's next stdout write (structlog "preloading
    // models..." etc.) raise OSError[Errno 22] and crash lifespan. The
    // worker thread therefore owns the reader for the child's full life.
    let reader = BufReader::new(stdout);
    let (tx, rx) = mpsc::channel::<Result<String, String>>();
    std::thread::spawn(move || {
        let mut reader = reader;
        let mut line_buf = String::new();
        let mut secret_sent = false;
        loop {
            line_buf.clear();
            match reader.read_line(&mut line_buf) {
                Ok(0) => {
                    if !secret_sent {
                        let _ = tx.send(Err(
                            "Backend exited without printing SHARED_SECRET".into()
                        ));
                    }
                    return;
                }
                Ok(_) => {
                    let trimmed = line_buf.trim_end_matches(['\r', '\n']);
                    if !secret_sent && trimmed.starts_with("SHARED_SECRET=") {
                        let s = trimmed.trim_start_matches("SHARED_SECRET=").to_string();
                        let _ = tx.send(Ok(s));
                        secret_sent = true;
                        // Keep draining silently from here on.
                    }
                }
                Err(e) => {
                    if !secret_sent {
                        let _ = tx.send(Err(format!(
                            "Failed to read backend stdout: {e}"
                        )));
                    }
                    return;
                }
            }
        }
    });

    let secret = match rx.recv_timeout(Duration::from_secs(SECRET_TIMEOUT_SECS)) {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => {
            let _ = child.kill();
            return Err(e);
        }
        Err(mpsc::RecvTimeoutError::Timeout) => {
            let _ = child.kill();
            return Err(format!(
                "Backend 启动超时（{SECRET_TIMEOUT_SECS}s 内未上报 SHARED_SECRET）。\n\
                 常见原因：CUDA / 模型加载失败。请打开日志目录排查。"
            ));
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            let _ = child.kill();
            return Err("Backend stdout pipe disconnected before SHARED_SECRET".into());
        }
    };

    Ok((child, secret, signer))
}

/// Spawn backend — runs in async context so it won't block the UI thread.
///
/// After the child process is up, installs a supervisor task that waits
/// on it. If the child exits without the user asking to stop, the
/// supervisor respawns with the same args after RESTART_COOLDOWN_MS.
/// The frontend is notified via the `backend-crashed` / `backend-restarted`
/// / `backend-dead` events so it can refresh its shared-secret cache.
#[command]
pub async fn start_backend(
    app: AppHandle,
    state: State<'_, BackendProcess>,
) -> Result<String, String> {
    // Single-flight the whole resolve/check/spawn/secret publication window.
    // A concurrent StrictMode/HMR caller waits here, then observes the secret
    // written by the first caller and returns it through the idempotent path.
    let _start_guard = state.start_gate.lock().await;

    eprintln!(
        "[backend_launch] start_backend acquired gate secret_present={}",
        state.shared_secret_clone().is_some()
    );

    // P3-S3: Rust now resolves the backend path itself. Frontend no
    // longer passes python_path / backend_dir — those hardcoded values
    // were dev-box-only. See `backend_launch::resolve` for priority.
    let launch = match backend_launch::resolve(&app) {
        Ok(l) => l,
        Err(e) => {
            let msg = backend_launch::format_user_message(&e);
            state.set_startup_error(Some(msg.clone()));
            return Err(msg);
        }
    };
    let documents_root = app.path().document_dir()
        .map_err(|e| format!("host_documents_unavailable:{e}"))?;

    // P3-S5: log which branch resolved so e2e smoke scripts can grep
    // the dev log to confirm Bundled vs Dev path was picked. Use stderr
    // because tauri dev redirects both streams to the same log file.
    match &launch {
        BackendLaunch::Bundled { exe } => {
            eprintln!("[backend_launch] Bundled exe={}", exe.display());
        }
        BackendLaunch::Dev { python, backend_dir } => {
            eprintln!(
                "[backend_launch] Dev python={} backend_dir={}",
                python.display(),
                backend_dir.display(),
            );
        }
    }

    // 幂等：以 shared_secret 作为"已有一个活着或正在重启中的 backend"的
    // 真实判据，而不是 state.child。原因：install_supervisor 在调 wait()
    // 之前会把 Child 从 Mutex 里 take() 出来持有在线程栈上，所以 backend
    // 活着的 99.99% 时间里 state.child 都是 None。只有 shared_secret 是
    // 在"启动 / respawn 成功"时 Some，"stop / kill_child / supervisor 放弃"
    // 时 None —— 这三条关闭路径都会显式清空它，语义一致。
    //
    // 效果：前端 F5 / StrictMode 重挂载里无脑 invoke start_backend 不会
    // 再次 spawn 出一个和现任 backend 抢 8100 端口的 Python。
    {
        let secret_guard = state.shared_secret.lock().map_err(|e| e.to_string())?;
        if let Some(existing) = secret_guard.as_ref() {
            eprintln!("[backend_launch] reusing running backend");
            return Ok(existing.clone());
        }
    }

    // Clone up-front so the supervisor owns its own copy.
    let launch_for_spawn = launch.clone();

    // Initial spawn runs on a blocking thread — the BufReader loop that
    // waits for SHARED_SECRET is blocking I/O.
    let documents_for_spawn = documents_root.clone();
    let spawn_result = tauri::async_runtime::spawn_blocking(move || {
        spawn_once(&launch_for_spawn, &documents_for_spawn)
    })
    .await
    .map_err(|e| format!("Task join error: {e}"))?;
    let (child, secret, signer) = match spawn_result {
        Ok(values) => values,
        Err(e) => {
            state.set_startup_error(Some(e.clone()));
            return Err(e);
        }
    };
    // Clear any stale error from a prior failed attempt.
    state.set_startup_error(None);

    // Record launch so the supervisor can respawn.
    *state.launch.lock().map_err(|e| e.to_string())? = Some(launch.clone());
    *state.shared_secret.lock().map_err(|e| e.to_string())? = Some(secret.clone());
    *state.control_signer.lock().map_err(|e| e.to_string())? = Some(signer);
    if let Ok(mut guard) = state.child_pid.lock() {
        *guard = Some(child.id());
    }
    *state.child.lock().map_err(|e| e.to_string())? = Some(child);
    state.shutdown_requested.store(false, Ordering::SeqCst);
    state.restart_count.store(0, Ordering::SeqCst);

    // Install the supervisor. It keeps its own Arc handles to the shared
    // atomics and the BackendProcess state (via AppHandle::state()).
    install_supervisor(app, launch, documents_root);

    Ok(secret)
}

/// Background loop: wait for the current child to exit; if the user
/// didn't ask for a shutdown, respawn. Emits lifecycle events so the
/// frontend can re-fetch the secret and reconnect its WebSockets.
fn install_supervisor(app: AppHandle, launch: BackendLaunch, documents_root: PathBuf) {
    std::thread::spawn(move || {
        loop {
            // Wait for the current child to exit. We have to release the
            // mutex while waiting so stop_backend can still seize the lock.
            let exit_status = {
                let state = app.state::<BackendProcess>();
                let mut guard = match state.child.lock() {
                    Ok(g) => g,
                    Err(_) => return,
                };
                let Some(mut child) = guard.take() else {
                    // Someone already removed the child (stop_backend or
                    // window-destroy). Supervisor's job is done.
                    return;
                };
                drop(guard);
                child.wait()
            };

            let state = app.state::<BackendProcess>();
            if state.shutdown_requested.load(Ordering::SeqCst) {
                // Clean shutdown — don't respawn.
                return;
            }

            // The child died on its own. Tell the frontend so it can
            // drop its WS connections; the restart path will give it a
            // new secret to reconnect with.
            let reason = match &exit_status {
                Ok(status) => format!("exit:{status}"),
                Err(e) => format!("wait_err:{e}"),
            };
            let _ = app.emit("backend-crashed", reason);

            // A transient pre-spawn failure (most notably the backend port
            // still being released) consumes the same bounded restart budget
            // as a child crash, but must not permanently stop the supervisor
            // after the first attempt.
            loop {
                if !reserve_restart_attempt(&state.restart_count) {
                    if let Ok(mut guard) = state.shared_secret.lock() {
                        *guard = None;
                    }
                    if let Ok(mut guard) = state.control_signer.lock() {
                        *guard = None;
                    }
                    let _ = app.emit("backend-dead", "restart budget exhausted");
                    return;
                }

                std::thread::sleep(Duration::from_millis(RESTART_COOLDOWN_MS));

                let started = Instant::now();
                match spawn_once(&launch, &documents_root) {
                    Ok((new_child, new_secret, new_signer)) => {
                        if let Ok(mut guard) = state.shared_secret.lock() {
                            *guard = Some(new_secret.clone());
                        }
                        if let Ok(mut guard) = state.child_pid.lock() {
                            *guard = Some(new_child.id());
                        }
                        if let Ok(mut guard) = state.child.lock() {
                            *guard = Some(new_child);
                        }
                        if let Ok(mut guard) = state.control_signer.lock() {
                            *guard = Some(new_signer);
                        }
                        state.set_startup_error(None);
                        let _ = app.emit("backend-restarted", new_secret);

                        if started.elapsed() > Duration::from_secs(RESTART_WINDOW_SECS) {
                            state.restart_count.store(0, Ordering::SeqCst);
                        }
                        break;
                    }
                    Err(e) => {
                        if let Ok(mut guard) = state.shared_secret.lock() {
                            *guard = None;
                        }
                        if let Ok(mut guard) = state.control_signer.lock() {
                            *guard = None;
                        }
                        state.set_startup_error(Some(e.clone()));
                        let _ = app.emit(
                            "backend-crashed",
                            format!("respawn failed: {e}; retrying"),
                        );
                    }
                }
            }
        }
    });
}

#[command]
pub fn stop_backend(state: State<'_, BackendProcess>) -> Result<(), String> {
    state.shutdown_requested.store(true, Ordering::SeqCst);
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = child_guard.take() {
        child.kill().map_err(|e| e.to_string())?;
    }
    // 让幂等检查恢复"无 backend"判断，好让之后的 start_backend 能真正
    // spawn，而不是返回这条已经被杀死的 stale secret。
    *state.shared_secret.lock().map_err(|e| e.to_string())? = None;
    *state.control_signer.lock().map_err(|e| e.to_string())? = None;
    Ok(())
}

#[command]
pub fn is_backend_running(state: State<'_, BackendProcess>) -> Result<bool, String> {
    let mut child_guard = state.child.lock().map_err(|e| e.to_string())?;
    match child_guard.as_mut() {
        Some(child) => match child.try_wait() {
            Ok(Some(_)) => {
                *child_guard = None;
                Ok(false)
            }
            Ok(None) => Ok(true),
            Err(e) => Err(e.to_string()),
        },
        None => Ok(false),
    }
}

#[command]
pub fn get_shared_secret(state: State<'_, BackendProcess>) -> Result<String, String> {
    state
        .shared_secret
        .lock()
        .map_err(|e| e.to_string())?
        .clone()
        .ok_or("No secret available (backend not started?)".into())
}

/// P3-S8 — returns the last classified startup error (human-readable
/// Chinese) or None. Frontend polls this after start_backend rejects
/// and also on window focus so crashes surfaced via `backend-dead` can
/// be re-displayed.
#[command]
pub fn get_startup_error(state: State<'_, BackendProcess>) -> Result<Option<String>, String> {
    Ok(state.startup_error.lock().map_err(|e| e.to_string())?.clone())
}

/// P3-S8 — clear the recorded startup error once the dialog is dismissed
/// so a successful retry doesn't re-show it.
#[command]
pub fn clear_startup_error(state: State<'_, BackendProcess>) -> Result<(), String> {
    *state.startup_error.lock().map_err(|e| e.to_string())? = None;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ring::signature::{UnparsedPublicKey, ED25519};

    #[test]
    fn port_precheck_reports_bound_port() {
        // Bind an ephemeral port, then assert check_port_free on that
        // port fails with a user-readable Chinese message.
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let err = check_port_free(port).expect_err("expected busy port to fail");
        assert!(err.contains(&port.to_string()), "error missing port: {err}");
        assert!(err.contains("占用"), "error not chinese: {err}");
    }

    #[test]
    fn port_precheck_passes_on_free_port() {
        // Pick a random high port, bind+drop to learn it's free, then
        // re-check. Race-y in theory; in practice fine for unit tests.
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        drop(listener);
        assert!(check_port_free(port).is_ok());
    }

    #[test]
    fn startup_error_round_trip() {
        let bp = BackendProcess::new();
        assert!(bp.startup_error.lock().unwrap().is_none());
        bp.set_startup_error(Some("boom".into()));
        assert_eq!(bp.startup_error.lock().unwrap().as_deref(), Some("boom"));
        bp.set_startup_error(None);
        assert!(bp.startup_error.lock().unwrap().is_none());
    }

    #[test]
    fn restart_attempt_budget_counts_spawn_failures_and_is_bounded() {
        let counter = AtomicU32::new(0);
        for expected in 1..=MAX_RESTARTS_PER_WINDOW {
            assert!(reserve_restart_attempt(&counter));
            assert_eq!(counter.load(Ordering::SeqCst), expected);
        }
        assert!(!reserve_restart_attempt(&counter));
        assert_eq!(
            counter.load(Ordering::SeqCst),
            MAX_RESTARTS_PER_WINDOW + 1
        );
    }

    #[test]
    fn backend_spawn_does_not_read_legacy_single_key_slot() {
        let source = include_str!("process_manager.rs");
        // Build the needle at runtime so the canary does not match itself.
        let legacy_reader = ["crate::secrets::get_", "cloud_api_key()"].concat();
        assert!(
            !source.contains(&legacy_reader),
            "backend spawn must leave provider credential resolution to the backend registry"
        );
    }

    #[test]
    fn backend_restart_rotates_window_signing_identity() {
        let first = BackendControlSigner::generate().unwrap();
        let second = BackendControlSigner::generate().unwrap();
        assert_ne!(
            first.backend_process_instance_id,
            second.backend_process_instance_id
        );
        assert_ne!(first.public_key_hex, second.public_key_hex);
        let payload = b"credential-payload";
        let signature = crate::control_command_canonical::hex_decode(
            &first.sign(payload).unwrap(),
        )
        .unwrap();
        let public = crate::control_command_canonical::hex_decode(
            &first.public_key_hex,
        )
        .unwrap();
        UnparsedPublicKey::new(&ED25519, public)
            .verify(payload, &signature)
            .unwrap();
        let bootstrap = first.bootstrap_line(Path::new("/tmp")).unwrap();
        assert!(bootstrap.contains(&first.public_key_hex));
        assert!(!bootstrap.contains(&hex_encode(&first.pkcs8)));
    }

    #[test]
    fn documents_resolver_is_embedded_in_host_bootstrap_v2() {
        let signer = BackendControlSigner::generate().unwrap();
        let line = signer.bootstrap_line(Path::new("/tmp")).unwrap();
        let payload = line.strip_prefix("WINDOW_CONTROL_BOOTSTRAP=").unwrap();
        let value: serde_json::Value = serde_json::from_str(payload).unwrap();
        assert_eq!(value["schema"], "host-bootstrap-v2");
        assert_eq!(value["documents_root"], Path::new("/tmp").canonicalize().unwrap().to_string_lossy().as_ref());
        assert_eq!(value["documents_identity"].as_str().unwrap().len(), 64);
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WindowControlScope {
    IdentityBind,
    CompanionAction,
}

impl WindowControlScope {
    fn as_str(&self) -> &'static str {
        match self {
            Self::IdentityBind => "identity_bind",
            Self::CompanionAction => "companion_action",
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WindowControlCredential {
    backend_process_instance_id: String,
    connection_id: String,
    control_epoch: String,
    window_label: String,
    scope: String,
    challenge_hash: String,
    request_seq: String,
    command_kind: String,
    canonical_request_hash: String,
    nonce: String,
    issued_at: String,
    expires_at: String,
    signature_hex: String,
}

#[command]
#[allow(clippy::too_many_arguments)]
pub fn get_window_control_credential(
    window: WebviewWindow,
    state: State<'_, BackendProcess>,
    connection_id: String,
    control_epoch: String,
    challenge: String,
    request_seq: String,
    command_kind: String,
    request_hash: String,
    requested_scope: WindowControlScope,
) -> Result<WindowControlCredential, String> {
    let label = window.label().to_owned();
    let scope = requested_scope.as_str();
    crate::webview_permissions::authorize_window_control_scope(&label, scope)?;
    crate::control_command_canonical::parse_canonical_u64(
        &control_epoch,
        "control_epoch",
    )?;
    crate::control_command_canonical::parse_canonical_u64(
        &request_seq,
        "request_seq",
    )?;
    let decoded_hash = crate::control_command_canonical::hex_decode(&request_hash)?;
    if decoded_hash.len() != 32 || request_hash.to_ascii_lowercase() != request_hash {
        return Err("canonical_request_hash:not_lower_hex_sha256".into());
    }
    if connection_id.trim().is_empty() || command_kind.trim().is_empty() {
        return Err("window_control_request_missing_identity".into());
    }

    let issued_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "system_clock_before_epoch")?
        .as_secs();
    let expires_at = issued_at + 30;
    let challenge_hash = sha256_hex(challenge.as_bytes());
    let nonce = uuid::Uuid::new_v4().to_string();
    let signer = state.control_signer.lock().map_err(|e| e.to_string())?;
    let signer = signer
        .as_ref()
        .ok_or_else(|| "window_control_signer_not_ready".to_string())?;
    let payload = credential_signed_payload(
        &signer.backend_process_instance_id,
        &connection_id,
        &control_epoch,
        &label,
        scope,
        &challenge_hash,
        &request_seq,
        &command_kind,
        &request_hash,
        &nonce,
        &issued_at.to_string(),
        &expires_at.to_string(),
    )?;
    let signature_hex = signer.sign(&payload)?;
    Ok(WindowControlCredential {
        backend_process_instance_id: signer.backend_process_instance_id.clone(),
        connection_id,
        control_epoch,
        window_label: label,
        scope: scope.to_owned(),
        challenge_hash,
        request_seq,
        command_kind,
        canonical_request_hash: request_hash,
        nonce,
        issued_at: issued_at.to_string(),
        expires_at: expires_at.to_string(),
        signature_hex,
    })
}
