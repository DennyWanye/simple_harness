import { text, type Json } from "../primary/sessionClient";
import "./ChatMissionNotices.styles.css";
export function ChatMissionNotices({
  notices,
  acknowledge,
}: {
  notices: Json[];
  acknowledge: (eventId: string) => void;
}) {
  return (
    <div aria-label="后台任务通知" className="sh-ChatMissionNotices-1">
      {notices
        .filter((notice) => notice.acked !== true)
        .map((notice) => (
          <div key={text(notice.event_id)} className="sh-ChatMissionNotices-2">
            <p>
              后台任务 {text(notice.mission_id)}：{text(notice.status)}
            </p>
            <button onClick={() => acknowledge(text(notice.event_id))}>已收到</button>
          </div>
        ))}
    </div>
  );
}
