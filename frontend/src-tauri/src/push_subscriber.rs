// 015 · push channel SSE subscriber（Rust 侧）。
//
// 持有 `agent_bridge` `/push/subscribe?kinds=agent_turn` 的长 SSE 连接，把解码后的
// envelope 通过 `tauri::Emitter::emit_to("bubble", "agent://push", env)` 透传到 bubble
// webview；user_turn 在服务端按 `kinds` 参数已过滤掉，本期 pet 窗只接 agent_turn。
//
// 详见 015 design §3 / §4。
//
// 本期范围（明确不做）：
// - 断线自动重连（Tier 1 `bridge 连接连续性` 留下个需求）
// - HTTP 4xx/5xx 重试（dev 期能立刻发现；脚本会保证 bridge 先于 frontend 起）
// - 背压（envelope 频率分钟级，无需处理）

use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

/// 与 `agent_bridge/src/agent_bridge/push/protocol.py` 的 `PushEnvelope` schema 对齐。
///
/// - `kind`：`"user_turn"` / `"agent_turn"` / `"heartbeat"`；订阅端 `kinds=agent_turn`
///   下绝大多数为 `agent_turn`，heartbeat 在本模块内部丢弃、不上抛 webview。
/// - `events`：序列化后的 `ConversationEvent` 列表，原样透传给 TS 侧 policy 消费。
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PushEnvelope {
    pub kind: String,
    pub session_id: String,
    pub seq: u64,
    pub source_kind: Option<String>,
    pub events: Vec<serde_json::Value>,
}

/// 启动 push channel 长 SSE 订阅；放在独立 tokio task 跑，app 退出时随 runtime drop。
///
/// 失败（HTTP / 网络 / 解码）即终结、log warn，不重连——见模块顶 docstring。
pub fn spawn_push_subscriber(app: &AppHandle, bridge_base_url: String) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = run_loop(handle, bridge_base_url).await {
            log::warn!("push_subscriber 终止: {e}");
        }
    });
}

async fn run_loop(
    app: AppHandle,
    base_url: String,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let url = format!("{base_url}/push/subscribe?kinds=agent_turn");
    log::info!("push_subscriber 启动: {url}");

    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .header("Accept", "text/event-stream")
        .send()
        .await?;
    let status = resp.status();
    if !status.is_success() {
        return Err(format!("push subscribe failed: HTTP {status}").into());
    }

    let mut stream = resp.bytes_stream();
    let mut buf: Vec<u8> = Vec::new();

    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        buf.extend_from_slice(&chunk);
        while let Some((idx, sep_len)) = find_frame_sep(&buf) {
            let drained: Vec<u8> = buf.drain(..idx + sep_len).collect();
            let frame = &drained[..idx];
            match parse_envelope_frame(frame) {
                Some(env) if env.kind == "heartbeat" => {
                    log::trace!("push heartbeat seq={}", env.seq);
                }
                Some(env) => {
                    log::debug!(
                        "push envelope: kind={} seq={} source_kind={:?} events={}",
                        env.kind,
                        env.seq,
                        env.source_kind,
                        env.events.len(),
                    );
                    if let Err(e) = app.emit_to("bubble", "agent://push", env.clone()) {
                        log::warn!("emit_to(bubble) failed: {e}");
                    }
                    // 17b · 桌宠状态机 + lip-sync 也消费 envelope（18 design §3.1 / §4.1）
                    if let Err(e) = app.emit_to("pet", "agent://push", env) {
                        log::warn!("emit_to(pet) failed: {e}");
                    }
                }
                None => {
                    // 非 data 帧（如服务器仅发 ":heartbeat" comment）或解码失败 —— 静默跳过下一帧
                    log::trace!("skip non-data frame: {} bytes", frame.len());
                }
            }
        }
    }
    // stream 自然结束（服务端关闭）；正常退出 task
    log::info!("push_subscriber stream 结束");
    Ok(())
}

