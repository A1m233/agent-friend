import { MessageSquareWarning, type LucideProps } from "lucide-react";

/**
 * 反馈入口的统一语义图标。
 *
 * 各入口保留自己的按钮形态与文案；如果未来调整反馈图标，只需修改这里。
 */
export function FeedbackIcon(props: LucideProps) {
  return <MessageSquareWarning {...props} />;
}