/// SSE 按空行（`\n\n`，兼容 `\r\n\r\n`）分帧，返回 `(分隔符起始索引, 分隔符长度)`。
///
/// 同时存在时选靠前的（与 frontend/src/services/stream.ts findFrameSep 行为对齐）。
fn find_frame_sep(buf: &[u8]) -> Option<(usize, usize)> {
    let crlf = find_subsequence(buf, b"\r\n\r\n");
    let lf = find_subsequence(buf, b"\n\n");
    match (crlf, lf) {
        (Some(c), Some(l)) if c < l => Some((c, 4)),
        (Some(c), None) => Some((c, 4)),
        (_, Some(l)) => Some((l, 2)),
        _ => None,
    }
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// 取一帧里所有 `data:` 行拼成 JSON 解析；非数据帧 / 解码失败返回 None。
///
/// SSE 帧形如：
/// ```text
/// event: push
/// data: {"kind":"agent_turn", ...}
/// ```
fn parse_envelope_frame(frame: &[u8]) -> Option<PushEnvelope> {
    let text = std::str::from_utf8(frame).ok()?;
    let json: String = text
        .lines()
        .filter_map(|line| line.strip_prefix("data:"))
        .map(|s| s.strip_prefix(' ').unwrap_or(s))
        .collect::<Vec<_>>()
        .join("\n");
    if json.is_empty() {
        return None;
    }
    serde_json::from_str(&json).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    // ----- find_frame_sep -----

    #[test]
    fn frame_sep_lf_only() {
        let buf = b"event: push\ndata: {}\n\nnext";
        let (idx, len) = find_frame_sep(buf).unwrap();
        assert_eq!(len, 2);
        assert_eq!(&buf[idx..idx + len], b"\n\n");
    }

    #[test]
    fn frame_sep_crlf_only() {
        let buf = b"event: push\r\ndata: {}\r\n\r\nnext";
        let (idx, len) = find_frame_sep(buf).unwrap();
        assert_eq!(len, 4);
        assert_eq!(&buf[idx..idx + len], b"\r\n\r\n");
    }

    #[test]
    fn frame_sep_both_picks_earlier() {
        // 先 \r\n\r\n 再 \n\n —— 取靠前的 CRLF
        let buf = b"a\r\n\r\nb\n\nc";
        let (idx, len) = find_frame_sep(buf).unwrap();
        assert_eq!(idx, 1);
        assert_eq!(len, 4);
    }

    #[test]
    fn frame_sep_neither() {
        assert!(find_frame_sep(b"event: push\ndata: {}").is_none());
        assert!(find_frame_sep(b"").is_none());
    }

    // ----- parse_envelope_frame -----

    #[test]
    fn parse_envelope_basic() {
        let frame = br#"event: push
data: {"kind":"agent_turn","session_id":"s1","seq":3,"source_kind":"cron:bedtime","events":[]}"#;
        let env = parse_envelope_frame(frame).unwrap();
        assert_eq!(env.kind, "agent_turn");
        assert_eq!(env.session_id, "s1");
        assert_eq!(env.seq, 3);
        assert_eq!(env.source_kind, Some("cron:bedtime".to_string()));
        assert!(env.events.is_empty());
    }

    #[test]
    fn parse_envelope_heartbeat_with_null_source_kind() {
        let frame = br#"event: push
data: {"kind":"heartbeat","session_id":"","seq":0,"source_kind":null,"events":[]}"#;
        let env = parse_envelope_frame(frame).unwrap();
        assert_eq!(env.kind, "heartbeat");
        assert!(env.source_kind.is_none());
    }

    #[test]
    fn parse_envelope_multiline_data_concat() {
        // SSE 协议允许同帧内多条 data: 行；parse_envelope_frame 应按 \n 拼接成完整 JSON
        let frame = b"data: {\"kind\":\"agent_turn\",\"session_id\":\"s1\",\ndata: \"seq\":7,\"source_kind\":null,\"events\":[]}";
        let env = parse_envelope_frame(frame).unwrap();
        assert_eq!(env.kind, "agent_turn");
        assert_eq!(env.seq, 7);
    }

    #[test]
    fn parse_envelope_with_event_header_line() {
        // 014 实际格式："event: push\ndata: {...}"——event: 行被 filter_map 自然丢掉
        let frame = b"event: push\ndata: {\"kind\":\"agent_turn\",\"session_id\":\"s2\",\"seq\":99,\"source_kind\":null,\"events\":[]}";
        let env = parse_envelope_frame(frame).unwrap();
        assert_eq!(env.session_id, "s2");
        assert_eq!(env.seq, 99);
    }

    #[test]
    fn parse_envelope_empty_frame_returns_none() {
        assert!(parse_envelope_frame(b"").is_none());
        assert!(parse_envelope_frame(b"event: push\n").is_none());  // 只有 event:、无 data:
        assert!(parse_envelope_frame(b": comment-only\n").is_none());  // SSE comment
    }

    #[test]
    fn parse_envelope_invalid_json_returns_none() {
        let frame = b"data: not json";
        assert!(parse_envelope_frame(frame).is_none());
    }

    #[test]
    fn parse_envelope_events_passthrough() {
        // events 字段含任意 dict —— 透传给 TS 侧；用普通 raw string + as_bytes() 兼容 UTF-8 内容
        let frame_str = r#"data: {"kind":"agent_turn","session_id":"s1","seq":1,"source_kind":"cron:bedtime","events":[{"type":"assistant_message","payload":{"content":"晚安"}}]}"#;
        let env = parse_envelope_frame(frame_str.as_bytes()).unwrap();
        assert_eq!(env.events.len(), 1);
        let ev = &env.events[0];
        assert_eq!(ev.get("type").and_then(|v| v.as_str()), Some("assistant_message"));
        // UTF-8 内容也能 round-trip
        let content = ev.get("payload").and_then(|p| p.get("content")).and_then(|v| v.as_str());
        assert_eq!(content, Some("晚安"));
    }

    // ----- PushEnvelope serde round-trip -----

    #[test]
    fn envelope_round_trip() {
        let original = PushEnvelope {
            kind: "agent_turn".into(),
            session_id: "s1".into(),
            seq: 42,
            source_kind: Some("idle_reflection".into()),
            events: vec![serde_json::json!({"type": "memory_observation"})],
        };
        let json = serde_json::to_string(&original).unwrap();
        let parsed: PushEnvelope = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.kind, original.kind);
        assert_eq!(parsed.session_id, original.session_id);
        assert_eq!(parsed.seq, original.seq);
        assert_eq!(parsed.source_kind, original.source_kind);
        assert_eq!(parsed.events.len(), 1);
    }
}
